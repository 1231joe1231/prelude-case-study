"""Eval: golden-set ranking assertions.

Each test isolates one scenario from backend/scripts/build_golden.py. Failures
indicate a regression in a specific component of the pipeline:

  test_perfect_greenfield_is_top      → hs_fit + volume + reachability + seniority
  test_not_interested_strong_below_*  → not_interested penalty
  test_hs_mismatch_loses_to_*         → hs_fit weight is doing its job
  test_unreachable_loses_to_*         → reachability + seniority floor
  test_partial_match_in_middle        → hs_overlap_ratio gradient works
  test_contested_competitor_shipments → bipartite-graph FK resolution works

Run: pytest backend/tests -v
"""
from __future__ import annotations

import pytest


# ---------- helpers ----------

def _ranked(db_session):
    """Return [(lead_id, score, components, features), ...] sorted by score desc."""
    from app.ranking.features import extract_all
    from app.ranking.persona import infer_persona
    from app.ranking.score import score_lead

    persona = infer_persona(db_session)
    feats = extract_all(db_session, persona)
    out = []
    for f in feats:
        composite, comps = score_lead(f)
        out.append((f.lead_id, composite, comps, f))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _rank_of(ranked, lead_id):
    for i, (lid, *_rest) in enumerate(ranked, 1):
        if lid == lead_id:
            return i
    raise AssertionError(f"{lead_id} not in ranking")


def _score_of(ranked, lead_id):
    for lid, score, *_rest in ranked:
        if lid == lead_id:
            return score
    raise AssertionError(f"{lead_id} not in ranking")


def _features_of(ranked, lead_id):
    for lid, _s, _c, f in ranked:
        if lid == lead_id:
            return f
    raise AssertionError(f"{lead_id} not in ranking")


# ---------- ingest / shape tests ----------

def test_golden_dataset_loaded(db_session):
    """Confirm the 10-lead golden set is in the DB after the conftest fixture."""
    from sqlalchemy import select, func
    from app.models import Lead, Personnel, Competitor

    assert db_session.scalar(select(func.count()).select_from(Lead)) == 10
    assert db_session.scalar(select(func.count()).select_from(Personnel)) == 8
    assert db_session.scalar(select(func.count()).select_from(Competitor)) == 5


def test_persona_includes_christmas_codes(db_session):
    """The 5 golden competitors must aggregate into a Christmas-decoration persona."""
    from app.ranking.persona import infer_persona
    p = infer_persona(db_session)
    expected = {"950510", "950300", "940542", "940350", "060490"}
    assert expected.issubset(set(p.hs_codes)), (
        f"persona missing core codes: have {sorted(p.hs_codes)}"
    )


# ---------- scenario assertions ----------

def test_validated_demand_outranks_greenfield(db_session):
    """GOLD-002 (PERFECT_CONTESTED, 5 known competitors) must rank above
    GOLD-001 (PERFECT_GREENFIELD, 0 competitors) despite GOLD-001 having
    a slightly stronger raw signal (4/4 HS vs 3/3, 300 ships vs 250).

    The scoring reframe of the 'competitive' signal is the architectural
    bet here: presence of competitors PROVES this lead buys the product
    category from China, i.e. demand is validated. Zero-competitor leads
    might be greenfield, but they might also just be data gaps. Demand
    validation is worth the +0.10 weight it carries.

    A previous version of this test asserted GOLD-001 == #1; that
    assumption predates the demand_validated signal and is now wrong.
    """
    ranked = _ranked(db_session)
    rank_001 = _rank_of(ranked, "GOLD-001")
    rank_002 = _rank_of(ranked, "GOLD-002")
    assert rank_002 < rank_001, (
        f"demand-validated lead must outrank greenfield: "
        f"GOLD-002 is #{rank_002}, GOLD-001 is #{rank_001}"
    )
    # GOLD-002 should specifically be #1 on the golden set — nothing else
    # combines hs_fit=1.0, validated demand, low concentration, recent activity,
    # and reachable senior contact.
    assert rank_002 == 1, f"GOLD-002 should be #1, got #{rank_002}"


def test_dominant_competitor_penalized_vs_split_market(db_session):
    """GOLD-003 (DOMINANT_COMPETITOR, 1 supplier at 80% share) must rank
    BELOW GOLD-002 (5 competitors splitting volume).

    Both leads have validated demand (demand_validated=1) and strong HS
    overlap. The discriminator is concentration: a single locked-in
    supplier at 80% share triggers the negative concentration penalty,
    while a 5-way split has concentration ≈ 0. If this fails, the
    concentration signal isn't doing real work.
    """
    ranked = _ranked(db_session)
    assert _rank_of(ranked, "GOLD-002") < _rank_of(ranked, "GOLD-003"), (
        "split-market lead must outrank dominant-supplier lead"
    )


def test_not_interested_weak_is_bottom(db_session):
    """GOLD-010 (no signal + not_interested) must rank dead last."""
    ranked = _ranked(db_session)
    assert _rank_of(ranked, "GOLD-010") == 10


def test_not_interested_strong_penalized_below_eligible(db_session):
    """GOLD-009 has IDENTICAL signal to GOLD-001 but status=not_interested.

    The × 0.4 penalty must drop it below GOLD-001 (and below GOLD-002..004
    which also have strong signal), but it should STILL be ranked (not
    filtered out) so an operator can see a re-engagement candidate.
    """
    ranked = _ranked(db_session)
    s001 = _score_of(ranked, "GOLD-001")
    s009 = _score_of(ranked, "GOLD-009")
    assert s009 < s001 * 0.5, (
        f"penalty too weak: GOLD-001={s001:.3f}, GOLD-009={s009:.3f} "
        f"(ratio {s009/s001:.2%})"
    )
    # Must remain in the ranking, not dropped
    assert _rank_of(ranked, "GOLD-009") <= 10


def test_hs_mismatch_loses_to_partial_match(db_session):
    """GOLD-007 (0/4 HS, 400 ships, senior) must lose to GOLD-005 (2/5 HS, 80 ships).

    Brief explicitly weights HS-code fit highly. A high-volume importer in the
    wrong product category is worth less than a lower-volume importer in the
    factory's lane. If this fails, hs_fit weight is too low or volume is
    overweighted.
    """
    ranked = _ranked(db_session)
    assert _rank_of(ranked, "GOLD-005") < _rank_of(ranked, "GOLD-007"), (
        "partial-match must outrank high-volume HS-mismatch"
    )


def test_unreachable_loses_to_reachable_equivalent(db_session):
    """GOLD-008 (perfect HS, no contact, no email) must lose to GOLD-001
    (perfect HS, senior contact, email) — reachability + seniority matter."""
    ranked = _ranked(db_session)
    assert _rank_of(ranked, "GOLD-001") < _rank_of(ranked, "GOLD-008")
    assert _score_of(ranked, "GOLD-001") - _score_of(ranked, "GOLD-008") > 0.1, (
        "reachability gap should produce ≥0.1 score difference at full HS+volume parity"
    )


def test_top4_are_strong_synced_leads(db_session):
    """The top 4 must all be in {GOLD-001, 002, 003, 004} — the four
    strong-signal synced leads. Order within top-4 is allowed to flex; just
    no other lead should crash in."""
    ranked = _ranked(db_session)
    top4_ids = {lid for lid, *_ in ranked[:4]}
    expected = {"GOLD-001", "GOLD-002", "GOLD-003", "GOLD-004"}
    assert top4_ids == expected, f"top-4 was {top4_ids}, expected {expected}"


def test_contested_lead_has_competitor_edges(db_session):
    """GOLD-002 must have ≥3 distinct competitor edges resolved (one per
    competitor that lists it). Validates the shipments-graph FK resolution
    works end-to-end on golden data, not just on the real CSVs.
    """
    ranked = _ranked(db_session)
    f = _features_of(ranked, "GOLD-002")
    assert f.competitor_count >= 3, (
        f"expected ≥3 competitor edges for GOLD-002, got {f.competitor_count}"
    )


def test_dominant_competitor_share_resolved(db_session):
    """GOLD-003 must have max_competitor_share_pct ≈ 80% from COMP-A."""
    ranked = _ranked(db_session)
    f = _features_of(ranked, "GOLD-003")
    assert f.competitor_count == 1, f"expected 1 dominant edge, got {f.competitor_count}"
    assert f.max_competitor_share_pct is not None
    assert 70 <= f.max_competitor_share_pct <= 90, (
        f"expected ~80% share, got {f.max_competitor_share_pct}"
    )


# ---------- ablation: which components drive the ranking ----------

@pytest.mark.parametrize("component", ["hs_fit", "volume"])
def test_ablation_zeroing_component_changes_top5(db_session, component):
    """Zero out one weight at a time; the new top-5 must DIFFER from the
    baseline top-5. Validates the component is doing real discriminative work
    on the golden set, not just contributing uniform noise.

    Reachability is intentionally NOT in this list: every top-4 golden lead
    has identical reachability (email + 1 contact), so zeroing it shifts all
    of them by the same amount and ranking is unchanged. Reachability's
    discriminating power is covered by test_unreachable_loses_to_reachable_equivalent.
    """
    from app.ranking import score as score_mod
    from app.ranking.features import extract_all
    from app.ranking.persona import infer_persona

    p = infer_persona(db_session)
    feats = extract_all(db_session, p)

    baseline = sorted(feats, key=lambda f: score_mod.score_lead(f)[0], reverse=True)
    baseline_top = [f.lead_id for f in baseline[:5]]

    original_weight = score_mod.WEIGHTS[component]
    score_mod.WEIGHTS[component] = 0.0
    try:
        ablated = sorted(feats, key=lambda f: score_mod.score_lead(f)[0], reverse=True)
        ablated_top = [f.lead_id for f in ablated[:5]]
    finally:
        score_mod.WEIGHTS[component] = original_weight

    assert baseline_top != ablated_top, (
        f"zeroing {component!r} did not change top-5 — weight is dead on golden set"
    )
