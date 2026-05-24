"""Read 3 CSVs from backend/input/ into SQLite + flatten nested BOL/competitor payloads.

Schema overview (see models.py for exact columns):

  leads / personnel / competitors    parent tables (1 row per source CSV row)
  lead_attributes                    M:1 per-lead atomic facts (hs_code | port | top_product)
  competitor_attributes              M:1 per-competitor atomic facts (hs_code | alias)
  shipments                          bipartite graph (exporter→importer), edges deduped
                                     across 3 source JSONs with normalized-name FK resolution

Source CSVs have no header row. Column lists below match the data layout
verified during inventory. Always-blank source columns are loaded as
placeholders then dropped before insert.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from .models import (
    Competitor,
    CompetitorAttribute,
    Lead,
    LeadAttribute,
    Personnel,
    Shipment,
)

log = logging.getLogger("uvicorn.error")

INPUT_DIR = Path(__file__).resolve().parent.parent / "input"

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


# ---------- generic helpers ----------

def _sanitize(v):
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
    df = df.where(df != "", None)
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
    for r in out:
        for k, v in list(r.items()):
            if v is pd.NaT or (isinstance(v, float) and pd.isna(v)):
                r[k] = None
    return out


# ---------- payload parsers ----------

def _parse_json(s: Any) -> Any:
    """Decode JSON, transparently unwrap double-encoded strings."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        out = json.loads(s)
    except (ValueError, TypeError):
        return None
    if isinstance(out, str):
        stripped = out.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(out)
            except (ValueError, TypeError):
                return None
    return out


_PG_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([^,{}]+)')


def _parse_pg_array(s: Any) -> list[str]:
    if not isinstance(s, str):
        return []
    s = s.strip()
    if not s or s == "{}" or not (s.startswith("{") and s.endswith("}")):
        return []
    inner = s[1:-1]
    out: list[str] = []
    for m in _PG_TOKEN.finditer(inner):
        q, u = m.group(1), m.group(2)
        if q is not None:
            out.append(q.replace('\\"', '"'))
        else:
            t = u.strip()
            if t:
                out.append(t)
    return out


def _parse_dt(s: Any) -> Any:
    if not s:
        return None
    try:
        ts = pd.to_datetime(s, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


# ---------- name normalization for FK resolution ----------

_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c\.|ltd|limited|co|corp|corporation|company|group|holdings?|"
    r"co\.?,?\s*ltd\.?|gmbh|sa|s\.a\.|plc|llp|pty)\.?\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_name(s: Any) -> str:
    """Lowercase, strip corporate suffixes & punct, collapse whitespace.

    Used only for FK resolution; raw names preserved on each row.
    """
    if not isinstance(s, str):
        return ""
    s = s.lower()
    s = _SUFFIX_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


# ---------- per-lead BOL flattening ----------

def _flatten_lead(lead_row: dict) -> tuple[dict, list[dict], list[dict]]:
    """Return (lead_scalar_updates, attribute_rows, supplier_edge_rows).

    Reads bol_payload_json + bol_recent_json. Updates scalars on the lead,
    emits lead_attributes rows for arrays, returns raw shipment edges (one
    per supplier mentioned) which will be deduped against competitor-side
    edges later.
    """
    lead_id = lead_row["id"]
    importer_name = lead_row.get("company_name") or ""

    scalar_updates = {
        "total_shipments": None,
        "matching_shipments": None,
        "most_recent_shipment": None,
        "growth_12m_pct": None,
        "china_concentration": None,
    }
    attrs: list[dict] = []
    edges: list[dict] = []

    payload = _parse_json(lead_row.get("bol_payload_json"))
    if isinstance(payload, dict):
        scalar_updates["total_shipments"] = _int(payload.get("totalShipments"))
        scalar_updates["matching_shipments"] = _int(payload.get("matchingShipments"))
        scalar_updates["most_recent_shipment"] = _parse_dt(payload.get("mostRecentShipment"))

        for rank, code in enumerate(payload.get("hsCodes") or []):
            if isinstance(code, str) and code.strip():
                attrs.append({"lead_id": lead_id, "kind": "hs_code", "value": code.strip(), "rank": rank})
        for rank, port in enumerate(payload.get("topPorts") or []):
            if isinstance(port, str) and port.strip():
                attrs.append({"lead_id": lead_id, "kind": "port", "value": port.strip(), "rank": rank})
        for rank, product in enumerate(payload.get("topProducts") or []):
            if isinstance(product, str) and product.strip():
                attrs.append({"lead_id": lead_id, "kind": "top_product", "value": product.strip(), "rank": rank})

        for sup in payload.get("topSuppliers") or []:
            if isinstance(sup, str) and sup.strip():
                edges.append({
                    "source_tag": "lead_payload",
                    "importer_name": importer_name,
                    "exporter_name": sup.strip(),
                })
            elif isinstance(sup, dict) and sup.get("name"):
                edges.append({
                    "source_tag": "lead_payload",
                    "importer_name": importer_name,
                    "exporter_name": str(sup["name"]).strip(),
                    "teu": _num(sup.get("teu")),
                    "share_pct": _num(sup.get("share")),
                    "trend_pct": _num(sup.get("trend")),
                })

    recent = _parse_json(lead_row.get("bol_recent_json"))
    if isinstance(recent, dict):
        scalar_updates["growth_12m_pct"] = _num(recent.get("growth12mPct"))
        scalar_updates["china_concentration"] = _num(recent.get("chinaConcentration"))

    suppliers = _parse_json(lead_row.get("bol_suppliers_json"))
    if isinstance(suppliers, dict):
        for sup in suppliers.get("suppliers") or []:
            if isinstance(sup, dict) and sup.get("name"):
                edges.append({
                    "source_tag": "lead_suppliers",
                    "importer_name": importer_name,
                    "exporter_name": str(sup["name"]).strip(),
                    "teu": _num(sup.get("teu")),
                    "share_pct": _num(sup.get("share")),
                    "trend_pct": _num(sup.get("trend")),
                })

    return scalar_updates, attrs, edges


# ---------- per-competitor flattening ----------

def _flatten_competitor(comp_row: dict) -> tuple[list[dict], list[dict]]:
    """Return (attribute_rows, customer_edge_rows)."""
    comp_id = comp_row["id"]
    exporter_name = comp_row.get("company_name") or ""

    attrs: list[dict] = []
    edges: list[dict] = []

    for code in _parse_pg_array(comp_row.get("hs_codes_pg_array")):
        attrs.append({"competitor_id": comp_id, "kind": "hs_code", "value": code})

    for alias in _parse_pg_array(comp_row.get("supplier_variants_pg_array")):
        if alias and alias != exporter_name:
            attrs.append({"competitor_id": comp_id, "kind": "alias", "value": alias})

    customers = _parse_json(comp_row.get("us_customer_list_json"))
    if isinstance(customers, list):
        for c in customers:
            if not isinstance(c, dict):
                continue
            name = c.get("company_name") or c.get("companyName")
            if not name:
                continue
            edges.append({
                "source_tag": "competitor_customers",
                "importer_name": str(name).strip(),
                "exporter_name": exporter_name,
                "teu": _num(c.get("total_teus")),
                "total_shipments": _int(c.get("total_shipments_supplier")),
                "shipments_12m": _int(c.get("shipments_12m")),
                "share_pct": _num(c.get("shipments_percents_supplier")),
                "most_recent_shipment": _parse_dt(c.get("most_recent_shipment")),
            })

    return attrs, edges


# ---------- shipment-graph dedup + FK resolution ----------

def _build_resolver_maps(
    lead_records: list[dict],
    competitor_records: list[dict],
    competitor_attrs: list[dict],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (importer_resolver, exporter_resolver): normalized_name → parent_id."""
    importer_map: dict[str, str] = {}
    for lr in lead_records:
        key = normalize_name(lr.get("company_name"))
        if key:
            importer_map.setdefault(key, lr["id"])

    exporter_map: dict[str, str] = {}
    for cr in competitor_records:
        key = normalize_name(cr.get("company_name"))
        if key:
            exporter_map.setdefault(key, cr["id"])
    # Aliases also resolve to the same competitor
    for ca in competitor_attrs:
        if ca["kind"] == "alias":
            key = normalize_name(ca["value"])
            if key:
                exporter_map.setdefault(key, ca["competitor_id"])

    return importer_map, exporter_map


def _dedup_edges(
    raw_edges: list[dict],
    importer_map: dict[str, str],
    exporter_map: dict[str, str],
) -> list[dict]:
    """Merge edges with same (normalized importer, normalized exporter) across sources."""
    merged: dict[tuple[str, str], dict] = {}

    for e in raw_edges:
        imp_key = normalize_name(e["importer_name"])
        exp_key = normalize_name(e["exporter_name"])
        if not imp_key or not exp_key:
            continue
        key = (imp_key, exp_key)

        if key not in merged:
            merged[key] = {
                "importer_lead_id": importer_map.get(imp_key),
                "exporter_competitor_id": exporter_map.get(exp_key),
                "importer_name": e["importer_name"],
                "exporter_name": e["exporter_name"],
                "teu": None,
                "total_shipments": None,
                "shipments_12m": None,
                "share_pct": None,
                "trend_pct": None,
                "most_recent_shipment": None,
                "seen_in_lead_payload": False,
                "seen_in_lead_suppliers": False,
                "seen_in_competitor_customers": False,
            }

        row = merged[key]
        tag = e.get("source_tag", "")
        if tag == "lead_payload":
            row["seen_in_lead_payload"] = True
        elif tag == "lead_suppliers":
            row["seen_in_lead_suppliers"] = True
        elif tag == "competitor_customers":
            row["seen_in_competitor_customers"] = True

        # Merge numeric metrics by max (each source may provide a different number)
        for k in ("teu", "total_shipments", "shipments_12m", "share_pct", "trend_pct"):
            v = e.get(k)
            if v is not None and (row[k] is None or v > row[k]):
                row[k] = v
        # Most recent shipment: latest wins
        ts = e.get("most_recent_shipment")
        if ts is not None and (row["most_recent_shipment"] is None or ts > row["most_recent_shipment"]):
            row["most_recent_shipment"] = ts

    return list(merged.values())


# ---------- main ----------

def ingest_all(session: Session) -> dict[str, int]:
    leads_df = _read_csv(INPUT_DIR / "leads.csv", LEADS_COLS)
    personnel_df = _read_csv(INPUT_DIR / "personnel.csv", PERSONNEL_COLS)
    competitors_df = _read_csv(INPUT_DIR / "bol_competitors.csv", COMPETITORS_COLS)

    lead_records = _records(leads_df)
    personnel_records = _records(personnel_df)
    competitor_records = _records(competitors_df)

    # Flatten leads
    lead_attrs: list[dict] = []
    raw_edges: list[dict] = []
    for r in lead_records:
        scalars, attrs, edges = _flatten_lead(r)
        r.update(scalars)
        lead_attrs.extend(attrs)
        raw_edges.extend(edges)

    # Flatten competitors
    competitor_attrs: list[dict] = []
    for r in competitor_records:
        attrs, edges = _flatten_competitor(r)
        competitor_attrs.extend(attrs)
        raw_edges.extend(edges)

    # Resolve & dedup shipment edges
    importer_map, exporter_map = _build_resolver_maps(
        lead_records, competitor_records, competitor_attrs
    )
    shipment_rows = _dedup_edges(raw_edges, importer_map, exporter_map)

    # Insert parents first
    session.bulk_insert_mappings(Lead, lead_records)
    session.bulk_insert_mappings(Personnel, personnel_records)
    session.bulk_insert_mappings(Competitor, competitor_records)
    session.flush()

    session.bulk_insert_mappings(LeadAttribute, lead_attrs)
    session.bulk_insert_mappings(CompetitorAttribute, competitor_attrs)
    session.bulk_insert_mappings(Shipment, shipment_rows)
    session.commit()

    resolved_importers = sum(1 for r in shipment_rows if r["importer_lead_id"])
    resolved_exporters = sum(1 for r in shipment_rows if r["exporter_competitor_id"])
    resolved_both = sum(
        1 for r in shipment_rows
        if r["importer_lead_id"] and r["exporter_competitor_id"]
    )

    counts = {
        "leads": len(lead_records),
        "personnel": len(personnel_records),
        "competitors": len(competitor_records),
        "lead_attributes": len(lead_attrs),
        "competitor_attributes": len(competitor_attrs),
        "shipments": len(shipment_rows),
        "shipments_resolved_importer": resolved_importers,
        "shipments_resolved_exporter": resolved_exporters,
        "shipments_resolved_both": resolved_both,
    }
    log.info("ingested %s", counts)
    return counts
