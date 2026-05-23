"""LLM rationale layer — STUB.

Owned by a separate Claude Code session. This file is the single boundary
between deterministic ranking and any LLM call. Replace `rationalize` with
an implementation that returns a 1-2 sentence rationale citing literal
facts from `features`.

Contract:
  - Input: a fully-populated LeadFeatures and the component breakdown
    (already computed by score.score_lead). No DB access needed.
  - Output: plain string (no JSON, no markdown). 1-2 sentences.
  - MUST cite at least one of: hs_overlap, total_shipments,
    top_competitor_name, senior_contact_title, days_since_recent.
  - MUST NOT invent facts not present in `features`.
  - Caller (router) handles caching, retries, and error fallback.

Until replaced, returns a short deterministic placeholder built from the
top driver so the frontend still has something to display.
"""
from __future__ import annotations

from .features import LeadFeatures
from .score import top_driver

_DRIVER_BLURB = {
    "volume":       "high shipment volume",
    "recency":      "recent shipping activity",
    "hs_fit":       "HS-code overlap with factory profile",
    "competitive":  "low competitor pressure",
    "reachability": "reachable contacts",
    "seniority":    "decision-maker contact present",
}


def rationalize(features: LeadFeatures, components: dict[str, float]) -> str:
    driver = top_driver(components)
    blurb = _DRIVER_BLURB.get(driver, driver)
    return f"[stub] Top signal: {blurb}. (LLM rationale not wired yet.)"
