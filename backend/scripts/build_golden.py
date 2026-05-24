"""Synthesize backend/input/golden/{leads,personnel,bol_competitors}.csv.

NOT picked from real data. Each lead is a hand-designed scenario that
exercises a specific code path in the ranking pipeline. Eval tests assert
the pipeline ranks them in the expected order.

Run:
    python -m backend.scripts.build_golden
    # or
    py -3.11 backend/scripts/build_golden.py

Re-run any time the schema or scenario set changes. Output overwrites existing
golden CSVs.

Scenarios (see eval tests for assertions):

  GOLD-001  PERFECT_GREENFIELD     4/4 HS, 300 ships, recent, senior, 0 competitors → expected #1
  GOLD-002  PERFECT_CONTESTED      3/3 HS, 250 ships, recent, senior, 5 competitors (split)
  GOLD-003  DOMINANT_COMPETITOR    3/3 HS, 180 ships, recent, senior, 1 competitor 80% share
  GOLD-004  STALE_PERFECT          4/4 HS, 150 ships, 2y ago, senior, 0 competitors
  GOLD-005  PARTIAL_MATCH          2/5 HS, 80 ships, recent, has email, no senior
  GOLD-006  NEW_GROWING            1/3 HS, 15 ships, recent, senior
  GOLD-007  HS_MISMATCH_HIGH_VOL   0/4 HS, 400 ships, recent, senior — wrong product
  GOLD-008  UNREACHABLE_PERFECT    4/4 HS, 200 ships, recent, NO email/contact
  GOLD-009  NOT_INTERESTED_STRONG  4/4 HS, 300 ships, recent, senior, status=not_interested
  GOLD-010  NOT_INTERESTED_WEAK    0/1 HS, 1 ship, old, no email, status=not_interested  → expected bottom

Competitors COMP-A..E aggregate HS codes to define a Christmas-decoration
persona (top-N includes 950510 950300 940542 940350 060490 ...).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

# Match ingest.py column orders exactly. Blank slots present as empty cells.
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

# ---------- helpers ----------

def pg_array(values: list[str]) -> str:
    """Postgres array literal: {a,b,c} with double-quoted values."""
    quoted = ['"' + v.replace('"', '\\"') + '"' for v in values]
    return "{" + ",".join(quoted) + "}"


def bol_payload(*, hs_codes: list[str], ports: list[str], products: list[str],
                top_suppliers: list[str], total_shipments: int,
                matching_shipments: int, most_recent: str) -> str:
    return json.dumps({
        "hsCodes": hs_codes,
        "topPorts": ports,
        "topProducts": products,
        "topSuppliers": top_suppliers,
        "totalShipments": total_shipments,
        "totalSuppliers": len(top_suppliers),
        "matchingShipments": matching_shipments,
        "mostRecentShipment": most_recent,
    })


def bol_recent(*, growth_12m_pct: float | None = None,
               china_concentration: float | None = None) -> str:
    return json.dumps({
        "growth12mPct": growth_12m_pct,
        "chinaConcentration": china_concentration,
    })


def empty_bol_payload() -> str:
    return json.dumps({
        "hsCodes": [], "topPorts": [], "topProducts": [], "topSuppliers": [],
        "totalShipments": 0, "totalSuppliers": 0, "matchingShipments": 0,
        "mostRecentShipment": None,
    })


# ---------- lead definitions ----------

ISO_RECENT = "2026-05-01T00:00:00Z"
ISO_OLD = "2024-04-15T00:00:00Z"
CREATED = "2025-01-01T00:00:00Z"
UPDATED = "2026-05-15T00:00:00Z"

LEADS = [
    {
        "id": "GOLD-001",
        "company_name": "Greenfield Holiday Imports",
        "city_state": "Los Angeles, CA",
        "data_source": "golden",
        "website": "greenfield-holiday.example",
        "status": "synced_to_crm",
        "legacy_score": "50",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            hs_codes=["950510", "950300", "940542", "940350"],
            ports=["Los Angeles", "Long Beach"],
            products=["artificial christmas trees", "decorative ornaments"],
            top_suppliers=[],  # no competitors → zero edges, scenario PERFECT_GREENFIELD
            total_shipments=300, matching_shipments=300,
            most_recent=ISO_RECENT,
        ),
        "bol_recent": bol_recent(growth_12m_pct=12.5, china_concentration=0.9),
        "scenario": "PERFECT_GREENFIELD",
    },
    {
        "id": "GOLD-002",
        "company_name": "Perfect Contested Importer Inc",
        "city_state": "Houston, TX",
        "data_source": "golden",
        "website": "contested-importer.example",
        "status": "synced_to_crm",
        "legacy_score": "55",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            hs_codes=["950510", "950300", "940350"],
            ports=["Houston", "New Orleans"],
            products=["artificial trees", "christmas lights"],
            top_suppliers=["Comp B Supplier", "Comp C Supplier", "Comp D Supplier"],
            total_shipments=250, matching_shipments=250,
            most_recent=ISO_RECENT,
        ),
        "bol_recent": bol_recent(growth_12m_pct=8.0, china_concentration=0.8),
        "scenario": "PERFECT_CONTESTED",
    },
    {
        "id": "GOLD-003",
        "company_name": "Dominant Competitor Buyer Llc",
        "city_state": "Savannah, GA",
        "data_source": "golden",
        "website": "domcomp-buyer.example",
        "status": "synced_to_crm",
        "legacy_score": "60",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            hs_codes=["950510", "940542", "060490"],
            ports=["Savannah", "Charleston"],
            products=["artificial trees", "decorative foliage"],
            top_suppliers=["Comp A Supplier"],  # one dominant
            total_shipments=180, matching_shipments=180,
            most_recent=ISO_RECENT,
        ),
        "bol_recent": bol_recent(growth_12m_pct=2.0, china_concentration=0.95),
        "scenario": "DOMINANT_COMPETITOR",
    },
    {
        "id": "GOLD-004",
        "company_name": "Stale Perfect Decor Co",
        "city_state": "Seattle, WA",
        "data_source": "golden",
        "website": "stale-decor.example",
        "status": "synced_to_crm",
        "legacy_score": "40",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            hs_codes=["950510", "950300", "940542", "940350"],
            ports=["Seattle", "Tacoma"],
            products=["christmas decorations"],
            top_suppliers=[],
            total_shipments=150, matching_shipments=150,
            most_recent=ISO_OLD,
        ),
        "bol_recent": bol_recent(growth_12m_pct=-15.0, china_concentration=0.7),
        "scenario": "STALE_PERFECT",
    },
    {
        "id": "GOLD-005",
        "company_name": "Partial Match Trading",
        "city_state": "Miami, FL",
        "data_source": "golden",
        "website": "partial-match.example",
        "status": "synced_to_crm",
        "legacy_score": "45",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            # 2/5 in persona (950510, 950300); other 3 are unrelated
            hs_codes=["950510", "950300", "870421", "271012", "271019"],
            ports=["Miami"],
            products=["mixed goods"],
            top_suppliers=[],
            total_shipments=80, matching_shipments=80,
            most_recent=ISO_RECENT,
        ),
        "bol_recent": bol_recent(growth_12m_pct=5.0, china_concentration=0.5),
        "scenario": "PARTIAL_MATCH",
    },
    {
        "id": "GOLD-006",
        "company_name": "New Growing Decor",
        "city_state": "Newark, NJ",
        "data_source": "golden",
        "website": "new-growing.example",
        "status": "synced_to_crm",
        "legacy_score": "35",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            hs_codes=["950510", "870421", "271012"],  # 1/3 overlap
            ports=["Newark"],
            products=["small assorted"],
            top_suppliers=[],
            total_shipments=15, matching_shipments=15,
            most_recent=ISO_RECENT,
        ),
        "bol_recent": bol_recent(growth_12m_pct=45.0, china_concentration=0.6),
        "scenario": "NEW_GROWING",
    },
    {
        "id": "GOLD-007",
        "company_name": "Wrong Industry Importer Inc",
        "city_state": "Detroit, MI",
        "data_source": "golden",
        "website": "wrong-industry.example",
        "status": "synced_to_crm",
        "legacy_score": "80",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            hs_codes=["870421", "271012", "271019", "392640"],  # 0/4 in persona
            ports=["Detroit"],
            products=["auto parts", "lubricants"],
            top_suppliers=[],
            total_shipments=400, matching_shipments=400,
            most_recent=ISO_RECENT,
        ),
        "bol_recent": bol_recent(growth_12m_pct=20.0, china_concentration=0.85),
        "scenario": "HS_MISMATCH_HIGH_VOL",
    },
    {
        "id": "GOLD-008",
        "company_name": "Unreachable Perfect Buyer Co",
        "city_state": "Oakland, CA",
        "data_source": "golden",
        "website": "unreachable.example",
        "status": "synced_to_crm",
        "legacy_score": "50",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            hs_codes=["950510", "950300", "940542", "940350"],
            ports=["Oakland"],
            products=["holiday decor"],
            top_suppliers=[],
            total_shipments=200, matching_shipments=200,
            most_recent=ISO_RECENT,
        ),
        "bol_recent": bol_recent(growth_12m_pct=10.0, china_concentration=0.9),
        "scenario": "UNREACHABLE_PERFECT",
    },
    {
        "id": "GOLD-009",
        "company_name": "Strong Signal But Declined Inc",
        "city_state": "Boston, MA",
        "data_source": "golden",
        "website": "declined-strong.example",
        "status": "not_interested",
        "legacy_score": "75",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            hs_codes=["950510", "950300", "940542", "940350"],
            ports=["Boston"],
            products=["christmas trees"],
            top_suppliers=[],
            total_shipments=300, matching_shipments=300,
            most_recent=ISO_RECENT,
        ),
        "bol_recent": bol_recent(growth_12m_pct=15.0, china_concentration=0.92),
        "scenario": "NOT_INTERESTED_STRONG",
    },
    {
        "id": "GOLD-010",
        "company_name": "Hard No With Trace Volume Llc",
        "city_state": "Anywhere, US",
        "data_source": "golden",
        "website": "",
        "status": "not_interested",
        "legacy_score": "10",
        "created_at": CREATED, "updated_at": UPDATED, "is_test": "true",
        "bol_payload": bol_payload(
            hs_codes=["870421"],  # 0/1 in persona
            ports=[],
            products=[],
            top_suppliers=[],
            total_shipments=1, matching_shipments=1,
            most_recent=ISO_OLD,
        ),
        "bol_recent": bol_recent(),
        "scenario": "NOT_INTERESTED_WEAK",
    },
]


# ---------- personnel ----------
# Each lead gets a contact tuned to the scenario.

PERSONNEL = [
    # GOLD-001: senior VP, has email
    {"first": "Alice", "last": "Anders", "title": "VP of Sourcing",
     "email": "alice.anders@greenfield-holiday.example", "phone": "+1-555-1001",
     "lead_id": "GOLD-001"},
    # GOLD-002: senior director, has email
    {"first": "Brian", "last": "Banks", "title": "Director of Procurement",
     "email": "b.banks@contested-importer.example", "phone": "+1-555-1002",
     "lead_id": "GOLD-002"},
    # GOLD-003: senior head, has email
    {"first": "Carla", "last": "Cho", "title": "Head of Supply",
     "email": "carla.cho@domcomp-buyer.example", "phone": "+1-555-1003",
     "lead_id": "GOLD-003"},
    # GOLD-004: senior owner, has email
    {"first": "Dan", "last": "Diaz", "title": "Owner",
     "email": "dan@stale-decor.example", "phone": "+1-555-1004",
     "lead_id": "GOLD-004"},
    # GOLD-005: generic contact, has email but no senior title
    {"first": "Eve", "last": "Eng", "title": "Coordinator",
     "email": "eve@partial-match.example", "phone": "+1-555-1005",
     "lead_id": "GOLD-005"},
    # GOLD-006: senior manager, has email
    {"first": "Frank", "last": "Foley", "title": "General Manager",
     "email": "frank@new-growing.example", "phone": "+1-555-1006",
     "lead_id": "GOLD-006"},
    # GOLD-007: senior contact (would be high but HS mismatch kills score)
    {"first": "Grace", "last": "Garza", "title": "VP Operations",
     "email": "grace@wrong-industry.example", "phone": "+1-555-1007",
     "lead_id": "GOLD-007"},
    # GOLD-008: NO contact at all (unreachable)
    # (intentionally omitted)
    # GOLD-009: senior contact, has email — strong-but-declined
    {"first": "Henry", "last": "Hsu", "title": "Chief Procurement Officer",
     "email": "henry@declined-strong.example", "phone": "+1-555-1009",
     "lead_id": "GOLD-009"},
    # GOLD-010: no contact (trace volume, declined)
    # (intentionally omitted)
]


# ---------- competitors ----------
# COMP-A through COMP-E aggregate HS codes so the persona top-N includes the
# scenario-relevant codes (950510, 950300, 940542, 940350, 060490, 950590, 611020).

COMPETITORS = [
    # COMP-A: all-rounder, also lists GOLD-003 as a dominant 80% supplier
    {"id": "COMP-A", "slug": "comp-a-supplier", "company_name": "Comp A Supplier",
     "company_name_cn": "公司A", "country": "China", "country_code": "CN",
     "city": "Shanghai", "hs_codes": ["950510", "950300", "940542", "940350", "060490"],
     "products": ["artificial trees", "ornaments", "lights"],
     "risk_rating": "HIGH", "count_shipments": 500, "revenue": "10000000",
     "us_customers": [
         # Dominant edge on GOLD-003
         {"company_name": "Dominant Competitor Buyer Llc", "total_teus": 200,
          "total_shipments_supplier": 150, "shipments_12m": 80,
          "shipments_percents_supplier": 80.0, "most_recent_shipment": ISO_RECENT},
     ],
     "aliases": ["Comp A Trading", "Comp A Co Ltd"]},
    # COMP-B: focused on 950510/950300, ships to GOLD-002
    {"id": "COMP-B", "slug": "comp-b-supplier", "company_name": "Comp B Supplier",
     "company_name_cn": "公司B", "country": "China", "country_code": "CN",
     "city": "Shenzhen", "hs_codes": ["950510", "950300"],
     "products": ["christmas trees"],
     "risk_rating": "MED", "count_shipments": 300, "revenue": "5000000",
     "us_customers": [
         {"company_name": "Perfect Contested Importer Inc", "total_teus": 60,
          "total_shipments_supplier": 50, "shipments_12m": 30,
          "shipments_percents_supplier": 20.0, "most_recent_shipment": ISO_RECENT},
     ], "aliases": []},
    {"id": "COMP-C", "slug": "comp-c-supplier", "company_name": "Comp C Supplier",
     "company_name_cn": "公司C", "country": "China", "country_code": "CN",
     "city": "Yiwu", "hs_codes": ["950510", "940350"],
     "products": ["lights", "garland"],
     "risk_rating": "MED", "count_shipments": 220, "revenue": "3500000",
     "us_customers": [
         {"company_name": "Perfect Contested Importer Inc", "total_teus": 40,
          "total_shipments_supplier": 40, "shipments_12m": 22,
          "shipments_percents_supplier": 18.0, "most_recent_shipment": ISO_RECENT},
     ], "aliases": []},
    {"id": "COMP-D", "slug": "comp-d-supplier", "company_name": "Comp D Supplier",
     "company_name_cn": "公司D", "country": "China", "country_code": "CN",
     "city": "Ningbo", "hs_codes": ["940542", "060490"],
     "products": ["lighting", "cut foliage"],
     "risk_rating": "LOW", "count_shipments": 120, "revenue": "1800000",
     "us_customers": [
         {"company_name": "Perfect Contested Importer Inc", "total_teus": 30,
          "total_shipments_supplier": 30, "shipments_12m": 15,
          "shipments_percents_supplier": 12.0, "most_recent_shipment": ISO_RECENT},
     ], "aliases": []},
    {"id": "COMP-E", "slug": "comp-e-supplier", "company_name": "Comp E Supplier",
     "company_name_cn": "公司E", "country": "China", "country_code": "CN",
     "city": "Guangzhou", "hs_codes": ["950590", "611020"],
     "products": ["other holiday", "knitwear"],
     "risk_rating": "LOW", "count_shipments": 90, "revenue": "1200000",
     "us_customers": [
         {"company_name": "Perfect Contested Importer Inc", "total_teus": 20,
          "total_shipments_supplier": 20, "shipments_12m": 10,
          "shipments_percents_supplier": 8.0, "most_recent_shipment": ISO_RECENT},
     ], "aliases": []},
]


# ---------- row builders ----------

def _lead_row(lead: dict) -> list[str]:
    cells = {c: "" for c in LEADS_COLS}
    cells["id"] = lead["id"]
    cells["company_name"] = lead["company_name"]
    cells["city_state"] = lead["city_state"]
    cells["data_source"] = lead["data_source"]
    cells["website"] = lead.get("website", "")
    cells["status"] = lead["status"]
    cells["legacy_score"] = lead["legacy_score"]
    cells["created_at"] = lead["created_at"]
    cells["updated_at"] = lead["updated_at"]
    cells["is_test"] = lead["is_test"]
    cells["bol_payload_json"] = lead["bol_payload"]
    cells["bol_recent_json"] = lead["bol_recent"]
    # bol_suppliers_json is optional and not needed for golden scenarios
    cells["bol_suppliers_json"] = ""
    return [cells[c] for c in LEADS_COLS]


def _personnel_row(p: dict, idx: int) -> list[str]:
    cells = {c: "" for c in PERSONNEL_COLS}
    cells["id"] = f"PERS-{idx:03d}"
    cells["first_name"] = p["first"]
    cells["last_name"] = p["last"]
    cells["full_name"] = f"{p['first']} {p['last']}"
    cells["company_name"] = ""  # joined via lead_id
    cells["data_source"] = "golden"
    cells["job_title"] = p["title"]
    cells["email"] = p.get("email", "")
    cells["phone"] = p.get("phone", "")
    cells["lead_id"] = p["lead_id"]
    cells["legacy_score"] = "50"
    cells["is_test"] = "true"
    cells["created_at"] = CREATED
    cells["updated_at"] = UPDATED
    return [cells[c] for c in PERSONNEL_COLS]


def _competitor_row(c: dict) -> list[str]:
    cells = {col: "" for col in COMPETITORS_COLS}
    cells["id"] = c["id"]
    cells["slug"] = c["slug"]
    cells["company_name"] = c["company_name"]
    cells["company_name_cn"] = c["company_name_cn"]
    cells["country"] = c["country"]
    cells["country_code"] = c["country_code"]
    cells["addresses_text"] = ""
    cells["city"] = c["city"]
    cells["hs_codes_pg_array"] = pg_array(c["hs_codes"])
    cells["importer_contact_count"] = str(len(c.get("us_customers", [])))
    cells["revenue_or_volume_usd"] = c.get("revenue", "")
    cells["metadata_json"] = ""
    cells["products_text_pg_array"] = pg_array(c.get("products", []))
    cells["count_shipments"] = str(c.get("count_shipments", 0))
    cells["logistics_metadata"] = ""
    cells["risk_rating"] = c.get("risk_rating", "")
    cells["flag_count"] = "0"
    cells["latitude_or_coord"] = ""
    cells["shipment_history_json"] = ""
    cells["us_customer_list_json"] = json.dumps(c.get("us_customers", []))
    cells["supplier_variants_pg_array"] = pg_array(c.get("aliases", []))
    cells["recent_shipment_bills_json"] = ""
    cells["logistics_partners_json"] = ""
    cells["is_archived"] = "false"
    cells["created_at"] = CREATED
    cells["updated_at"] = UPDATED
    return [cells[col] for col in COMPETITORS_COLS]


# ---------- main ----------

OUT_DIR = Path(__file__).resolve().parent.parent / "input" / "golden"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    leads_path = OUT_DIR / "leads.csv"
    pers_path = OUT_DIR / "personnel.csv"
    comp_path = OUT_DIR / "bol_competitors.csv"

    with leads_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        for lead in LEADS:
            w.writerow(_lead_row(lead))

    with pers_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        for i, p in enumerate(PERSONNEL):
            w.writerow(_personnel_row(p, i + 1))

    with comp_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        for c in COMPETITORS:
            w.writerow(_competitor_row(c))

    print(f"wrote {len(LEADS)} leads → {leads_path}")
    print(f"wrote {len(PERSONNEL)} personnel → {pers_path}")
    print(f"wrote {len(COMPETITORS)} competitors → {comp_path}")


if __name__ == "__main__":
    main()
