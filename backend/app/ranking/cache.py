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

from .features import LeadFeatures
from .rationale import (
    Rationale,
    fallback_text,
    rationalize_batch_async,
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


async def _run_batch(items: list[tuple[LeadFeatures, dict[str, float]]]) -> None:
    """Background worker: run LLM batch, write results + traces to cache."""
    lead_ids = [f.lead_id for f, _ in items]
    started = time.time()
    try:
        results = await rationalize_batch_async(items)
    except Exception as e:
        log.exception("rationale.cache: batch failed for %d leads", len(items))
        emit(
            "batch_complete",
            f"LLM batch FAILED for {len(items)} lead(s)",
            count=len(items),
            error=f"{type(e).__name__}: {e}",
            duration_ms=int((time.time() - started) * 1000),
        )
        results = None

    async with _lock:
        if results is not None:
            for (f, _), (rationale, trace) in zip(items, results):
                _cache[f.lead_id] = _to_cached(rationale)
                put_trace(trace)
        else:
            # Batch died entirely: downgrade pending → fallback (already in text)
            for f, _ in items:
                cur = _cache.get(f.lead_id)
                if cur and cur.source == "pending":
                    cur.source = "fallback"
        for lid in lead_ids:
            _in_flight.discard(lid)

    if results is not None:
        by_source: dict[str, int] = {}
        for _, t in results:
            by_source[t.final_source] = by_source.get(t.final_source, 0) + 1
        emit(
            "batch_complete",
            f"LLM batch complete: {by_source}",
            count=len(items),
            by_source=by_source,
            duration_ms=int((time.time() - started) * 1000),
        )
    log.info("rationale.cache: batch complete for %d leads", len(items))


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
