"""LLM rationale layer — single-call Anthropic SDK with prompt caching.

Architecture choice (defended in README): single completion call per top-N
lead, not a Claude Agent SDK tool-using loop. Trade-offs we accept:

+ 1 round-trip / lead (~500ms), ~10x cheaper than agentic loop at 50 leads
+ System prompt cached via cache_control=ephemeral → ~80% input-token savings
  on the second call onward; full top-N batch costs ~5x a single uncached call
+ Factuality verified deterministically by regex/substring against the source
  feature payload (no separate verify-loop tool needed at this dataset size)
+ Stateless: easy to swap models, mock, eval, or replace with Agent SDK later
  without changing the (features, components) -> str interface

Escalation path documented for the README: if ablation shows >5% hallucinated
citations on the real CSVs, swap this module for an Agent-SDK implementation
exposing bounded retrieval tools (get_lead_attributes(lead_id, kind),
get_competitors_for_hs(code), get_personnel(lead_id), verify_claim(text)).
The public function signatures stay identical so callers don't change.

Fallback policy: when ANTHROPIC_API_KEY is absent, the SDK import fails, a
per-call LLM error happens, or factuality check rejects the output, we
synthesize a deterministic rationale from the features. The demo runs without
keys; downstream output is always grounded.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Literal

from .features import LeadFeatures
from .score import top_driver
from .trace import FactualityCheck, LLMCallTrace, RationaleTrace, emit

log = logging.getLogger("uvicorn.error")

# Model — Haiku 4.5 is fast and cheap; the output is 1-2 sentences of
# structured citation, well within Haiku capability. Override via env for
# regression sweeps against Sonnet.
MODEL_DEFAULT = os.environ.get("RATIONALE_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = 220
TEMPERATURE = 0.3
# Free-tier Anthropic limit is 50 requests/minute. A batch of 50 with
# CONCURRENCY=10 finishes in ~5s, well above the per-minute ceiling, and we
# observed 429s on the last snapshot. Drop to 4 in-flight so steady-state
# throughput stays under the limit (~45/min including retries below).
CONCURRENCY = 4
LLM_RETRY_ATTEMPTS = 2   # 1 retry after first 429/transient error
LLM_RETRY_BACKOFF_S = 1.5

HS_CODE_RE = re.compile(r"\b(\d{6})\b")

# Matches a company-shaped phrase in the rationale: capitalized run of 1-5
# tokens optionally followed by a corporate suffix. Used to catch
# hallucinated lead-name substitutions (e.g. Pearhead → "Pearson Inc").
_CORP_SUFFIX = r"Inc\.?|Incorporated|LLC|L\.L\.C\.|Ltd\.?|Limited|Corp\.?|Corporation|Co\.?|Company|Group|Holdings?|GmbH|S\.?A\.?|PLC|LLP|Pty"
COMPANY_SHAPED_RE = re.compile(
    r"\b((?:[A-Z][\w&'\-]*)(?:\s+(?:[A-Z][\w&'\-]*|of|and|&)){0,4})"
    rf"(?:\s+(?:{_CORP_SUFFIX}))\b"
)
_COMPANY_NORM_STRIP_RE = re.compile(
    rf"\b(?:{_CORP_SUFFIX})\.?\b|[^\w\s]",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


def _normalize_company(name: str) -> str:
    """Strip corporate suffixes + punctuation, lowercase, collapse whitespace."""
    s = _COMPANY_NORM_STRIP_RE.sub(" ", name).lower()
    return _WS_RE.sub(" ", s).strip()

# Cached system prompt — everything that doesn't change per lead lives here.
# Tagged with cache_control=ephemeral so the 50-lead batch only pays full
# input-token price once.
SYSTEM_PROMPT = """You are a sales analyst for Prelude, a B2B platform helping a mid-size Chinese factory that exports Christmas decorations and seasonal goods (artificial trees, ornaments, lights, decorative foliage) identify US importers worth contacting this week.

Given a structured payload of signals for one US importer — derived from US Customs Bill-of-Lading (BOL) data, competitor shipment overlap, and personnel reachability — produce a 1-2 sentence rationale that explains whether and why this lead is worth pursuing now.

Constraints (strictly enforced by downstream factuality check; violations are rejected):
- Cite at least one SPECIFIC fact from the payload: an HS code (6-digit), a port name, a competitor company name, a contact title, or a numeric BOL metric.
- Do NOT mention or invent any HS code, port, competitor name, person, or company not present in the payload.
- Do NOT mention the lead's score itself.
- Be terse and analytical. Plain factual English. No marketing language, no exclamation marks, no hedging adverbs.

Status semantics — read carefully:
- `synced_to_crm` is the NORMAL/default state in this dataset; every active importer is already in the operator's CRM. It is NOT a negative signal. Do not criticize it, do not call it "already in CRM", do not say "deprioritize for that reason", do not mention it at all unless directly relevant. Treat absence of `synced_to_crm` as neutral.
- `not_interested` means the operator has previously contacted this lead and they declined. They are still worth pursuing on a strong new signal — frame the rationale as a re-engagement angle citing what changed (volume, recency, new contact, HS shift). Do not treat as disqualifying.

The payload includes a "Top signal" field — let it guide where you anchor the rationale, but cite the literal data, not the label.

Respond with ONLY a JSON object of shape:
{"reasoning": "<one or two sentences>"}
"""


# ---------- result type ----------

RationaleSource = Literal["llm", "fallback_no_key", "fallback_error", "fallback_factuality"]

_DRIVER_BLURB: dict[str, str] = {
    "volume":       "high shipment volume",
    "recency":      "recent shipping activity",
    "hs_fit":       "HS-code overlap with factory profile",
    "competitive":  "low competitor pressure",
    "reachability": "reachable contacts",
    "seniority":    "decision-maker contact present",
}


@dataclass
class Rationale:
    text: str
    source: RationaleSource
    factuality_ok: bool
    cited_hs_codes: list[str] = field(default_factory=list)
    cited_ports: list[str] = field(default_factory=list)
    cited_competitors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- payload builder ----------

def _build_user_payload(f: LeadFeatures, components: dict[str, float]) -> str:
    driver = top_driver(components)
    driver_label = _DRIVER_BLURB.get(driver, driver)

    parts = [
        f"Lead: {f.company} (id={f.lead_id})",
        f"Workflow status: {f.status or 'new'}",
        (
            f"BOL signal: total_shipments={f.total_shipments}, "
            f"matching_shipments={f.matching_shipments}, "
            "days_since_most_recent_shipment="
            + (str(f.days_since_recent) if f.days_since_recent is not None else "unknown")
        ),
        f"Lead's HS codes (top): {', '.join(f.lead_hs_codes) or 'none'}",
        (
            f"HS overlap with factory profile: {', '.join(f.hs_overlap) or 'none'} "
            f"({f.hs_overlap_count}/{len(f.lead_hs_codes)} codes, ratio {f.hs_overlap_ratio:.2f})"
        ),
        f"Lead's top ports: {', '.join(f.top_ports) or 'none'}",
        (
            f"Competitive pressure: {f.competitor_count} distinct exporters shipping to this lead"
            + (f", top exporter={f.top_competitor_name}" if f.top_competitor_name else "")
            + (
                f", max_supplier_share_pct={f.max_competitor_share_pct:.1f}"
                if f.max_competitor_share_pct is not None
                else ""
            )
        ),
        (
            f"Personnel: {f.contact_count} contacts in dataset, has_email={f.has_email}, "
            f"senior_contact_title={f.senior_contact_title or 'none'}"
            + (
                f", senior_contact_name={f.senior_contact_name}"
                if f.senior_contact_name
                else ""
            )
        ),
        f"Top signal (highest-weighted component): {driver} — {driver_label}",
    ]
    # NOTE: synced_to_crm is the default state in this dataset; don't surface
    # it in the prompt at all (the system prompt already tells the model so).
    if f.is_not_interested:
        parts.append(
            "Status=not_interested — operator previously contacted this lead and they declined. "
            "Frame the rationale as a re-engagement angle: cite the new or strong "
            "signal that justifies a second attempt. Do not write the lead off."
        )
    return "\n".join(parts)


# ---------- factuality check ----------

def _has_numeric_bol_anchor(text: str, f: LeadFeatures) -> bool:
    """True if the text honestly references one of the lead's numeric BOL
    metrics (including legitimate zeros). Lets "no-signal" leads pass the
    anchor requirement when no HS / port / competitor / title exists to cite.
    """
    lower = text.lower()
    # Direct numeric mention of any populated BOL metric
    for metric in (f.total_shipments, f.matching_shipments, f.days_since_recent):
        if metric is not None and re.search(rf"\b{re.escape(str(metric))}\b", text):
            return True
    # Honest "zero/no" phrasing about activity — valid anchor when actually zero
    if (f.total_shipments or 0) == 0 and (f.matching_shipments or 0) == 0:
        if re.search(r"\b(no|zero|0)\s+(matching\s+)?shipments?\b", lower):
            return True
        if "no recent" in lower or "no import" in lower:
            return True
    return False


def _factuality_check(text: str, f: LeadFeatures) -> FactualityCheck:
    """Verify cited facts exist in the feature payload.

    ok == True requires:
      1. every 6-digit number cited is in f.lead_hs_codes (no hallucinated HS)
      2. every company-shaped phrase in the rationale resolves to the lead's
         own company name (catches name substitution e.g. Pearhead → Pearson Inc).
         Known anchors that LOOK like company-shaped phrases (competitor name,
         contact name) are excluded from this check.
      3. at least one specific anchor cited. Anchors, in priority order:
         a. HS code present in lead's hs_codes
         b. port name from lead's top_ports
         c. competitor name from lead's top_competitor_name
         d. senior contact title
         e. correct mention of the lead's own company name
         f. numeric BOL metric from the lead's own data (incl. honest zeros) —
            covers the "no-signal" case where the lead has no positive anchor
            available; without this rule a truthful "0 matching shipments, no
            HS overlap" rationale would be wrongly rejected
    """
    lower = text.lower()
    lead_codes = set(f.lead_hs_codes)

    cited_hs = HS_CODE_RE.findall(text)
    invalid_hs = [c for c in cited_hs if c not in lead_codes]
    hs_ok = not invalid_hs

    cited_ports = [p for p in f.top_ports if p and p.lower() in lower]

    cited_competitors: list[str] = []
    if f.top_competitor_name and f.top_competitor_name.lower() in lower:
        cited_competitors.append(f.top_competitor_name)

    cited_titles: list[str] = []
    if f.senior_contact_title and f.senior_contact_title.lower() in lower:
        cited_titles.append(f.senior_contact_title)

    # Company-name validation: find every Inc/LLC/Co/Ltd-suffixed phrase, normalize
    # both that and the lead's company name, compare. Known non-lead entities
    # (resolved competitor name) are whitelisted so they don't count as invalid.
    lead_norm = _normalize_company(f.company) if f.company else ""
    whitelist = {lead_norm}
    if f.top_competitor_name:
        whitelist.add(_normalize_company(f.top_competitor_name))
    if f.senior_contact_name:
        whitelist.add(_normalize_company(f.senior_contact_name))

    cited_companies: list[str] = []
    invalid_companies: list[str] = []
    for match in COMPANY_SHAPED_RE.findall(text):
        norm = _normalize_company(match)
        if not norm:
            continue
        if norm in whitelist:
            if norm == lead_norm and norm:
                cited_companies.append(match.strip())
        else:
            invalid_companies.append(match.strip())

    # Also catch lead-name mentions WITHOUT a corporate suffix (rare but valid
    # anchor). Substring match on the normalized lead name in the lowered text.
    if lead_norm and lead_norm in lower and not cited_companies:
        cited_companies.append(f.company)

    company_ok = not invalid_companies

    has_bol_anchor = _has_numeric_bol_anchor(text, f)
    has_anchor = bool(
        cited_hs or cited_ports or cited_competitors or cited_titles
        or cited_companies or has_bol_anchor
    )

    reason: str | None = None
    if not hs_ok:
        reason = f"cited HS code(s) {invalid_hs} not in lead's hs_codes"
    elif not company_ok:
        reason = (
            f"company-shaped phrase {invalid_companies} does not match lead "
            f"{f.company!r}"
        )
    elif not has_anchor:
        reason = "no specific anchor cited (no HS code, port, competitor, title, company, or BOL metric)"

    return FactualityCheck(
        ok=hs_ok and company_ok and has_anchor,
        cited_hs_codes=cited_hs,
        cited_ports=cited_ports,
        cited_competitors=cited_competitors,
        cited_titles=cited_titles,
        cited_companies=cited_companies,
        invalid_hs_codes=invalid_hs,
        invalid_companies=invalid_companies,
        has_anchor=has_anchor,
        reason=reason,
    )


# ---------- deterministic fallback ----------

def fallback_text(f: LeadFeatures, components: dict[str, float]) -> str:
    driver = top_driver(components)
    parts: list[str] = []

    if f.is_not_interested:
        parts.append("Previously declined; re-engagement candidate on new signal")

    if f.hs_overlap:
        parts.append(
            f"HS {', '.join(f.hs_overlap[:2])} match factory profile"
            + (f" ({len(f.hs_overlap)}/{len(f.lead_hs_codes)} codes)" if f.lead_hs_codes else "")
        )
    elif f.lead_hs_codes:
        parts.append(
            f"HS {', '.join(f.lead_hs_codes[:2])} — no overlap with factory profile"
        )

    if f.total_shipments:
        recency = (
            f"{f.days_since_recent}d ago"
            if f.days_since_recent is not None
            else "recency unknown"
        )
        parts.append(f"{f.total_shipments} BOL shipments (last {recency})")
    elif f.matching_shipments:
        parts.append(f"{f.matching_shipments} matching shipments on file")

    if f.top_ports:
        parts.append(f"via {', '.join(f.top_ports[:2])}")

    if f.top_competitor_name and f.max_competitor_share_pct:
        parts.append(
            f"{f.top_competitor_name} supplies {f.max_competitor_share_pct:.1f}% of volume"
        )
    elif f.competitor_count:
        parts.append(f"{f.competitor_count} competing exporters")

    if f.senior_contact_title:
        parts.append(f"reachable via {f.senior_contact_title}")
    elif f.has_email:
        parts.append("email reachable")
    elif f.contact_count == 0:
        parts.append("no contact in dataset")

    # synced_to_crm = default state on this dataset; not mentioned in fallback.

    if not parts:
        # Use driver as last resort
        return f"Top signal: {_DRIVER_BLURB.get(driver, driver)}. Sparse downstream data."
    return ". ".join(parts).rstrip(".") + "."


# ---------- Anthropic call ----------

async def _llm_call(
    client, f: LeadFeatures, user_payload: str
) -> tuple[str | None, LLMCallTrace]:
    """Single Anthropic call with one retry on rate-limit / transient errors.

    Returns (extracted_reasoning_text_or_None, llm_call_trace). The trace is
    always populated (latency, model, error string if any) so callers can audit
    the call regardless of success.
    """
    started = time.monotonic()
    trace = LLMCallTrace(model=MODEL_DEFAULT, latency_ms=0)
    msg = None
    last_err: Exception | None = None
    for attempt in range(LLM_RETRY_ATTEMPTS):
        try:
            msg = await client.messages.create(
                model=MODEL_DEFAULT,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_payload}],
            )
            last_err = None
            break
        except Exception as e:
            last_err = e
            # Retry only on 429 / transient errors; bail immediately on 4xx etc.
            err_name = type(e).__name__
            transient = "RateLimit" in err_name or "Timeout" in err_name or "APIConnection" in err_name
            if not transient or attempt == LLM_RETRY_ATTEMPTS - 1:
                break
            log.warning(
                "rationale.llm_call lead=%s attempt=%d transient err=%s — backing off %.1fs",
                f.lead_id, attempt + 1, err_name, LLM_RETRY_BACKOFF_S,
            )
            await asyncio.sleep(LLM_RETRY_BACKOFF_S)

    if msg is None:
        # All attempts failed — capture last error and bail to fallback path
        trace.latency_ms = int((time.monotonic() - started) * 1000)
        trace.error = f"{type(last_err).__name__}: {last_err}" if last_err else "unknown error"
        log.warning("rationale.llm_call lead=%s exhausted retries: %s", f.lead_id, trace.error)
        return None, trace

    try:
        trace.latency_ms = int((time.monotonic() - started) * 1000)
        usage = getattr(msg, "usage", None)
        if usage is not None:
            trace.input_tokens = getattr(usage, "input_tokens", None)
            trace.output_tokens = getattr(usage, "output_tokens", None)
            trace.cache_read_input_tokens = getattr(usage, "cache_read_input_tokens", None)
            trace.cache_creation_input_tokens = getattr(usage, "cache_creation_input_tokens", None)
        raw = "".join(b.text for b in msg.content if hasattr(b, "text")).strip()
        trace.raw_response_text = raw
        if not raw:
            return None, trace
        # Strict JSON first
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("reasoning"):
                return str(parsed["reasoning"]).strip(), trace
        except (ValueError, TypeError):
            pass
        # Lenient extraction — model sometimes wraps JSON in extra prose
        m = re.search(r'"reasoning"\s*:\s*"((?:[^"\\]|\\.)+)"', raw)
        if m:
            extracted = (
                m.group(1)
                .encode("utf-8")
                .decode("unicode_escape", errors="replace")
                .strip()
            )
            return extracted, trace
        return None, trace
    except Exception as e:
        trace.latency_ms = int((time.monotonic() - started) * 1000)
        trace.error = f"{type(e).__name__}: {e}"
        log.warning("rationale.llm_call lead=%s err=%s", f.lead_id, e)
        return None, trace




# ---------- public API ----------

def get_anthropic_client():
    """Return an AsyncAnthropic client, or None if key/SDK is unavailable.

    Returning None lets callers (e.g. cache._run_batch) cleanly take the
    fallback path without raising. Cached behind module-level None test.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        log.error("rationale: anthropic SDK not installed — using fallback")
        return None
    return AsyncAnthropic(api_key=api_key)


def make_fallback(
    f: LeadFeatures,
    c: dict[str, float],
    source: RationaleSource = "fallback_no_key",
) -> tuple[Rationale, RationaleTrace]:
    """Build (Rationale, RationaleTrace) for the deterministic fallback path."""
    fb = fallback_text(f, c)
    rationale = Rationale(text=fb, source=source, factuality_ok=True)
    trace = RationaleTrace(
        lead_id=f.lead_id,
        generated_at=time.time(),
        user_payload=_build_user_payload(f, c),
        system_prompt=SYSTEM_PROMPT,
        llm=None,
        factuality=None,
        final_source=source,
        final_text=fb,
        fallback_text=fb,
    )
    return rationale, trace


async def rationalize_one_async(
    client, f: LeadFeatures, c: dict[str, float]
) -> tuple[Rationale, RationaleTrace]:
    """Single-lead LLM path: call → factuality → outcome.

    Caller is responsible for concurrency limiting (semaphore) and for
    writing the result somewhere visible (cache, log, file). Use this when
    you want to stream completions to the cache as they arrive rather than
    waiting for a whole batch via `rationalize_batch_async`.
    """
    user_payload = _build_user_payload(f, c)
    text, llm_trace = await _llm_call(client, f, user_payload)

    trace = RationaleTrace(
        lead_id=f.lead_id,
        generated_at=time.time(),
        user_payload=user_payload,
        system_prompt=SYSTEM_PROMPT,
        llm=llm_trace,
    )

    if not text:
        fb = fallback_text(f, c)
        trace.final_source = "fallback_error"
        trace.final_text = fb
        trace.fallback_text = fb
        emit("llm_call", f"LLM call failed for {f.company}",
             lead_id=f.lead_id, latency_ms=llm_trace.latency_ms,
             error=llm_trace.error or "empty response")
        return Rationale(text=fb, source="fallback_error", factuality_ok=True), trace

    fc = _factuality_check(text, f)
    trace.factuality = fc

    if not fc.ok:
        fb = fallback_text(f, c)
        trace.final_source = "fallback_factuality"
        trace.final_text = fb
        trace.fallback_text = fb
        log.warning(
            "rationale.factuality_fail lead=%s reason=%s text=%r",
            f.lead_id, fc.reason, text[:120],
        )
        emit("factuality_fail", f"factuality rejected for {f.company}",
             lead_id=f.lead_id, reason=fc.reason,
             cited_hs=fc.cited_hs_codes, invalid_hs=fc.invalid_hs_codes,
             text=text[:200])
        return Rationale(
            text=fb,
            source="fallback_factuality",
            factuality_ok=False,
            cited_hs_codes=fc.cited_hs_codes,
            cited_ports=fc.cited_ports,
            cited_competitors=fc.cited_competitors,
        ), trace

    trace.final_source = "llm"
    trace.final_text = text
    emit("llm_call", f"LLM rationale generated for {f.company}",
         lead_id=f.lead_id, latency_ms=llm_trace.latency_ms,
         in_tok=llm_trace.input_tokens, out_tok=llm_trace.output_tokens,
         cache_read=llm_trace.cache_read_input_tokens)
    return Rationale(
        text=text,
        source="llm",
        factuality_ok=True,
        cited_hs_codes=fc.cited_hs_codes,
        cited_ports=fc.cited_ports,
        cited_competitors=fc.cited_competitors,
    ), trace


async def rationalize_batch_async(
    items: list[tuple[LeadFeatures, dict[str, float]]],
    *,
    concurrency: int = CONCURRENCY,
) -> list[tuple[Rationale, RationaleTrace]]:
    """Generate rationales + traces for a batch of (features, components) pairs.

    Returns a list of (Rationale, RationaleTrace) pairs in INPUT ORDER. Awaits
    all items before returning. For streaming (write each to cache as it
    completes), call `rationalize_one_async` directly with your own semaphore.
    """
    if not items:
        return []

    client = get_anthropic_client()
    if client is None:
        log.info(
            "rationale: no API key/SDK — deterministic fallback for %d leads",
            len(items),
        )
        return [make_fallback(f, c, "fallback_no_key") for f, c in items]

    sem = asyncio.Semaphore(concurrency)

    async def guarded(f, c):
        async with sem:
            return await rationalize_one_async(client, f, c)

    return await asyncio.gather(*(guarded(f, c) for f, c in items))


def rationalize_batch(
    items: list[tuple[LeadFeatures, dict[str, float]]],
) -> list[tuple[Rationale, RationaleTrace]]:
    """Sync wrapper for CLI / test use. Don't call from inside a running event loop."""
    return asyncio.run(rationalize_batch_async(items))


def rationalize(features: LeadFeatures, components: dict[str, float]) -> str:
    """Single-lead rationale — matches the stub signature for backwards-compat.

    Returns just the text string. For batches, prefer rationalize_batch_async
    so the LLM calls run in parallel and the prompt cache is shared.
    """
    return rationalize_batch([(features, components)])[0][0].text


def rationalize_rich(
    features: LeadFeatures, components: dict[str, float]
) -> tuple[Rationale, RationaleTrace]:
    """Same as `rationalize` but returns the full Rationale + RationaleTrace.
    Useful for eval scripts and audit tools."""
    return rationalize_batch([(features, components)])[0]
