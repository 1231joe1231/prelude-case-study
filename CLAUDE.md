# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A take-home for Prelude (cross-border B2B platform connecting Chinese factories with US importers). The brief — `../prelude_takehome_brief.html` — asks for a **Lead Intelligence Agent**: ingest three CSVs, output a ranked list of US importers worth chasing, each with reasoning grounded in the underlying data. A thin single-page frontend surfaces the ranking with a checkbox to mark leads as selected for outreach.

The agent is the deliverable. Frontend is intentionally minimal. Out of scope: drafting emails, filters/search/charts, auth, deployment, persistence beyond the demo.

## Current state (scaffolding only, no agent yet)

What exists:
- [backend/app/main.py](backend/app/main.py) — FastAPI app, CORS for `localhost:5173`, mounts `/api/health` and `/api/records`.
- [backend/app/models.py](backend/app/models.py) — **placeholder `CustomsRecord` model that does NOT match the real CSV schema**. Will be replaced with `Lead`, `Personnel`, `Competitor` tables.
- [backend/app/db.py](backend/app/db.py) — SQLAlchemy engine pointing at `backend/data.db` (SQLite).
- [frontend/src/App.tsx](frontend/src/App.tsx) — boilerplate health-check page; will be replaced with the ranked table.
- [frontend/vite.config.ts](frontend/vite.config.ts) — proxies `/api/*` → `http://localhost:8000`.

What is missing (everything that matters):
- CSV ingestion / schema for all three entities
- The agent itself
- Ranking + reasoning API endpoint
- The single-page table UI with the "selected for outreach" checkbox
- The README required by the brief (architecture defence, scaling story, eval plan)

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

No test runner is configured yet. If tests are added, prefer `pytest` for backend (deterministic feature-extraction tests are the highest-value targets) and `vitest` for frontend.

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
- "Worth chasing this week" = active recent shipping + product fit + reachable contact + not already in `not_interested`.
- `synced_to_crm` is treated as "in play, deprioritize" not "won" — adjustable.
- Legacy `score` columns are ignored per brief.

## Conventions

- Backend uses SQLAlchemy 2.0 typed `Mapped[...]` style (see existing model).
- Frontend uses Tailwind v4 via `@tailwindcss/vite` plugin — no `tailwind.config.js`, classes are in the JSX.
- Vite dev proxy means frontend always calls `/api/...` (never absolute URLs).
- SQLite file at `backend/data.db` is the demo store. Safe to delete and re-ingest.
