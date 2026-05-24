# Scoring Reference

This document is the authoritative specification for how a lead's composite score is computed. The README describes the *behavior*; this file describes the *math*. Source of truth: [`backend/app/ranking/score.py`](backend/app/ranking/score.py). If anything below disagrees with the live `WEIGHTS` dict in code, the code wins.

## Composite formula

```
composite = 0.30 × hs_fit
          + 0.25 × volume
          + 0.20 × reachability
          + 0.10 × seniority
          + 0.10 × demand_validated
          + 0.05 × recency             (signed: can be positive or negative)
          − 0.05 × concentration       (penalty; always subtracts or zero)

then multiplicatively:
  if hs_overlap_count == 0 and lead has any HS codes:   composite × 0.5
  if status == "not_interested":                        composite × 0.4
  if status == "synced_to_crm":                         composite × 1.0   (no-op)

final = clamp(composite, 0, 1)
```

| Sum of positive weights | 1.00 |
|---|---|
| Concentration penalty contribution range | [−0.05, 0] |
| Recency contribution range | [−0.05, +0.05] |
| Composite raw range before clamp | [−0.10, +1.00] |
| Composite final range | [0, 1] |

## Weights at a glance

| Signal | Weight | Direction | Range | Missing-data behavior |
|---|---|---|---|---|
| `hs_fit` | +0.30 | positive | [0, 1] | 0 when lead has no HS codes |
| `volume` | +0.25 | positive | [0, 1] | 0 when no shipments |
| `reachability` | +0.20 | positive | [0, 1] | 0 when no contacts |
| `seniority` | +0.10 | positive | [0.3, 1.0] | 0.3 baseline if any contact exists |
| `demand_validated` | +0.10 | positive | {0, 1} | 0 when no competitor edges |
| `recency` | +0.05 | signed | [−1, +1] | **0 (no opinion)** when `most_recent_shipment` is None |
| `concentration` | −0.05 | penalty | [0, 1] (magnitude) | **0 (no penalty)** when `max_supplier_share` is None |

Three multiplicative modifiers apply after the weighted sum:
- `hs_overlap_count == 0` and lead has HS codes → `× 0.5` (wrong-product floor)
- `not_interested` → `× 0.4` (soft re-engagement penalty, not a filter)
- `synced_to_crm` → `× 1.0` (default state, no modifier)

---

## Component specifications

### 1. `hs_fit` — product-category match

**Question answered**: does the lead actually buy what the factory makes?

```
hs_fit = |lead.hs_codes ∩ factory.hs_profile| / |lead.hs_codes|
```

| Term | Source |
|---|---|
| `lead.hs_codes` | `lead_attributes` rows where `kind = 'hs_code'`, sourced from `bol_payload.hsCodes` |
| `factory.hs_profile` | Top 8 HS codes counted across `competitor_attributes` (`kind = 'hs_code'`). Inferred at startup, cached. |
| Empty `lead.hs_codes` | Returns 0 |

Live persona on the real dataset: `950510, 950300, 392690, 392640, 940350, 950590, 611020, 940360` (Christmas decorations, plastic articles, festival goods).

**Worked values**

| Lead's HS codes | Overlap | Ratio |
|---|---|---|
| `[940542, 950510, 981795]` | 1 (`950510`) | 1/3 ≈ 0.33 |
| `[950300]` | 1 | 1.0 |
| `[392640, 950510]` | 2 | 1.0 |
| `[060490, 670210, 950510]` | 1 | 1/3 ≈ 0.33 |
| `[732599]` (HS not in profile) | 0 | 0.0 |

---

### 2. `volume` — how active an importer is this?

**Question answered**: is this lead a real trader or just a trace footprint?

```
volume = log(1 + total_shipments) / log(1 + 500)
       clamped to [0, 1]
```

| Term | Source |
|---|---|
| `total_shipments` | `leads.total_shipments`, sourced from `bol_payload.totalShipments` |
| Constant `500` | Reference cap. ≈ a major importer; anything more saturates. |

**Worked values**

| `total_shipments` | `volume` |
|---|---|
| 0 | 0.000 |
| 1 | 0.111 |
| 10 | 0.386 |
| 50 | 0.633 |
| 100 | 0.741 |
| 200 | 0.852 |
| 500 | 1.000 |
| 2000 | 1.000 (clamped) |

Log scaling prevents one whale (10,000 shipments) from dominating; doubling from 50 to 100 still meaningfully moves the score.

---

### 3. `reachability` — can we actually contact this lead?

**Question answered**: is anyone reachable by email at all, and how many contacts do we have?

```
reachability = 0.5 × (1 if any contact has email else 0)
             + 0.5 × min(1, contact_count / 3)
```

| Term | Source |
|---|---|
| Has-email check | Any `personnel.email` containing `@` for this `lead_id` |
| `contact_count` | Count of `personnel` rows for this `lead_id` |
| Saturation at 3 | More than 3 contacts adds no further reachability score |

**Worked values**

| Contacts | Any email? | `reachability` |
|---|---|---|
| 0 | — | 0.00 |
| 1 | no | 0.17 (0 + 0.5 × 1/3) |
| 1 | yes | 0.67 (0.5 + 0.5 × 1/3) |
| 2 | yes | 0.83 |
| 3 | yes | 1.00 |
| 5 | yes | 1.00 (saturated) |

---

### 4. `seniority` — do we have a decision-maker?

**Question answered**: is anyone among the contacts senior enough to make purchase decisions?

```
seniority = 1.0   if any contact's job_title matches the senior regex
          = 0.3   otherwise (baseline — some contact > no contact)
```

**Senior regex** (case-insensitive, word-boundary match):
```
CEO | CFO | COO | CTO | CIO
President | Founder | Owner | Partner
VP | Vice President
Director | Head | Chief | Principal | Managing
General Manager | Manager
Procurement | Sourcing | Buyer | Purchasing | Supply
```

**Worked values**

| Title example | Match? | `seniority` |
|---|---|---|
| `VP Sourcing` | yes | 1.0 |
| `CEO/President` | yes | 1.0 |
| `Procurement Manager` | yes | 1.0 |
| `Sales Associate` | no | 0.3 |
| (no title field populated) | no | 0.3 |
| (no contacts at all) | no | 0.3 |

The 0.3 baseline is intentional — having *any* contact, even an unmapped one, is worth a small lift over having zero.

---

### 5. `demand_validated` — is the lead a proven buyer of this category?

**Question answered**: do we have first-party evidence that someone is already selling this product to this importer?

```
demand_validated = 1.0   if competitor_count > 0
                 = 0.0   otherwise
```

| Term | Source |
|---|---|
| `competitor_count` | Distinct `shipments.exporter_name` rows where `importer_lead_id` resolves to this lead |

**Why presence is a positive signal, not a negative one**:
A known Chinese competitor shipping to this lead proves the lead buys this product category. They are a *qualified buyer*, not a cold prospect. Selling to a qualified buyer is easier than convincing a cold importer the category matters. Lock-in risk is handled separately by `concentration` (below).

**Worked values**

| Competitor edges resolved to lead | `demand_validated` |
|---|---|
| 0 (likely data gap on current dataset) | 0.0 |
| 1 | 1.0 |
| 7 | 1.0 |

The signal does not distinguish between 1 and 7 — that's a deliberate choice. Either we know someone ships to them or we don't. The *concentration* of those shipments is the next signal.

---

### 6. `recency` — fresh activity or gone cold?

**Question answered**: is the lead actively shipping right now, or are we looking at a stale footprint?

```
recency = +1.0                                              if days_since_recent ≤ 30
        = 1 − 2 × (days − 30) / (180 − 30)                 if 30 < days < 180   (linear)
        = −1.0                                              if days_since_recent ≥ 180
        =  0.0                                              if days_since_recent is None
```

| Term | Source |
|---|---|
| `days_since_recent` | UTC days between now and `leads.most_recent_shipment` |
| Fresh threshold (30 days) | Inside this window = clearly active |
| Stale threshold (180 days) | Outside this window = clearly gone cold |
| `None` | Returns 0 — we don't have an opinion |

**This is the only positive-weight signal that can go negative.** A stale lead actively *subtracts* from the composite (down to −0.05). A missing date contributes nothing.

**Worked values**

| `days_since_recent` | `recency` | Contribution to composite |
|---|---|---|
| 1 day | +1.000 | +0.050 |
| 30 days | +1.000 | +0.050 |
| 60 days | +0.600 | +0.030 |
| 90 days | +0.200 | +0.010 |
| 105 days | 0.000 | 0.000 |
| 120 days | −0.200 | −0.010 |
| 180 days | −1.000 | −0.050 |
| 365 days | −1.000 | −0.050 (clamped) |
| `None` | 0.000 | 0.000 |

---

### 7. `concentration` — is the lead locked in to one supplier?

**Question answered**: how easy is it to win share from the current incumbent?

```
concentration = 0                                            if max_share ≤ 30
              = (max_share − 30) / (100 − 30)               if 30 < max_share ≤ 100
              = 0                                            if max_share is None
```

| Term | Source |
|---|---|
| `max_share` | Top supplier's percentage of the lead's total BOL volume (0–100). Sourced from `competitor.us_customer_list_json.shipments_percents_supplier` and `lead.bol_payload.topSuppliers.share` |
| Floor 30% | Below this, supply landscape is competitive — no penalty |
| Ceiling 100% | At this, one supplier owns the lead — full penalty |
| `None` | Returns 0 — no penalty for missing data |

Multiplied by **negative weight `−0.05`**, so a high value *subtracts* from the composite.

**Worked values**

| `max_share` | `concentration` | Contribution to composite |
|---|---|---|
| `None` | 0.000 | 0.000 |
| 10% | 0.000 | 0.000 |
| 30% | 0.000 | 0.000 |
| 40% | 0.143 | −0.007 |
| 50% | 0.286 | −0.014 |
| 67.5% (QVC's case) | 0.535 | −0.027 |
| 80% | 0.714 | −0.036 |
| 100% | 1.000 | −0.050 |

The penalty scales smoothly: a 50% share is "moderate friction" (−0.014), a 100% lock-in is the full penalty (−0.05). It is never catastrophic on its own — combined with a missing `demand_validated` signal it could lower a lead by ~0.15, which is enough to swap mid-pack rankings but won't sink a strong-fit lead.

---

## Multiplicative modifiers

These apply *after* the weighted sum, before the final clamp.

### `hs_overlap_count == 0` and lead has HS codes → `× 0.5`

If the lead ships products and **none** of their HS codes intersect the factory profile, the composite is halved. The factory has nothing to pitch them.

This is a soft floor, not a hard filter — the lead is still ranked, still visible, still in the audit trail. The operator can still see it if they want, but it cannot beat a weakly-aligned in-product lead on the strength of volume + reachability alone.

Leads with empty HS codes entirely (no data) are not penalized here — `hs_fit` is already 0 for them, which depresses the score naturally.

### `not_interested` → `× 0.4`

A soft penalty, not a filter. The operator contacted this lead before and they declined. In real pipelines, re-engagement on a strong new signal is normal. The 0.4× multiplier lets a high-signal previously-declined lead resurface above weaker fresh leads while keeping it below average ones.

### `synced_to_crm` → `× 1.0`

No-op modifier. `synced_to_crm` is the default state on this dataset (every active lead carries it). Penalizing it would suppress everyone uniformly without changing rank, so we treat it as neutral.

---

## Worked example: QVC Inc (live #1, score 0.650)

```
Per-component normalized values + weighted contributions

   component         value      weight    contribution
   ─────────────────────────────────────────────────────
   hs_fit             0.400  ×  +0.30  =  +0.120
   volume             0.973  ×  +0.25  =  +0.243
   reachability       0.667  ×  +0.20  =  +0.133
   seniority          0.300  ×  +0.10  =  +0.030
   demand_validated   1.000  ×  +0.10  =  +0.100
   recency           +1.000  ×  +0.05  =  +0.050
   concentration      0.535  ×  −0.05  =  −0.027
                                          ─────────
   weighted sum                          = +0.649

Modifiers:
   hs_overlap_count > 0 → no mismatch penalty
   status != not_interested → no re-engagement penalty
   status == synced_to_crm → × 1.0 (no-op)

final = clamp(0.649, 0, 1) = 0.650
```

Reading the breakdown: QVC is volume-driven (`+0.243`) with strong reachability and validated demand, partly offset by a concentration risk (`−0.027`) from Century Distribution holding 67.5% supply. The LLM rationale correctly cites this as "incumbent entrenchment."

---

## Top-driver and top-penalty heuristics

The rationale layer asks `top_driver(components)` to pick what to anchor the reasoning on. Both helpers live in [`score.py`](backend/app/ranking/score.py).

| Function | Returns | Logic |
|---|---|---|
| `top_driver(components)` | the *positive* signal contributing most to the score | `argmax(weights[k] × components[k])` over signals where `weights[k] > 0` and `components[k] > 0` |
| `top_penalty(components)` | the signal contributing most *negatively*, or `None` | symmetric `argmin` over signals where the weighted contribution is negative |

For QVC: `top_driver = "volume"` (0.243), `top_penalty = "concentration"` (−0.027). The LLM prompt receives the driver as a hint.

---

## Tuning constants (live values)

All exported from [`score.py`](backend/app/ranking/score.py):

```python
VOLUME_LOG_CAP            = log(1 + 500)        # log-scale saturation
RECENCY_FRESH_DAYS        = 30                  # +1 below this
RECENCY_STALE_DAYS        = 180                 # −1 above this
CONCENTRATION_FLOOR_PCT   = 30.0                # penalty starts here
CONCENTRATION_CEIL_PCT    = 100.0               # full penalty here
CONTACT_SATURATION        = 3                   # reachability cap
SENIORITY_BASELINE        = 0.3                 # contact-but-no-senior baseline

NOT_INTERESTED_PENALTY    = 0.4                 # composite multiplier
HS_MISMATCH_PENALTY       = 0.5                 # composite multiplier
```

These are intentionally hand-set and exposed at the top of the module. The feedback-loop plan (see README, "Closing the feedback loop") describes how they would be learned from operator selections once enough labels accumulate.

---

## What the operator sees

The `components` dict for every ranked lead is returned by `GET /api/leads/ranked` and rendered as the **components** column on the Deliverable tab. Each row of bars is a *normalized* component value (the left-hand-side numbers in this doc), not the weighted contribution. The composite score in the leftmost column is what the bars combine to (with weights + modifiers applied).

Cross-reference: `GET /api/ranking/events` exposes the live `WEIGHTS` dict so a reviewer can verify the weights in this doc match the running system. If they ever disagree — file a bug.
