from datetime import datetime
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


# Source CSVs have no header row. Column names below are inferred from data.
# Always-blank source columns are dropped at ingest time and not modeled here.
# See backend/app/ingest.py for the index→name mapping used during load.


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_name: Mapped[str | None] = mapped_column(Text)
    city_state: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(String(64))
    website: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(32), index=True)
    legacy_score: Mapped[int | None] = mapped_column(Integer)  # brief: do not use as input
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_test: Mapped[bool | None] = mapped_column(Boolean)
    # Flattened BOL scalars (parsed from bol_payload_json / bol_recent_json at ingest)
    total_shipments: Mapped[int | None] = mapped_column(Integer)
    matching_shipments: Mapped[int | None] = mapped_column(Integer)
    most_recent_shipment: Mapped[datetime | None] = mapped_column(DateTime)
    growth_12m_pct: Mapped[float | None] = mapped_column(Float)         # bol_recent_json.growth12mPct
    china_concentration: Mapped[float | None] = mapped_column(Float)    # bol_recent_json.chinaConcentration
    # Raw JSON payloads kept as audit trail; arrays normalized into child tables below
    bol_suppliers_json: Mapped[str | None] = mapped_column(Text)
    bol_payload_json: Mapped[str | None] = mapped_column(Text)
    bol_recent_json: Mapped[str | None] = mapped_column(Text)


class LeadAttribute(Base):
    """Per-lead M:1 atomic facts parsed from bol_payload_json arrays.

    kind ∈ {'hs_code', 'port', 'top_product'}
    rank preserves source-array order (0-indexed).
    """
    __tablename__ = "lead_attributes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String(36), ForeignKey("leads.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    value: Mapped[str] = mapped_column(Text, index=True)
    rank: Mapped[int | None] = mapped_column(Integer)


class Personnel(Base):
    __tablename__ = "personnel"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text)
    company_name: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(String(64))
    job_title: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text, index=True)
    phone: Mapped[str | None] = mapped_column(Text)
    lead_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("leads.id"), index=True)
    legacy_score: Mapped[int | None] = mapped_column(Integer)
    is_test: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class Competitor(Base):
    __tablename__ = "competitors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str | None] = mapped_column(Text)
    company_name: Mapped[str | None] = mapped_column(Text)
    company_name_cn: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(64))
    country_code: Mapped[str | None] = mapped_column(String(8))
    addresses_text: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    hs_codes_pg_array: Mapped[str | None] = mapped_column(Text)
    importer_contact_count: Mapped[int | None] = mapped_column(Integer)
    revenue_or_volume_usd: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    products_text_pg_array: Mapped[str | None] = mapped_column(Text)
    count_shipments: Mapped[int | None] = mapped_column(Integer)
    logistics_metadata: Mapped[str | None] = mapped_column(Text)
    risk_rating: Mapped[str | None] = mapped_column(String(16))
    flag_count: Mapped[int | None] = mapped_column(Integer)
    latitude_or_coord: Mapped[float | None] = mapped_column(Float)
    shipment_history_json: Mapped[str | None] = mapped_column(Text)
    us_customer_list_json: Mapped[str | None] = mapped_column(Text)
    supplier_variants_pg_array: Mapped[str | None] = mapped_column(Text)
    recent_shipment_bills_json: Mapped[str | None] = mapped_column(Text)
    logistics_partners_json: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class CompetitorAttribute(Base):
    """Per-competitor M:1 atomic facts.

    kind ∈ {'hs_code', 'alias'}
      - 'hs_code' from hs_codes_pg_array
      - 'alias'   from supplier_variants_pg_array (alternate names for the same competitor;
                  used at ingest time to resolve shipment-graph FKs)
    """
    __tablename__ = "competitor_attributes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    competitor_id: Mapped[str] = mapped_column(String(36), ForeignKey("competitors.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    value: Mapped[str] = mapped_column(Text, index=True)


class Shipment(Base):
    """The bipartite shipping graph: exporter (Chinese factory) → importer (US company).

    One row per deduplicated edge across 3 source JSONs:
      - lead.bol_payload_json.topSuppliers       (lead-side, summary)
      - lead.bol_suppliers_json.suppliers        (lead-side, enriched)
      - competitor.us_customer_list_json         (competitor-side, enriched)

    FKs are populated when normalized-name matches a known parent row;
    otherwise left null and raw names preserved for later resolution.
    """
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Resolved foreign keys (NULL when name didn't match a known parent)
    importer_lead_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("leads.id"), index=True)
    exporter_competitor_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("competitors.id"), index=True)
    # Raw names (always populated)
    importer_name: Mapped[str] = mapped_column(Text, index=True)
    exporter_name: Mapped[str] = mapped_column(Text, index=True)
    # Merged metrics (max across sources where each provides them)
    teu: Mapped[float | None] = mapped_column(Float)
    total_shipments: Mapped[int | None] = mapped_column(Integer)
    shipments_12m: Mapped[int | None] = mapped_column(Integer)
    share_pct: Mapped[float | None] = mapped_column(Float)          # supplier's share of importer's shipments
    trend_pct: Mapped[float | None] = mapped_column(Float)          # 12m supplier trend
    most_recent_shipment: Mapped[datetime | None] = mapped_column(DateTime)
    # Source flags — which JSONs surfaced this edge
    seen_in_lead_payload: Mapped[bool] = mapped_column(Boolean, default=False)
    seen_in_lead_suppliers: Mapped[bool] = mapped_column(Boolean, default=False)
    seen_in_competitor_customers: Mapped[bool] = mapped_column(Boolean, default=False)
