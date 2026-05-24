"""Unit tests for ``_factuality_check`` — the LLM rationale guardrail.

Brief weights grounding heavily. The factuality check is the last line of
defense before LLM-generated text reaches the operator. These tests pin
its behavior so it doesn't quietly drift.

Each test builds a minimal LeadFeatures fixture (no DB needed) and asserts
the rejection / acceptance verdict on a sample rationale string.
"""
from __future__ import annotations

from app.ranking.features import LeadFeatures
from app.ranking.rationale import _factuality_check


def _make_lead(
    *,
    company: str = "Test Importer Inc",
    hs_codes: list[str] | None = None,
    ports: list[str] | None = None,
    competitor: str | None = None,
    competitor_share: float | None = None,
    senior_title: str | None = None,
    total_shipments: int = 100,
    matching_shipments: int = 100,
    days_since_recent: int | None = 30,
) -> LeadFeatures:
    """Build a minimal LeadFeatures stand-in for factuality tests."""
    return LeadFeatures(
        lead_id="L-TEST",
        company=company,
        status="synced_to_crm",
        total_shipments=total_shipments,
        matching_shipments=matching_shipments,
        days_since_recent=days_since_recent,
        most_recent_shipment=None,
        lead_hs_codes=hs_codes or [],
        top_ports=ports or [],
        hs_overlap=hs_codes or [],   # not used by factuality
        hs_overlap_count=len(hs_codes or []),
        hs_overlap_ratio=1.0,
        competitor_count=1 if competitor else 0,
        top_competitor_name=competitor,
        max_competitor_share_pct=competitor_share,
        contact_count=1,
        has_email=True,
        has_senior_contact=bool(senior_title),
        senior_contact_title=senior_title,
        senior_contact_name=None,
        is_not_interested=False,
        is_synced_to_crm=True,
    )


# ---------- valid citations pass ----------

def test_valid_hs_citation_passes():
    """Rationale citing an HS code the lead actually has → pass."""
    f = _make_lead(hs_codes=["950510", "950300"])
    text = "Strong fit on HS 950510 with 100 matching shipments."
    fc = _factuality_check(text, f)
    assert fc.ok is True
    assert "950510" in fc.cited_hs_codes
    assert fc.invalid_hs_codes == []


def test_valid_port_citation_passes():
    """Rationale citing a port the lead actually ships through → pass."""
    f = _make_lead(hs_codes=["950510"], ports=["Long Beach", "Oakland"])
    text = "Importer routes via Long Beach with 100 shipments on file."
    fc = _factuality_check(text, f)
    assert fc.ok is True
    assert "Long Beach" in fc.cited_ports


def test_valid_competitor_citation_passes():
    """Rationale citing the lead's known top competitor → pass."""
    f = _make_lead(hs_codes=["950510"], competitor="Acme Holdings Ltd")
    text = "Already buying from Acme Holdings Ltd; switch story is on pricing."
    fc = _factuality_check(text, f)
    assert fc.ok is True
    assert "Acme Holdings Ltd" in fc.cited_competitors


# ---------- hallucinated HS code rejected ----------

def test_invalid_hs_citation_rejected():
    """Rationale citing an HS code NOT in the lead's hs_codes → reject.

    This is the canonical hallucination case the factuality check exists for.
    """
    f = _make_lead(hs_codes=["950510"])
    text = "Strong fit on HS 850710 with high shipment volume."  # 850710 NOT in list
    fc = _factuality_check(text, f)
    assert fc.ok is False
    assert "850710" in fc.invalid_hs_codes
    assert fc.reason is not None
    assert "850710" in fc.reason


def test_mixed_valid_and_invalid_hs_codes_rejected():
    """One good HS + one bad HS → entire rationale rejected."""
    f = _make_lead(hs_codes=["950510"])
    text = "Fit on HS 950510 and HS 850710 — strong activity."
    fc = _factuality_check(text, f)
    assert fc.ok is False
    assert fc.invalid_hs_codes == ["850710"]


# ---------- no-anchor rejection ----------

def test_no_anchor_pure_prose_rejected():
    """Rationale with NO specific fact cited → reject as ungrounded.

    The brief weights grounding heavily; pure prose like 'looks like a
    great lead' must fail even if the words happen to all be valid English.
    """
    f = _make_lead(hs_codes=["950510"], ports=["Long Beach"], total_shipments=100)
    text = "This importer looks like a promising opportunity for outreach."
    fc = _factuality_check(text, f)
    assert fc.ok is False
    assert fc.has_anchor is False
    assert fc.reason is not None and "no specific anchor" in fc.reason


# ---------- numeric BOL anchor (no-signal leads can pass) ----------

def test_numeric_bol_anchor_passes_for_zero_signal_lead():
    """A lead with no positive anchor data (no HS, no ports, no competitor,
    no senior title) can still pass if the rationale honestly cites the
    numeric BOL metrics it DOES have. Without this rule, truthful 'no
    activity' rationales get rejected.
    """
    f = _make_lead(
        hs_codes=[], ports=[], competitor=None,
        total_shipments=0, matching_shipments=0, days_since_recent=None,
    )
    text = "Zero matching shipments and no HS overlap with the factory profile."
    fc = _factuality_check(text, f)
    assert fc.ok is True


def test_numeric_metric_citation_passes():
    """Citing a literal `total_shipments=N` value → counts as a valid anchor."""
    f = _make_lead(
        hs_codes=[], ports=[], competitor=None,
        total_shipments=42, matching_shipments=42,
    )
    text = "Lead has 42 total_shipments on file — limited but worth a touch."
    fc = _factuality_check(text, f)
    assert fc.ok is True


# ---------- company-name fuzzy matching ----------

def test_company_name_subset_match_passes():
    """LLM dropping a corporate suffix is allowed via token-set containment.

    Example from real data: lead 'Stale Perfect Decor Co' cited as just
    'Stale Perfect Decor'. Should pass because tokens are a subset.
    """
    f = _make_lead(
        company="Stale Perfect Decor Co",
        hs_codes=["950510"],
    )
    text = "Stale Perfect Decor matches HS 950510 with strong shipment volume."
    fc = _factuality_check(text, f)
    assert fc.ok is True


def test_company_name_typo_rejected():
    """The README's flagship example: 'Pearhead Inc' subtly typo'd to
    'Pearson Inc' is hard-rejected, even though the rationale also cites
    a valid HS code. Token-set containment catches this — {Pearson, Inc}
    shares only the corporate suffix with {Pearhead, Inc}, so no candidate
    fuzzy-match accepts it.
    """
    f = _make_lead(
        company="Pearhead Inc",
        hs_codes=["950510"],
    )
    text = "Pearson Inc matches HS 950510 — strong volume signal."
    fc = _factuality_check(text, f)
    assert fc.ok is False
    assert "Pearson" in fc.invalid_companies
    assert fc.reason is not None and "Pearson" in fc.reason


# ---------- title anchor ----------

def test_senior_title_citation_passes():
    """Citing the lead's senior contact title → valid anchor on its own."""
    f = _make_lead(
        hs_codes=[],
        senior_title="VP of Procurement",
    )
    text = "Direct path to a VP of Procurement; worth the touch."
    fc = _factuality_check(text, f)
    assert fc.ok is True
    assert "VP of Procurement" in fc.cited_titles
