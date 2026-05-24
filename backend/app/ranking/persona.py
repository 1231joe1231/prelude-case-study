"""Factory persona: HS-code profile inferred from competitor_attributes.

The brief's working assumption (CLAUDE.md): the operator factory's product
profile is approximated by the dominant HS codes across the known Chinese
competitor set (Christmas/decoration goods). Computed once at startup and
cached; recomputed only when the cache is cleared (e.g. after re-ingest).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CompetitorAttribute
from .trace import emit

DEFAULT_TOP_N = 8


@dataclass(frozen=True)
class Persona:
    hs_codes: frozenset[str]
    hs_code_ranks: dict[str, int]  # 0-indexed; lower = more dominant


_cached: Persona | None = None


def infer_persona(db: Session, top_n: int = DEFAULT_TOP_N) -> Persona:
    rows = db.scalars(
        select(CompetitorAttribute.value).where(CompetitorAttribute.kind == "hs_code")
    ).all()
    counts = Counter(v for v in rows if v)
    top_with_counts = counts.most_common(top_n)
    top = [code for code, _ in top_with_counts]
    emit(
        "persona_inferred",
        f"Persona HS profile: {', '.join(top)}",
        top_n=top_n,
        hs_codes=top,
        counts={code: cnt for code, cnt in top_with_counts},
        total_distinct=len(counts),
    )
    return Persona(
        hs_codes=frozenset(top),
        hs_code_ranks={code: i for i, code in enumerate(top)},
    )


def get_persona(db: Session) -> Persona:
    global _cached
    if _cached is None:
        _cached = infer_persona(db)
    return _cached


def clear_cache() -> None:
    global _cached
    _cached = None
