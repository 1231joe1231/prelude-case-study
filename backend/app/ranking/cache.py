"""In-memory rationale cache + background batch dispatcher.

The router needs to return immediately with a fallback rationale, then upgrade
to an LLM-generated rationale in the background. The frontend polls the same
endpoint every ~2s; each call merges the latest cache state with the live
ranking. Once all leads have status != 'pending', polling stops.

Cache is process-local and cleared on restart. Keyed by lead_id only; if
features change (re-ingest), call `clear_cache()` from the lifespan hook.

Concurrency model:
  - Single asyncio.Lock guards _cache + _in_flight mutations
  - LLM batch runs as a fire-and-forget asyncio.create_task
  - Duplicate /ranked requests during a pending batch are no-ops (idempotent
    via the _in_flight set)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal

from ..pipeline import state as pstate
from .features import LeadFeatures
from .rationale import (
    CONCURRENCY,
    Rationale,
    fallback_text,
    get_anthropic_client,
    make_fallback,
    rationalize_one_async,
)
from .trace import emit, put_trace

log = logging.getLogger("uvicorn.error")

RationaleStatus = Literal["pending", "llm", "fallback"]


@dataclass
class CachedRationale:
    text: str
    source: RationaleStatus
    factuality_ok: bool
    cited_hs_codes: list[str] = field(default_factory=list)
    cited_ports: list[str] = field(default_factory=list)
    cited_competitors: list[str] = field(default_factory=list)
    generated_at: float = 0.0


_cache: dict[str, CachedRationale] = {}
_in_flight: set[str] = set()
_lock = asyncio.Lock()


def _to_cached(r: Rationale) -> CachedRationale:
    # rationalize_batch_async returns source ∈ {llm, fallback_no_key,
    # fallback_error, fallback_factuality}. We collapse the 3 fallback flavors
    # to a single 'fallback' status for the UI; the original is logged.
    status: RationaleStatus = "llm" if r.source == "llm" else "fallback"
    return CachedRationale(
        text=r.text,
        source=status,
        factuality_ok=r.factuality_ok,
        cited_hs_codes=r.cited_hs_codes,
        cited_ports=r.cited_ports,
        cited_competitors=r.cited_competitors,
        generated_at=time.time(),
    )


async def get_or_kick(
    items: list[tuple[LeadFeatures, dict[str, float]]],
) -> dict[str, CachedRationale]:
    """Ensure every lead in `items` has a cache entry; kick off an LLM batch
    for the ones not yet generated; return the current cache snapshot for the
    requested leads (each entry will be either 'pending' fallback or final).

    The returned dict is keyed by lead_id and contains only the requested
    leads.
    """
    to_run: list[tuple[LeadFeatures, dict[str, float]]] = []
    snapshot: dict[str, CachedRationale] = {}

    async with _lock:
        for f, c in items:
            existing = _cache.get(f.lead_id)
            if existing is not None:
                # Already cached (pending, llm, or fallback) — return whatever's there
                snapshot[f.lead_id] = existing
                continue
            # First sight of this lead: seed cache with pending+fallback text
            placeholder = CachedRationale(
                text=fallback_text(f, c),
                source="pending",
                factuality_ok=False,
                generated_at=time.time(),
            )
            _cache[f.lead_id] = placeholder
            _in_flight.add(f.lead_id)
            snapshot[f.lead_id] = placeholder
            to_run.append((f, c))

    if to_run:
        log.info("rationale.cache: kicking off batch for %d leads", len(to_run))
        emit(
            "batch_dispatched",
            f"LLM batch dispatched for {len(to_run)} lead(s)",
            count=len(to_run),
            lead_ids=[f.lead_id for f, _ in to_run],
        )
        asyncio.create_task(_run_batch(to_run))

    return snapshot


async def _persist_result(
    f: LeadFeatures, rationale: Rationale, trace
) -> None:
    """Write one finished lead's result + trace to the cache. Single source of
    truth for cache mutation from background workers."""
    async with _lock:
        _cache[f.lead_id] = _to_cached(rationale)
        put_trace(trace)
        _in_flight.discard(f.lead_id)


async def _run_batch(items: list[tuple[LeadFeatures, dict[str, float]]]) -> None:
    """Background worker: stream per-lead rationales → cache as each completes.

    Each lead is its own awaitable; the cache write happens the moment the
    lead's rationale + factuality check resolve, so the frontend's 2s poll
    sees rationales appear incrementally instead of all-at-once.
    """
    started = time.time()
    by_source: dict[str, int] = {}

    client = get_anthropic_client()
    if client is None:
        # No key / no SDK — write fallback for every lead immediately
        log.info("rationale.cache: no API key/SDK; fallback for %d leads", len(items))
        for f, c in items:
            r, t = make_fallback(f, c, "fallback_no_key")
            await _persist_result(f, r, t)
            by_source[t.final_source] = by_source.get(t.final_source, 0) + 1
            pstate.tick_llm(t.final_source)
        emit(
            "batch_complete",
            f"LLM batch complete (no-key fallback): {by_source}",
            count=len(items),
            by_source=by_source,
            duration_ms=int((time.time() - started) * 1000),
        )
        pstate.finish(finished_at=time.time())
        return

    sem = asyncio.Semaphore(CONCURRENCY)

    async def worker(f: LeadFeatures, c: dict[str, float]) -> str:
        async with sem:
            try:
                rationale, trace = await rationalize_one_async(client, f, c)
            except Exception as e:
                log.exception("rationale.cache: worker crashed for lead=%s", f.lead_id)
                rationale, trace = make_fallback(f, c, "fallback_error")
                if trace.llm is None:
                    pass  # make_fallback already set llm=None
                trace.final_text = rationale.text
        await _persist_result(f, rationale, trace)
        return trace.final_source

    tasks = [asyncio.create_task(worker(f, c)) for f, c in items]
    for coro in asyncio.as_completed(tasks):
        try:
            src = await coro
            by_source[src] = by_source.get(src, 0) + 1
            pstate.tick_llm(src)
        except Exception:
            log.exception("rationale.cache: task wrapper exception")

    emit(
        "batch_complete",
        f"LLM batch complete: {by_source}",
        count=len(items),
        by_source=by_source,
        duration_ms=int((time.time() - started) * 1000),
    )
    pstate.finish(finished_at=time.time())
    log.info("rationale.cache: batch complete for %d leads (%s)", len(items), by_source)


def get_cached(lead_id: str) -> CachedRationale | None:
    return _cache.get(lead_id)


def stats() -> dict[str, int]:
    counts = {"pending": 0, "llm": 0, "fallback": 0}
    for v in _cache.values():
        counts[v.source] = counts.get(v.source, 0) + 1
    return counts


def clear_cache() -> None:
    _cache.clear()
    _in_flight.clear()
