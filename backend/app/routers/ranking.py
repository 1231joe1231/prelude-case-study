"""Ranking endpoint: read-only.

Computes scoring on the fly (it's deterministic + cheap) but does NOT kick the
LLM batch. The full pipeline (including rationale generation) is triggered
explicitly via POST /api/pipeline/run from the frontend Pipeline page.

Rationale text comes from whatever's in the cache:
  - cached llm/fallback  → real generated text
  - no cache entry yet   → reasoning="" and rationale_source="not_generated"

Frontend should show a "Run pipeline first" message when most rows report
'not_generated'.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..ranking.cache import get_cached, stats as cache_stats
from ..ranking.features import extract_all
from ..ranking.persona import get_persona
from ..ranking.score import WEIGHTS, score_lead
from ..ranking.trace import events_since, get_trace, trace_count

router = APIRouter(prefix="/leads", tags=["ranking"])


@router.get("/ranked")
def get_ranked(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    persona = get_persona(db)
    all_features = extract_all(db, persona)

    # not_interested leads are NOT filtered — they're penalized in score_lead
    # so a strong re-engagement signal can still surface them.
    scored = [(f, *score_lead(f)) for f in all_features]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:limit]

    rows = []
    row_stats = {"not_generated": 0, "pending": 0, "llm": 0, "fallback": 0}
    for f, composite, components in top:
        cached = get_cached(f.lead_id)
        if cached is None:
            src = "not_generated"
            text = ""
            fact_ok = False
        else:
            src = cached.source
            text = cached.text
            fact_ok = cached.factuality_ok
        rows.append({
            "lead_id": f.lead_id,
            "company": f.company,
            "score": round(composite, 4),
            "components": {k: round(v, 4) for k, v in components.items()},
            "features": f.to_dict(),
            "reasoning": text,
            "rationale_source": src,
            "factuality_ok": fact_ok,
            "selected": False,
        })
        row_stats[src] = row_stats.get(src, 0) + 1

    return {
        "rows": rows,
        # Stats computed over RANKED ROWS, not whole cache, so totals match
        # the displayed table. Whole-cache stats live on /api/ranking/events.
        "stats": row_stats,
        "cache_stats": cache_stats(),
        "pending_count": row_stats["pending"],
        "not_generated_count": row_stats["not_generated"],
    }


@router.get("/persona")
def get_factory_persona(db: Session = Depends(get_db)) -> dict:
    """Expose the inferred factory HS-code profile (for debugging / README)."""
    p = get_persona(db)
    return {
        "hs_codes": sorted(p.hs_codes),
        "ranks": p.hs_code_ranks,
    }


@router.get("/{lead_id}/trace")
def get_lead_trace(lead_id: str) -> dict:
    """Per-lead full pipeline trace: features payload, LLM prompt, raw response,
    factuality check, fallback used (if any). Populated by the rationale cache;
    returns 404 if /leads/ranked hasn't been called yet for this lead.

    The trace is the auditability surface: every claim in the displayed
    rationale traces back to a literal value in the source data.
    """
    t = get_trace(lead_id)
    if t is None:
        raise HTTPException(
            status_code=404,
            detail=f"no trace for lead_id={lead_id}; call /api/leads/ranked first",
        )
    return t.to_dict()


# Sibling router under /ranking for system-level introspection.
ranking_router = APIRouter(prefix="/ranking", tags=["ranking"])


@ranking_router.get("/events")
def get_events(since_seq: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)) -> dict:
    """Global pipeline event stream.

    Cross-lead events emitted by the pipeline: ingest, persona inference,
    batch dispatch/complete, individual LLM calls, factuality failures.
    Frontend polls with ?since_seq=<last_seen> to stream new events.
    """
    evs = events_since(since_seq=since_seq, limit=limit)
    return {
        "events": [
            {"seq": e.seq, "ts": e.ts, "kind": e.kind, "summary": e.summary, "payload": e.payload}
            for e in evs
        ],
        "trace_count": trace_count(),
        "cache_stats": cache_stats(),
        "weights": WEIGHTS,
    }
