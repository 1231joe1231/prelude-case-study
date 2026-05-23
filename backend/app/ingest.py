"""Read 3 CSVs from backend/input/ into SQLite.

Source CSVs have no header row. Column lists below match the data layout
verified during inventory. Always-blank source columns are loaded as
placeholders then dropped before insert.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from .models import Competitor, Lead, Personnel

log = logging.getLogger("uvicorn.error")

INPUT_DIR = Path(__file__).resolve().parent.parent / "input"

# index -> name. Placeholder names ("_blank_N") mark source columns that are
# always empty across the file; they are dropped before insert.
LEADS_COLS = [
    "id", "company_name", "city_state", "data_source",
    "_blank_4", "_blank_5", "_blank_6", "_blank_7",
    "website", "status", "legacy_score", "created_at", "updated_at",
    "is_test", "bol_suppliers_json", "bol_payload_json", "bol_recent_json",
]

PERSONNEL_COLS = [
    "id", "first_name", "last_name", "full_name", "company_name", "data_source",
    "job_title", "_blank_7", "_blank_8", "email", "phone",
    "_blank_11", "_blank_12", "_blank_13",
    "lead_id", "legacy_score", "is_test", "created_at", "updated_at", "_blank_19",
]

COMPETITORS_COLS = [
    "id", "slug", "company_name", "company_name_cn", "country", "country_code",
    "addresses_text", "city", "hs_codes_pg_array", "_blank_9",
    "importer_contact_count", "_blank_11", "_blank_12",
    "revenue_or_volume_usd", "metadata_json", "products_text_pg_array",
    "count_shipments", "logistics_metadata", "risk_rating", "flag_count",
    "latitude_or_coord", "shipment_history_json", "us_customer_list_json",
    "supplier_variants_pg_array", "recent_shipment_bills_json",
    "logistics_partners_json", "is_archived", "_blank_27",
    "created_at", "updated_at",
]

DATETIME_COLS = {"created_at", "updated_at"}
BOOL_COLS = {"is_test", "is_archived"}
INT_COLS = {"legacy_score", "importer_contact_count", "count_shipments", "flag_count"}
FLOAT_COLS = {"revenue_or_volume_usd", "latitude_or_coord"}


def _sanitize(v):
    # Strip lone surrogates / invalid UTF-8 so FastAPI JSON encoding stays clean.
    if isinstance(v, str):
        return v.encode("utf-8", errors="replace").decode("utf-8")
    return v


def _read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        header=None,
        names=columns,
        dtype=str,
        na_filter=False,
        keep_default_na=False,
        encoding="utf-8",
        encoding_errors="replace",
    )
    df = df.drop(columns=[c for c in df.columns if c.startswith("_blank_")])
    df = df.map(_sanitize)
    # empty string -> None
    df = df.where(df != "", None)
    # type coercions
    for col in df.columns:
        if col in DATETIME_COLS:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
            df[col] = df[col].astype(object).where(df[col].notna(), None)
        elif col in BOOL_COLS:
            df[col] = df[col].map(lambda v: None if v is None else (str(v).strip().lower() == "true"))
        elif col in INT_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
            df[col] = df[col].astype(object).where(df[col].notna(), None)
        elif col in FLOAT_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].astype(object).where(df[col].notna(), None)
        else:
            df[col] = df[col].map(lambda v: v.strip() if isinstance(v, str) else v)
    return df


def _records(df: pd.DataFrame) -> list[dict]:
    out = df.to_dict(orient="records")
    # pandas returns NaT/NaN for missing; replace with None
    for r in out:
        for k, v in list(r.items()):
            if v is pd.NaT or (isinstance(v, float) and pd.isna(v)):
                r[k] = None
    return out


def ingest_all(session: Session) -> dict[str, int]:
    leads_df = _read_csv(INPUT_DIR / "leads.csv", LEADS_COLS)
    personnel_df = _read_csv(INPUT_DIR / "personnel.csv", PERSONNEL_COLS)
    competitors_df = _read_csv(INPUT_DIR / "bol_competitors.csv", COMPETITORS_COLS)

    session.bulk_insert_mappings(Lead, _records(leads_df))
    session.bulk_insert_mappings(Personnel, _records(personnel_df))
    session.bulk_insert_mappings(Competitor, _records(competitors_df))
    session.commit()

    counts = {
        "leads": len(leads_df),
        "personnel": len(personnel_df),
        "competitors": len(competitors_df),
    }
    log.info("ingested %s", counts)
    return counts
