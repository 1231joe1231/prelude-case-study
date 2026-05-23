"""Per-lead feature extraction.

Pure functions: take a db Session + persona, return dataclasses. No LLM, no
network. Each feature is a literal fact derivable from the source data so
that downstream rationale generation can cite the exact value.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Lead, LeadAttribute, Personnel, Shipment
from .persona import Persona

SENIOR_TITLE_RE = re.compile(
    r"\b(ceo|cfo|coo|cto|cio|president|founder|owner|partner|vp|vice\s*president|"
    r"director|head|chief|principal|managing|general\s*manager|manager|"
    r"procurement|sourcing|buyer|purchasing|supply)\b",
    re.IGNORECASE,
)


@dataclass
class LeadFeatures:
    # Identity
    lead_id: str
    company: str
    status: str | None

    # Volume + recency
    total_shipments: int
    matching_shipments: int
    days_since_recent: int | None
    most_recent_shipment: datetime | None

    # Product fit
    lead_hs_codes: list[str] = field(default_factory=list)
    top_ports: list[str] = field(default_factory=list)
    hs_overlap: list[str] = field(default_factory=list)
    hs_overlap_count: int = 0
    hs_overlap_ratio: float = 0.0          # |overlap| / |lead_hs_codes|

    # Competitive pressure
    competitor_count: int = 0              # distinct exporters shipping to this lead
    top_competitor_name: str | None = None
    max_competitor_share_pct: float | None = None

    # Reachability
    contact_count: int = 0
    has_email: bool = False
    has_senior_contact: bool = False
    senior_contact_title: str | None = None
    senior_contact_name: str | None = None

    # Filter flags
    is_not_interested: bool = False
    is_synced_to_crm: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.most_recent_shipment is not None:
            d["most_recent_shipment"] = self.most_recent_shipment.isoformat()
        return d


def _days_since(ts: datetime | None) -> int | None:
    if ts is None:
        return None
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    return max(0, delta.days)


def _lead_hs_and_ports(db: Session, lead_id: str) -> tuple[list[str], list[str]]:
    rows = db.execute(
        select(LeadAttribute.kind, LeadAttribute.value, LeadAttribute.rank)
        .where(LeadAttribute.lead_id == lead_id)
        .order_by(LeadAttribute.rank.asc().nullslast())
    ).all()
    hs = [v for k, v, _ in rows if k == "hs_code" and v]
    ports = [v for k, v, _ in rows if k == "port" and v]
    return hs, ports


def _competitor_stats(db: Session, lead_id: str) -> tuple[int, str | None, float | None]:
    rows = db.execute(
        select(Shipment.exporter_name, Shipment.share_pct)
        .where(Shipment.importer_lead_id == lead_id)
    ).all()
    if not rows:
        return 0, None, None
    distinct = {name for name, _ in rows if name}
    shares = [(name, s) for name, s in rows if s is not None]
    if shares:
        top_name, top_share = max(shares, key=lambda x: x[1])
        return len(distinct), top_name, top_share
    return len(distinct), next(iter(distinct), None), None


def _personnel_stats(db: Session, lead_id: str) -> tuple[int, bool, bool, str | None, str | None]:
    rows = db.execute(
        select(Personnel.full_name, Personnel.job_title, Personnel.email)
        .where(Personnel.lead_id == lead_id)
    ).all()
    if not rows:
        return 0, False, False, None, None
    has_email = any(e and "@" in e for _, _, e in rows)
    senior_title: str | None = None
    senior_name: str | None = None
    for name, title, _ in rows:
        if title and SENIOR_TITLE_RE.search(title):
            senior_title = title
            senior_name = name
            break
    return len(rows), has_email, senior_title is not None, senior_title, senior_name


def extract_features(db: Session, lead: Lead, persona: Persona) -> LeadFeatures:
    hs, ports = _lead_hs_and_ports(db, lead.id)
    overlap = [c for c in hs if c in persona.hs_codes]
    hs_ratio = (len(overlap) / len(hs)) if hs else 0.0

    comp_count, top_comp, max_share = _competitor_stats(db, lead.id)
    n_contacts, has_email, has_senior, senior_title, senior_name = _personnel_stats(db, lead.id)

    return LeadFeatures(
        lead_id=lead.id,
        company=lead.company_name or "",
        status=lead.status,
        total_shipments=lead.total_shipments or 0,
        matching_shipments=lead.matching_shipments or 0,
        days_since_recent=_days_since(lead.most_recent_shipment),
        most_recent_shipment=lead.most_recent_shipment,
        lead_hs_codes=hs,
        top_ports=ports,
        hs_overlap=overlap,
        hs_overlap_count=len(overlap),
        hs_overlap_ratio=hs_ratio,
        competitor_count=comp_count,
        top_competitor_name=top_comp,
        max_competitor_share_pct=max_share,
        contact_count=n_contacts,
        has_email=has_email,
        has_senior_contact=has_senior,
        senior_contact_title=senior_title,
        senior_contact_name=senior_name,
        is_not_interested=(lead.status == "not_interested"),
        is_synced_to_crm=(lead.status == "synced_to_crm"),
    )


def extract_all(db: Session, persona: Persona) -> list[LeadFeatures]:
    leads = db.scalars(select(Lead)).all()
    return [extract_features(db, lead, persona) for lead in leads]
