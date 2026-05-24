# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A take-home for Prelude (cross-border B2B platform connecting Chinese factories with US importers). The brief — `../prelude_takehome_brief.html` — asks for a **Lead Intelligence Agent**: ingest three CSVs, output a ranked list of US importers worth chasing, each with reasoning grounded in the underlying data. A thin single-page frontend surfaces the ranking with a checkbox to mark leads as selected for outreach.

The agent is the deliverable. Frontend is intentionally minimal. Out of scope: drafting emails, filters/search/charts, auth, deployment, persistence beyond the demo.

## Current state (pipeline + audit + eval harness done)

Built:
- `backend/app/main.py` — FastAPI app, CORS for `localhost:5173`, reads `backend/.env`, lifespan does drop+ingest+clear caches and emits startup events.
- `backend/app/models.py` — `Lead`, `LeadAttribute`, `Personnel`, `Competitor`, `CompetitorAttribute`, `Shipment` (bipartite-graph edge table).
- `backend/app/ingest.py` — reads CSVs from `backend/input/{INPUT_VERSION}/` (default `real`); flattens BOL payloads; normalizes HS/ports/products into `lead_attributes`; resolves shipment FKs via normalized name + alias map.
- `backend/app/ranking/persona.py` — top-N HS codes aggregated from `competitor_attributes` (cached).
- `backend/app/ranking/features.py` — `LeadFeatures` dataclass + pure per-lead extraction.
- `backend/app/ranking/score.py` — weighted 6-component composite. Live WEIGHTS: `hs_fit=0.35, volume=0.30, reachability=0.25, seniority=0.10, recency=0, competitive=0`. Penalties: `not_interested → ×0.4`, `hs_overlap_count=0 → ×0.5`. See module docstring for tuning rationale.
- `backend/app/ranking/rationale.py` — Anthropic SDK single-call per lead, ephemeral prompt cache, factuality check (HS / ports / competitors / titles / numeric BOL anchors), deterministic fallback on missing key / 429 / factuality fail. Retry on transient errors. `CONCURRENCY=4` (free-tier 50 req/min).
- `backend/app/ranking/cache.py` — in-memory cache; streams per-lead writes to cache + trace as each LLM call completes (not gather-then-flush).
- `backend/app/ranking/trace.py` — `RationaleTrace` (full 6-stage audit) + global event ring buffer.
- `backend/app/routers/ranking.py` — `GET /api/leads/ranked`, `/persona`, `/{id}/trace`, `/ranking/events`.
- `backend/app/routers/tables.py` — `GET /api/tables/{leads|personnel|competitors|lead_attributes|competitor_attributes|shipments}`.
- `backend/scripts/build_golden.py` — synthesizes the 10-lead/8-personnel/5-competitor golden set; each lead is a hand-designed scenario.
- `backend/tests/test_golden_ranking.py` — 12 pytest assertions over the golden set (rank-1 sanity, penalty correctness, hs_fit dominance, ablation).
- `frontend/src/App.tsx` — 4-tab SPA: Deliverable (ranked table + per-lead 6-stage trace expander), Tables (dropdown over 6 tables), Graph (ReactFlow bipartite), Trace (global event stream).

Remaining (CLAUDE.md plan):
- `POST /api/leads/{id}/select` — operator selection persistence (separate from CSV `status`).
- Final README the brief asks for (architecture defence, scaling story, eval plan, working assumptions).

## Commands

### Backend (Python 3.11, FastAPI)
```powershell
cd backend
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
- Health: http://localhost:8000/api/health
- OpenAPI: http://localhost:8000/docs

### Frontend (Vite + React 19 + TS + Tailwind v4)
```powershell
cd frontend
npm install
npm run dev      # dev server on :5173, proxies /api → :8000
npm run build    # tsc -b && vite build
npm run lint     # eslint
```

### Tests
```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest tests -v
```
- `tests/conftest.py` sets `INPUT_VERSION=golden` in-process and rebuilds a per-session test DB.
- `tests/test_golden_ranking.py` asserts ranking on the hand-designed scenarios in `backend/scripts/build_golden.py`.

To regenerate the golden CSVs after editing scenarios:
```powershell
.\.venv\Scripts\python.exe backend\scripts\build_golden.py
```

## Source data (lives in `../` — parent of the project root)

Three CSVs at `case-study-material/{leads,personnel,bol_competitors}.csv`. **All three have NO header row** — columns must be inferred. Sizes: 120 leads, 119 personnel, 29 competitors. Brief explicitly warns the data is "real-shape and intentionally messy".

Inferred column layout (verify before coding against it):

- **leads.csv** — `id, company, location, source, ?, ?, ?, ?, domain, status, legacy_score, created_at, updated_at, ?bool, ?, raw_bol_json, ?`
  - `status` ∈ {`not_interested`, `synced_to_crm`} — this is the **operator feedback loop**.
  - `raw_bol_json` (col 16) is the BOL payload: `{hsCodes, topPorts, topProducts, topSuppliers, totalShipments, totalSuppliers, matchingShipments, mostRecentShipment}`.
- **personnel.csv** — `id, first, last, full, company, source, ..., email, ..., lead_id, legacy_score, ?bool, created_at, updated_at`. Joined to leads by `lead_id`.
- **bol_competitors.csv** — Chinese factories. Columns include `id, slug, name, country, address, city, hs_codes{}, ..., products{}, ..., LOW/MED/HIGH activity bucket, ..., timestamps`. Used to gauge competitive pressure on a given lead.

The dataset is curated around **Christmas decoration / artificial tree products** (HS 950510, 950300, 940542, 940350, 060490 dominate). The implied "factory operator" sells this product category — useful as a default persona.

**Brief explicitly says the `score` columns in leads.csv and personnel.csv are legacy artifacts. Do not use them as input or as a baseline.**

## Architecture plan (think this through before writing code)

### Chosen agent shape: **deterministic feature pipeline + LLM rationale layer** (brief option 2)

Rejected alternatives:
- **Single LLM call over full dataset** — works at 120 rows, breaks at 100K, non-deterministic, hard to evaluate, can't be audited per-feature.
- **Multi-step retrieval agent** — overkill for 120 leads with structured data; each lead doesn't need open-ended investigation. Latency and cost are not justified by signal gain.

Defence (this is what the README must articulate):
- **Scaling**: deterministic scoring is O(n) and stays cheap as leads grow 1000×. LLM cost stays bounded by ranking top-K, not by total leads.
- **Auditability**: every score component is a literal fact from the data; reasoning strings cite those same features. The brief weights "grounding" heavily.
- **Evaluation**: features can be tested in isolation; the LLM layer only generates prose, not ranking, so its drift is bounded.
- **Feedback loop**: `status` column maps directly to labels. Future-Claude can train a weighted model over features once enough operator decisions accumulate.

### Feature design (uses all three sources — the brief grades on this)

For each lead, compute features that combine the three CSVs:

| Source | Signal | Why it matters to a factory |
|---|---|---|
| leads.csv `totalShipments`, `mostRecentShipment` | volume + recency | high-volume, recently active importers are real buyers |
| leads.csv `hsCodes` ∩ factory HS profile | product fit | matches what the factory actually makes |
| leads.csv `topPorts` | logistics fit | factories near specific ports prefer certain destinations |
| bol_competitors.csv overlap on HS codes | competitive pressure | importer already buying from many Chinese factories = harder to win |
| bol_competitors.csv `LOW/MED/HIGH` bucket on overlapping competitors | concentration risk | dominated by one big competitor = uphill |
| personnel.csv contact count + email presence | reachability | a lead with no email is not actionable |
| personnel.csv title (if present) | seniority | decision-maker > generic ops contact |
| leads.csv `status` | filter | drop `not_interested`; treat `synced_to_crm` as "already in play" |

Compose into a single score (weights initially hand-set, later learnable from `status` feedback).

### LLM rationale layer

For the top-N ranked leads, call an LLM with **only that lead's extracted features** (not the raw CSV) and ask for a 1-2 sentence rationale citing specific HS codes, ports, competitors, or contacts. Constraints in the prompt:
- must cite at least one literal fact from the feature payload
- must not invent facts not in the payload
- terse, not marketing copy

This isolates the LLM to a bounded text-generation role — easy to swap models, easy to cache, easy to eval (golden examples + factuality check that cited facts appear in the source).

### API shape (target)

- `GET /api/leads/ranked?limit=50` → `[{lead_id, company, score, reasoning, features, selected}]`
- `POST /api/leads/{id}/select` `{selected: true}` → updates a local `selected_for_outreach` flag (separate from the CSV `status` so we don't fight the brief's "no persistence beyond demo")
- `POST /api/ingest` (one-shot, or run at startup) → reads the three CSVs into SQLite

### Frontend

Single page, single table. Columns: company, score, reasoning, checkbox. No filters, no search, no detail pages — brief explicitly says do not build these.

### Evaluation strategy (the brief weights this heavily)

- **Golden set**: hand-label ~10 leads as obviously-good / obviously-bad based on the data and check the ranker agrees.
- **Ablation**: rerank with each feature zeroed out; if removing a feature does not change top-10, that feature is dead weight.
- **Factuality check on rationales**: regex / substring-match every cited HS code, port, competitor name back into the source row. Any uncited claim is a hallucination.
- **Feedback loop sketch**: document how `status` labels would feed a learned weighting over time (do not build the trainer; describing it is enough).

## Working assumptions to call out in the final README

- The factory operator's product profile is inferred from the dominant HS codes in `bol_competitors.csv` (Christmas/decoration goods). In production this would be parameterized per factory.
- "Worth chasing this week" = product fit + reachable contact + activity volume + not in `not_interested` (or in `not_interested` with strong new signal — penalized × 0.4, not dropped).
- `synced_to_crm` is treated as a neutral status on this dataset (every eligible lead is synced_to_crm; uniform penalty would be a no-op).
- `not_interested` is penalized (× 0.4), not dropped — re-engagement is a real sales pattern when new signal arrives.
- Zero HS-overlap leads are penalized × 0.5 — we have no product story to pitch them with regardless of activity.
- Recency and competitive pressure features are computed and exposed, but weighted 0 in scoring until source data improves (16/121 leads have `most_recent_shipment`; only 5/50 top-ranked have any competitor edges).
- Legacy `score` columns are ignored per brief.

## Conventions

- Backend uses SQLAlchemy 2.0 typed `Mapped[...]` style (see existing model).
- Frontend uses Tailwind v4 via `@tailwindcss/vite` plugin — no `tailwind.config.js`, classes are in the JSX.
- Vite dev proxy means frontend always calls `/api/...` (never absolute URLs).
- SQLite file at `backend/data.db` is the demo store. Safe to delete and re-ingest.

## Input versioning + golden dataset (live)

`ingest.py:get_input_dir()` reads `INPUT_VERSION` env (default `real`) and joins
`backend/input/{version}/`. Tests set `INPUT_VERSION=golden` in `conftest.py`.

Layout:
```
backend/input/
  real/      121 leads, 120 personnel, 30 competitors  (gitignored)
  golden/    10  leads, 8   personnel, 5  competitors  (committed)
```

The golden CSVs are NOT picked from real data — they're synthesized by
`backend/scripts/build_golden.py` so each lead exercises a specific code
path. Scenarios:

| id        | scenario                | expected behavior                                       |
|-----------|-------------------------|---------------------------------------------------------|
| GOLD-001  | PERFECT_GREENFIELD      | rank #1 (full HS, high vol, recent, senior, no comps)   |
| GOLD-002  | PERFECT_CONTESTED       | top-mid (5 competitor edges resolved via shipments)     |
| GOLD-003  | DOMINANT_COMPETITOR     | top-mid (1 competitor at ~80% supplier share)           |
| GOLD-004  | STALE_PERFECT           | top-mid (recency weight=0 → staleness doesn't hurt)     |
| GOLD-005  | PARTIAL_MATCH           | middle (2/5 HS overlap)                                 |
| GOLD-006  | NEW_GROWING             | mid-low (small volume even with senior contact)         |
| GOLD-007  | HS_MISMATCH_HIGH_VOL    | low (HS_MISMATCH_PENALTY × 0.5 sinks the rank)          |
| GOLD-008  | UNREACHABLE_PERFECT     | low (reachability + seniority both at floor)            |
| GOLD-009  | NOT_INTERESTED_STRONG   | mid (× 0.4 penalty but still ranked)                    |
| GOLD-010  | NOT_INTERESTED_WEAK     | dead last                                               |

Eval suite (`backend/tests/test_golden_ranking.py`, 12 tests):
- shape: ingest sanity, persona contains core Christmas codes
- rank: GOLD-001 is #1, GOLD-010 is #10, top-4 are the strong synced leads
- penalty: NOT_INTERESTED scaling, hs_mismatch beats high-volume nonsense
- graph: contested lead resolves ≥3 edges, dominant edge yields ~80% share
- ablation: zeroing hs_fit or volume changes top-5 (proves they're load-bearing)

To regenerate after editing scenarios: `python backend/scripts/build_golden.py`.
