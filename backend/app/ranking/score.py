"""Composite scoring over LeadFeatures.

Each component is normalized into [0, 1] then combined by hand-set weights.
The breakdown dict is returned alongside the composite so the rationale
layer (and ablation tests) can see what drove a given lead's rank.

Weight design notes (data-driven, see backend/.dev/snapshot.json analysis):

  recency=0     16/121 leads have most_recent_shipment; 3/50 nonzero in top.
                Recency contributed ~0.05 average — pure noise on this data.
                Folded into volume's weight until source data improves.

  competitive=0 5/50 top-ranked have any resolved competitor edges; the other
                45 trivially scored 1.0 (no data == perfect score, inverted bug).
                Zero-weighted until the bipartite graph is denser. The number
                is still computed + shown in the rationale prompt for context.

  hs_fit=0.35   The dominant discriminator. HS-code match to the factory's
                inferred persona is the single most defensible signal.

  volume=0.30   Log-scaled total_shipments. Captures "real importer" vs trace.

  reachability=0.25  Has-email + contact count. Mid-signal but high coverage.

  seniority=0.10     Title regex. Sparse (7/50 senior in last snapshot) but
                     when it fires it materially changes outreach value.

Status policy:

  not_interested → composite × 0.4. Operator passed previously; in real
                   pipelines re-engagement is normal, but only when the new
                   signal is strong. Penalty lets a high-signal lead resurface
                   above weak fresh leads while keeping it below average ones.
                   (Old behavior: dropped entirely. Too brittle.)

  synced_to_crm  → no modifier. The penalty was uniform on current data
                   (every eligible lead is synced_to_crm) so it just lowered
                   the displayed scale without changing rank.

  new            → no live rule yet (no `new` leads in current dataset). Add
                   a multiplicative boost (e.g. 1.3) when the bucket appears,
                   to surface untapped leads against an already-warm pipeline.
"""
from __future__ import annotations

import math

from .features import LeadFeatures

WEIGHTS: dict[str, float] = {
    "volume":       0.30,
    "recency":      0.00,
    "hs_fit":       0.35,
    "competitive":  0.00,
    "reachability": 0.25,
    "seniority":    0.10,
}

# Reference cap for log-scaled volume. 500 shipments ≈ a major importer; above
# that we treat further volume as saturated rather than letting one whale dominate.
VOLUME_LOG_CAP = math.log1p(500)
RECENCY_HORIZON_DAYS = 180
COMPETITOR_SATURATION = 10
CONTACT_SATURATION = 3

NOT_INTERESTED_PENALTY = 0.4   # Slight, not a drop. See module docstring.


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _component_scores(f: LeadFeatures) -> dict[str, float]:
    volume = _clamp(math.log1p(max(0, f.total_shipments)) / VOLUME_LOG_CAP)
    if f.days_since_recent is None:
        recency = 0.0
    else:
        recency = _clamp(1.0 - f.days_since_recent / RECENCY_HORIZON_DAYS)
    hs_fit = _clamp(f.hs_overlap_ratio)
    # Zero-competitor leads default to NEUTRAL (0.5), not perfect 1.0 —
    # absence of data shouldn't reward a lead. Once edge coverage improves
    # and `competitive` is re-weighted, this stops handing out free score.
    if f.competitor_count == 0:
        competitive = 0.5
    else:
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

    `not_interested` leads are NOT filtered out — they are scored with a 0.4×
    composite penalty so a high-signal previously-declined lead can still
    surface above weaker untouched leads (re-engagement case).
    """
    components = _component_scores(f)
    composite = sum(WEIGHTS[k] * v for k, v in components.items())
    if f.is_not_interested:
        composite *= NOT_INTERESTED_PENALTY
    return _clamp(composite), components


def top_driver(components: dict[str, float]) -> str:
    """Return the component name with the highest weighted contribution.

    Components with WEIGHTS=0 cannot win (contribution is always 0), so the
    returned driver reflects the live scoring story, not the raw features.
    """
    weighted = {k: WEIGHTS[k] * v for k, v in components.items()}
    return max(weighted, key=weighted.get)
