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
    bol_suppliers_json: Mapped[str | None] = mapped_column(Text)
    bol_payload_json: Mapped[str | None] = mapped_column(Text)
    bol_recent_json: Mapped[str | None] = mapped_column(Text)


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
    legacy_score: Mapped[int | None] = mapped_column(Integer)  # brief: do not use as input
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
    hs_codes_pg_array: Mapped[str | None] = mapped_column(Text)  # pg array literal, parse later
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
