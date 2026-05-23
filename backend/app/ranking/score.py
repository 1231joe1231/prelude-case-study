"""Composite scoring over LeadFeatures.

Each component is normalized into [0, 1] then combined by hand-set weights.
The breakdown dict is returned alongside the composite so the rationale
layer (and ablation tests) can see what drove a given lead's rank.

Weights are intentionally hand-set, not learned. The CLAUDE.md plan
sketches how `leads.status` could feed a learned weighting once enough
operator decisions exist; not built yet.
"""
from __future__ import annotations

import math

from .features import LeadFeatures

WEIGHTS: dict[str, float] = {
    "volume":       0.20,
    "recency":      0.15,
    "hs_fit":       0.25,
    "competitive":  0.15,
    "reachability": 0.15,
    "seniority":    0.10,
}

# Reference cap for log-scaled volume. 500 shipments ≈ a major importer; above
# that we treat further volume as saturated rather than letting one whale dominate.
VOLUME_LOG_CAP = math.log1p(500)
RECENCY_HORIZON_DAYS = 180
COMPETITOR_SATURATION = 10
CONTACT_SATURATION = 3
SYNCED_TO_CRM_PENALTY = 0.5


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _component_scores(f: LeadFeatures) -> dict[str, float]:
    volume = _clamp(math.log1p(max(0, f.total_shipments)) / VOLUME_LOG_CAP)
    if f.days_since_recent is None:
        recency = 0.0
    else:
        recency = _clamp(1.0 - f.days_since_recent / RECENCY_HORIZON_DAYS)
    hs_fit = _clamp(f.hs_overlap_ratio)
    competitive = _clamp(1.0 - f.competitor_count / COMPETITOR_SATURATION)
    reachability = 0.5 * (1.0 if f.has_email else 0.0) \
        + 0.5 * _clamp(f.contact_count / CONTACT_SATURATION)
    seniority = 1.0 if f.has_senior_contact else 0.3
    return {
        "volume": volume,
        "recency": recency,
        "hs_fit": hs_fit,
        "competitive": competitive,
        "reachability": reachability,
        "seniority": seniority,
    }


def score_lead(f: LeadFeatures) -> tuple[float, dict[str, float]]:
    """Return (composite_score, component_breakdown).

    Composite is in [0, 1]; breakdown values are pre-weight contributions
    (also in [0, 1]) so the rationale layer can pick the top driver.
    Leads with status='synced_to_crm' are halved (deprioritized, not dropped).
    `not_interested` is expected to be filtered out before this function is
    called — it is not handled here.
    """
    components = _component_scores(f)
    composite = sum(WEIGHTS[k] * v for k, v in components.items())
    if f.is_synced_to_crm:
        composite *= SYNCED_TO_CRM_PENALTY
    return _clamp(composite), components


def top_driver(components: dict[str, float]) -> str:
    """Return the component name with the highest weighted contribution."""
    weighted = {k: WEIGHTS[k] * v for k, v in components.items()}
    return max(weighted, key=weighted.get)
