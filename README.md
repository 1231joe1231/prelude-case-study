# Prelude Case Study — Lead Intelligence Agent

Ingest three messy CSVs (US importers, their contacts, competing Chinese exporters) and produce a ranked list of importers worth pitching this week, each with a grounded 1–2 sentence rationale.

## Stack

- **Backend**: FastAPI + SQLAlchemy + SQLite + Anthropic SDK (port 8000)
- **Frontend**: Vite + React 19 + TypeScript + Tailwind v4 + `@xyflow/react` (port 5173)

## Run

```powershell
# Backend
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env       # then paste ANTHROPIC_API_KEY into .env
uvicorn app.main:app --port 8000

# Frontend (new shell)
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Backend startup ingests `backend/input/real/*.csv` into SQLite. Open the **Pipeline** tab, click **Run pipeline** to compute rankings + rationales. The **Deliverable** tab renders the ranked table with checkboxes; rationales appear as fallback placeholders first, then upgrade to LLM output via 2s polling.

Without `ANTHROPIC_API_KEY`, the rationale layer falls back to a deterministic synthesizer (still cites literal facts). Demo runs without a key.

## Architecture choice

**Option 2 from the brief: deterministic feature pipeline + LLM rationale layer.**

```
CSV ── ingest ── SQLite (5 tables) ── persona ── features ── score ── rank
                                                                       │
                                                          rationale (LLM) ── /api/leads/ranked
```

Rejected alternatives:

- **Single LLM call over the dataset.** Works at 120 rows, breaks at 100K. Non-deterministic, hard to evaluate, can't audit per-feature. Fails the brief's scaling and grounding asks.
- **Multi-step retrieval agent (tool-use loop).** Overkill at 120 rows of structured data. Each lead doesn't need open-ended investigation. Latency + cost not justified. Documented escalation path in `ranking/rationale.py` — swap to Agent SDK with grounding tools if ablation shows >5% hallucinated citations.

Defence (the brief grades on this):

- **Auditability.** Every score component is a literal value from the data. The `/api/leads/{id}/trace` endpoint returns the full per-lead trace: features payload, LLM prompt, raw response, factuality check result, fallback used (if any). The `/api/ranking/events` stream surfaces persona inference, batch dispatch, per-lead LLM calls, factuality failures.
- **Bounded LLM role.** LLM never ranks; it only writes prose for the top-N. Drift is bounded; output is verified by a regex factuality check (any 6-digit number cited must appear in the lead's HS codes; ≥1 specific anchor required).
- **Failure transparency.** Three fallback sources distinguished in `rationale_source`: `fallback_no_key`, `fallback_error`, `fallback_factuality`. Frontend shows a "FALLBACK" badge; the trace shows why.

## How each of the three sources is used

| Source | Signal extracted | Where in code |
|---|---|---|
| `leads.csv` | volume (`total_shipments`), recency (`most_recent_shipment` → days since), product fit (`bol_payload.hsCodes` overlapped with persona), logistics (`topPorts`), workflow state (`status` → not-interested filter / synced-to-CRM penalty) | `ranking/features.py`, `ranking/score.py` |
| `personnel.csv` | reachability (any contact with email), seniority (title matches CEO/VP/Owner/Director/Procurement regex) | `ranking/features.py` |
| `bol_competitors.csv` | factory persona (top-8 HS codes across all competitors); competitive pressure per lead (distinct exporters shipping to that importer); top-supplier share % | `ranking/persona.py`, `ranking/features.py`; shipments graph in `ingest.py` |

Each major score component sources from a different table — the brief weights "use of all three sources" and this trace is explicit in `score.WEIGHTS` and the Deliverable table's `components` column.

The shipment data inside the CSVs was a graph in disguise — `lead.bol_suppliers_json`, `lead.bol_payload.topSuppliers`, and `competitor.us_customer_list_json` are three views of the same `exporter → importer` edges. `ingest.py` deduplicates them into a single `shipments` table with normalized-name FK resolution (lowercase + strip Inc/LLC/Co/Ltd suffixes + punct). The **Graph** tab renders this bipartite graph with `@xyflow/react` — drag/zoom/pan + hover tooltips with TEU, total ships, supplier share %, source-JSON provenance.

## Scaling from ~120 to ~100,000 leads

- **Ingest** is `pandas → SQLAlchemy bulk_insert_mappings`. Linear in row count; ~120ms at current size, hours at 100K but only at startup — not on the request path.
- **Feature extraction** runs in-process per lead with one SQL query per lead. At 100K that's 100K small queries; trivial to batch with a single grouped query in `features.py` if it becomes hot. The DB indexes (`lead_id`, `kind`, `competitor_id`) keep individual queries O(log n).
- **Scoring** is pure Python arithmetic; ~µs per lead. No scaling concern.
- **LLM rationale** scales with **top-N**, not total leads. Top-50 default. At 100K leads, you still only pay for 50 LLM calls per ranking refresh, batched parallel via `asyncio.gather` + semaphore (current 10). With prompt caching, ~95% of input tokens are cached after the first call → roughly 10× cheaper than uncached. Estimated cost: <$0.01 per full ranking refresh on Haiku 4.5.
- **Frontend** polls cached rationales every 2s. Cache is keyed by `lead_id`; subsequent ranking requests reuse cached rationales unless the operator triggers a re-run.

The only piece that does NOT scale automatically is the deterministic ranker reading every lead into memory each request. At 100K leads, push the sort into SQL (`ORDER BY composite_score DESC LIMIT 50`) and recompute features lazily on a schedule rather than per-request.

## Evaluation + operator feedback loop

The brief grades "evaluation thinking." What we built and what we'd build next:

**Built**

- **Factuality check (deterministic).** Every LLM rationale is regex-checked: cited HS codes must be in the lead's `lead_hs_codes`; at least one specific anchor (HS code, port substring, competitor name, or senior title) must be cited. Failures fall back to deterministic text + `source=fallback_factuality`. Surfaced as a count in the `/ranking/events` stream and as a per-row badge in the UI.
- **Per-lead trace.** `/api/leads/{id}/trace` returns the exact prompt, raw model output, factuality verdict, and final text. Lets a reviewer (or eval script) verify any claim against source data.
- **Golden dataset hook.** `INPUT_VERSION=golden` swaps `backend/input/real/` for `backend/input/golden/`. A small hand-crafted CSV set with known-correct rankings is intended to assert exact expected output in a pytest run. Folder skeleton in place; golden CSVs not authored (next phase).

**Plan for next iteration**

- **Ablation script.** Zero each weight in `score.WEIGHTS` and rerank; report Spearman correlation against full ranking. Any weight whose ablation changes nothing is dead weight.
- **Eyeball set.** Pick ~10 leads manually labeled "obviously high / obviously low." Assert all `high` rank in top 33%, all `low` in bottom 33%.
- **Factuality regression.** Run the LLM on a fixed feature set N times; fail the build if hallucination rate >5%.

**Operator feedback loop (the `status` column)**

`leads.csv` already carries `not_interested` and `synced_to_crm` — the operator's existing labels. Currently consumed as filter+penalty:

- `not_interested` → score penalty (still listed but pushed down; the operator can flip back if signal changes)
- `synced_to_crm` → 0.5× score multiplier (in-play; deprioritize for "fresh leads worth chasing *this week*")

To close the loop:

1. **Log selections.** When the operator ticks the "selected for outreach" checkbox, persist `{lead_id, selected_at, feature_snapshot}`. The features at decision time are the supervised label inputs.
2. **Re-fit weights.** Once enough labels accumulate (say 200), fit a logistic regression over the 6 component scores against `was_selected_within_7d`. New `WEIGHTS` replace the hand-set ones, versioned in code so old vs new can be A/B'd via `INPUT_VERSION`.
3. **Drift watch.** When the factuality failure rate or the rank-vs-selection AUC drops below a threshold, alert. Brief asks how the loop closes — this is the answer: features + status feedback + retrain.

## Working assumptions

- **Factory persona is inferred, not given.** Brief gives no "I am factory X" input. Persona derived from top-8 HS codes across the 30 competitors. In production: parameterized per logged-in factory (`/api/leads/persona` would return per-tenant). Documented in [`ranking/persona.py`](backend/app/ranking/persona.py).
- **"Worth chasing this week"** = recent shipping + product fit (HS overlap) + reachable contact + not currently in `not_interested`.
- **Legacy `score` columns** in `leads.csv` and `personnel.csv` are ignored per brief.
- **Single product vertical.** Dataset is curated around Christmas decorations / artificial trees (HS 950510, 950300, 940542, 940350, 060490 dominate). No cross-vertical leakage modeled.
- **In-memory rationale cache** is process-local and cleared on restart. Brief asks for no persistence beyond demo; this satisfies it. Production: Redis with `(lead_id, feature_hash)` key + TTL.
- **`synced_to_crm` ≠ won.** Treated as "in play, deprioritize"; adjustable in [`ranking/score.py`](backend/app/ranking/score.py).

## Out of scope (explicitly, per brief)

- Outreach email drafting.
- Filters / search / lead detail pages / charts.
- Authentication, multi-user state, deployment.
- Persistence beyond what the demo needs (selections live in React `useState`).

## Repo layout

```
backend/
  app/
    ingest.py          # CSV → SQLite + bipartite shipments graph
    models.py          # SQLAlchemy schema (5 tables)
    db.py              # engine + session
    main.py            # FastAPI lifespan, dotenv loader, router mount
    pipeline/
      state.py         # in-memory pipeline state + stage transitions
    ranking/
      persona.py       # factory HS profile (inferred from competitors)
      features.py      # per-lead feature extraction
      score.py         # 6-component weighted sum
      rationale.py     # Anthropic SDK + prompt caching + factuality + fallback
      cache.py         # in-memory rationale cache + background batch
      trace.py         # per-lead audit trail + global event stream
    routers/
      health.py        # GET /api/health
      tables.py        # raw table dumps (debugging)
      ranking.py       # GET /api/leads/ranked, /api/leads/{id}/trace
      pipeline.py      # POST /api/pipeline/run, GET /api/pipeline/state
  input/
    real/              # 121 leads, 120 personnel, 30 competitors
    golden/            # hand-crafted eval set (placeholder)
  .env                 # ANTHROPIC_API_KEY (gitignored)

frontend/
  src/
    App.tsx            # Deliverable / Tables / Graph / Pipeline pages
    lib/api.ts         # thin fetch wrapper
```
