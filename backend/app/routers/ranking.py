"""Ranking endpoint: deterministic feature pipeline + score + (stubbed) LLM rationale."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..ranking.features import extract_all
from ..ranking.persona import get_persona
from ..ranking.rationale import rationalize
from ..ranking.score import score_lead

router = APIRouter(prefix="/leads", tags=["ranking"])


@router.get("/ranked")
def get_ranked(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict]:
    persona = get_persona(db)
    all_features = extract_all(db, persona)

    scored: list[tuple] = []
    for f in all_features:
        if f.is_not_interested:
            continue
        composite, components = score_lead(f)
        scored.append((f, composite, components))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:limit]

    return [
        {
            "lead_id": f.lead_id,
            "company": f.company,
            "score": round(composite, 4),
            "components": {k: round(v, 4) for k, v in components.items()},
            "features": f.to_dict(),
            "reasoning": rationalize(f, components),
            "selected": False,
        }
        for f, composite, components in top
    ]


@router.get("/persona")
def get_factory_persona(db: Session = Depends(get_db)) -> dict:
    """Expose the inferred factory HS-code profile (for debugging / README)."""
    p = get_persona(db)
    return {
        "hs_codes": sorted(p.hs_codes),
        "ranks": p.hs_code_ranks,
    }
