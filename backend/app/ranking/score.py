"""Composite scoring over LeadFeatures.

Each component is normalized then multiplied by its hand-set weight; the
weighted sum is the composite. Weights can be positive (signal helps) or
negative (signal hurts), and component values can be 0 when data is missing
so missing-data leads contribute *nothing* to that signal — neither bonus
nor penalty. Old behavior treated missing data as worst-case (0.0) for
recency and best-case (1.0 before normalization) for competitor count;
both punished or rewarded the wrong thing.

Component policy (current):

  hs_fit          (+0.30) overlap between lead's HS codes and inferred
                  factory profile, as a ratio in [0, 1]. Lead with zero
                  hs_codes scores 0 (no signal).

  volume          (+0.25) log-scaled total_shipments saturating at 500.
                  Captures "real importer" vs trace.

  reachability    (+0.20) half-credit for any email + half-credit scaling
                  with contact count. High coverage on this dataset.

  seniority       (+0.10) 1.0 if a contact title matches a decision-maker
                  pattern, else 0.3 baseline.

  recency         (+0.05) SIGNED: signal value in [-1, +1].
                    None         → 0      (no opinion, no contribution)
                    days ≤ 30    → +1     (fresh, real activity)
                    days = 90    →  0     (neutral)
                    days ≥ 180   → -1     (stale, lead has gone cold)

  demand_validated (+0.10) BINARY signal. 1.0 if any known Chinese
                  competitor ships to this lead, else 0. Reinterprets the
                  competitive signal: presence of competitors proves
                  product fit and qualified buyer ("they buy this thing
                  somewhere"), not just lock-in risk.

  concentration   (-0.05) PENALTY signal in [0, +1], weight is negative.
                  Ramps from 0 to 1 as the top supplier's share grows
                  past 30% of the lead's BOL volume; 50% share already
                  costs the lead ~0.025 of composite. Zero when no
                  supplier data exists (no false penalty on data gaps).

Status policy:

  not_interested → composite × 0.4. Operator passed previously; in real
                   pipelines re-engagement is normal, but only when the
                   new signal is strong. Penalty lets a high-signal lead
                   resurface above weak fresh leads while keeping it
                   below average ones. Old behavior dropped entirely;
                   too brittle.

  synced_to_crm  → no modifier. Every active lead carries this; treating
                   it as a penalty would suppress everyone uniformly.

  hs_overlap=0   → composite × 0.5. Hard product-fit floor. Wrong-product
                   leads can't beat weakly-aligned in-product leads no
                   matter how active they are.

Why missing-data leads return 0 (not the old defaults):

  16/121 leads have a usable most_recent_shipment; 5/50 top-ranked have
  any resolved competitor edges. Treating missing as 0 (old recency
  behavior) punished leads for being absent from the source data;
  treating it as 1.0 (old competitive behavior) rewarded them with a
  free top score. Both were wrong. The new model is: if we don't have
  the signal, we don't claim an opinion — the composite just leans on
  the signals we do have.
"""
from __future__ import annotations

import math

from .features import LeadFeatures

WEIGHTS: dict[str, float] = {
    # Positive-direction signals (more = better)
    "hs_fit":           0.30,
    "volume":           0.25,
    "reachability":     0.20,
    "seniority":        0.10,
    "demand_validated": 0.10,
    "recency":          0.05,   # signed signal in [-1, +1]
    # Negative-direction signal (more = worse)
    "concentration":   -0.05,
}

# Tuning constants
VOLUME_LOG_CAP = math.log1p(500)           # log-scaled volume saturates here
RECENCY_FRESH_DAYS = 30                    # ≤ this many days → +1
RECENCY_STALE_DAYS = 180                   # ≥ this many days → -1
CONCENTRATION_FLOOR_PCT = 30.0             # share below this → no penalty
CONCENTRATION_CEIL_PCT = 100.0             # share at this → full penalty
CONTACT_SATURATION = 3
SENIORITY_BASELINE = 0.3                   # any contact w/o senior title

# Multiplicative composite modifiers
NOT_INTERESTED_PENALTY = 0.4
HS_MISMATCH_PENALTY = 0.5


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _recency_signal(days_since: int | None) -> float:
    """Signed recency in [-1, +1]. None → 0 (no contribution)."""
    if days_since is None:
        return 0.0
    if days_since <= RECENCY_FRESH_DAYS:
        return 1.0
    if days_since >= RECENCY_STALE_DAYS:
        return -1.0
    # Linear interpolate +1 → -1 across (FRESH, STALE)
    span = RECENCY_STALE_DAYS - RECENCY_FRESH_DAYS
    return 1.0 - 2.0 * (days_since - RECENCY_FRESH_DAYS) / span


def _concentration_signal(max_share_pct: float | None) -> float:
    """[0, 1] penalty magnitude. None → 0 (no data, no penalty)."""
    if max_share_pct is None:
        return 0.0
    if max_share_pct <= CONCENTRATION_FLOOR_PCT:
        return 0.0
    span = CONCENTRATION_CEIL_PCT - CONCENTRATION_FLOOR_PCT
    return _clamp((max_share_pct - CONCENTRATION_FLOOR_PCT) / span)


def _component_scores(f: LeadFeatures) -> dict[str, float]:
    volume = _clamp(math.log1p(max(0, f.total_shipments)) / VOLUME_LOG_CAP)
    hs_fit = _clamp(f.hs_overlap_ratio)
    reachability = (
        0.5 * (1.0 if f.has_email else 0.0)
        + 0.5 * _clamp(f.contact_count / CONTACT_SATURATION)
    )
    seniority = 1.0 if f.has_senior_contact else SENIORITY_BASELINE

    # Recency: signed signal — fresh good, stale bad, missing neutral
    recency = _recency_signal(f.days_since_recent)

    # Demand validated: binary positive signal. Presence of known
    # competitor edges proves a qualified buyer; absence is treated as
    # "no data" not "no demand".
    demand_validated = 1.0 if f.competitor_count > 0 else 0.0

    # Concentration: signed magnitude in [0, 1]; weight is negative, so a
    # high share contributes a negative term to the composite. None / zero
    # share data → 0 contribution.
    concentration = _concentration_signal(f.max_competitor_share_pct)

    return {
        "hs_fit": hs_fit,
        "volume": volume,
        "reachability": reachability,
        "seniority": seniority,
        "demand_validated": demand_validated,
        "recency": recency,
        "concentration": concentration,
    }


def score_lead(f: LeadFeatures) -> tuple[float, dict[str, float]]:
    """Return (composite_score, component_breakdown).

    Composite is clamped to [0, 1]; breakdown values are pre-weight
    contributions (may be negative for `recency`; `concentration` is
    always ≥ 0 but its weight is negative).

    `not_interested` leads are NOT filtered — they're scored with a 0.4×
    multiplier so a high-signal previously-declined lead can still
    surface above weaker fresh leads (re-engagement case).
    """
    components = _component_scores(f)
    composite = sum(WEIGHTS[k] * v for k, v in components.items())
    if f.hs_overlap_count == 0 and f.lead_hs_codes:
        composite *= HS_MISMATCH_PENALTY
    if f.is_not_interested:
        composite *= NOT_INTERESTED_PENALTY
    return _clamp(composite), components


def top_driver(components: dict[str, float]) -> str:
    """Return the component name with the highest weighted contribution.

    Penalty signals (negative weights) and zero-weighted components are
    excluded so the returned driver always identifies what's lifting the
    score, not what's dragging it down. The penalty side can be surfaced
    separately by `top_penalty`.
    """
    weighted = {
        k: WEIGHTS[k] * v
        for k, v in components.items()
        if WEIGHTS.get(k, 0) > 0
    }
    return max(weighted, key=weighted.get) if weighted else "none"


def top_penalty(components: dict[str, float]) -> str | None:
    """Return the negative-contribution component pulling the score down
    most, or None if no penalty is active. Symmetric to top_driver."""
    weighted = {
        k: WEIGHTS[k] * v
        for k, v in components.items()
        if WEIGHTS.get(k, 0) < 0 or (WEIGHTS.get(k, 0) > 0 and v < 0)
    }
    if not weighted:
        return None
    worst_key = min(weighted, key=weighted.get)
    return worst_key if weighted[worst_key] < 0 else None
