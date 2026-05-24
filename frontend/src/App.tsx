import React, { useEffect, useMemo, useState } from 'react'
import {
  Background,
  Controls,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type EdgeMouseHandler,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from './lib/api'

type Row = Record<string, unknown>
type Page = 'deliverable' | 'tables' | 'graph' | 'trace'
type TableKey =
  | 'leads'
  | 'personnel'
  | 'competitors'
  | 'lead_attributes'
  | 'competitor_attributes'
  | 'shipments'

const LEAD_COLS = [
  'id', 'company_name', 'city_state', 'data_source', 'website', 'status',
  'legacy_score', 'created_at', 'updated_at', 'is_test',
  'total_shipments', 'matching_shipments', 'most_recent_shipment',
  'growth_12m_pct', 'china_concentration',
  'bol_suppliers_json', 'bol_payload_json', 'bol_recent_json',
]

const PERSONNEL_COLS = [
  'id', 'first_name', 'last_name', 'full_name', 'company_name', 'data_source',
  'job_title', 'email', 'phone', 'lead_id', 'legacy_score', 'is_test',
  'created_at', 'updated_at',
]

const COMPETITOR_COLS = [
  'id', 'slug', 'company_name', 'company_name_cn', 'country', 'country_code',
  'addresses_text', 'city', 'hs_codes_pg_array', 'importer_contact_count',
  'revenue_or_volume_usd', 'metadata_json', 'products_text_pg_array',
  'count_shipments', 'logistics_metadata', 'risk_rating', 'flag_count',
  'latitude_or_coord', 'shipment_history_json', 'us_customer_list_json',
  'supplier_variants_pg_array', 'recent_shipment_bills_json',
  'logistics_partners_json', 'is_archived', 'created_at', 'updated_at',
]

const LEAD_ATTR_COLS = ['id', 'lead_id', 'kind', 'value', 'rank']
const COMP_ATTR_COLS = ['id', 'competitor_id', 'kind', 'value']
const SHIPMENT_COLS  = [
  'id', 'importer_lead_id', 'exporter_competitor_id',
  'importer_name', 'exporter_name',
  'teu', 'total_shipments', 'shipments_12m',
  'share_pct', 'trend_pct', 'most_recent_shipment',
  'seen_in_lead_payload', 'seen_in_lead_suppliers', 'seen_in_competitor_customers',
]

const TABLE_CONFIG: Record<TableKey, { label: string; cols: string[]; endpoint: string }> = {
  leads:                 { label: 'leads',                 cols: LEAD_COLS,      endpoint: '/tables/leads' },
  personnel:             { label: 'personnel',             cols: PERSONNEL_COLS, endpoint: '/tables/personnel' },
  competitors:           { label: 'competitors',           cols: COMPETITOR_COLS,endpoint: '/tables/competitors' },
  lead_attributes:       { label: 'lead_attributes',       cols: LEAD_ATTR_COLS, endpoint: '/tables/lead_attributes' },
  competitor_attributes: { label: 'competitor_attributes', cols: COMP_ATTR_COLS, endpoint: '/tables/competitor_attributes' },
  shipments:             { label: 'shipments',             cols: SHIPMENT_COLS,  endpoint: '/tables/shipments' },
}

type RationaleSource = 'pending' | 'llm' | 'fallback'

type RankedLead = {
  lead_id: string
  company: string
  score: number
  components: Record<string, number>
  features: Record<string, unknown>
  reasoning: string
  rationale_source: RationaleSource
  factuality_ok: boolean
  selected: boolean
}

type RankedResponse = {
  rows: RankedLead[]
  stats: Record<string, number>
  pending_count: number
}

type LLMCallTrace = {
  model: string
  latency_ms: number
  input_tokens: number | null
  output_tokens: number | null
  cache_read_input_tokens: number | null
  cache_creation_input_tokens: number | null
  raw_response_text: string | null
  error: string | null
}

type FactualityCheck = {
  ok: boolean
  cited_hs_codes: string[]
  cited_ports: string[]
  cited_competitors: string[]
  cited_titles: string[]
  invalid_hs_codes: string[]
  has_anchor: boolean
  reason: string | null
}

type RationaleTrace = {
  lead_id: string
  generated_at: number
  user_payload: string
  system_prompt_excerpt: string
  llm: LLMCallTrace | null
  factuality: FactualityCheck | null
  final_source: string
  final_text: string
  fallback_text: string | null
}

type TraceEvent = {
  seq: number
  ts: number
  kind: string
  summary: string
  payload: Record<string, unknown>
}

type EventsResponse = {
  events: TraceEvent[]
  trace_count: number
  cache_stats: Record<string, number>
  weights: Record<string, number>
}

function renderCell(v: unknown): { display: string; title: string; muted: boolean } {
  if (v === null || v === undefined || v === '') return { display: '—', title: '', muted: true }
  if (typeof v === 'boolean') return { display: String(v), title: String(v), muted: false }
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  if (s.length > 80) return { display: s.slice(0, 80) + '…', title: s, muted: false }
  return { display: s, title: s, muted: false }
}

function DataTable({ columns, rows }: { columns: string[]; rows: Row[] }) {
  return (
    <div className="h-full overflow-auto rounded-lg border border-slate-200 bg-white shadow-sm">
      <table className="min-w-full text-xs">
        <thead className="sticky top-0 z-10 bg-slate-50 text-slate-600 shadow-sm">
          <tr>
            {columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-3 py-2 text-left font-medium">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={(r.id as string) ?? i} className="border-t border-slate-100 hover:bg-slate-50">
              {columns.map((c) => {
                const cell = renderCell(r[c])
                return (
                  <td
                    key={c}
                    title={cell.title}
                    className={
                      'px-3 py-1.5 align-top font-mono ' +
                      (cell.muted ? 'text-slate-300' : 'text-slate-800')
                    }
                  >
                    {cell.display}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ComponentBar({ name, value }: { name: string; value: number }) {
  const pct = Math.round(value * 100)
  return (
    <div className="flex items-center gap-1.5 text-[10px]">
      <span className="w-20 shrink-0 text-slate-500">{name}</span>
      <div className="relative h-1.5 w-24 overflow-hidden rounded-full bg-slate-100">
        <div className="absolute inset-y-0 left-0 bg-slate-700" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right font-mono tabular-nums text-slate-600">{pct}</span>
    </div>
  )
}

function RationaleCell({ row }: { row: RankedLead }) {
  if (row.rationale_source === 'pending') {
    return (
      <div className="flex items-start gap-2">
        <span
          aria-label="generating LLM rationale"
          className="mt-1 inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600"
        />
        <div className="flex-1">
          <span className="text-slate-400 italic">{row.reasoning}</span>
          <span className="ml-2 text-[10px] uppercase tracking-wider text-slate-400">generating…</span>
        </div>
      </div>
    )
  }
  if (row.rationale_source === 'llm') {
    return (
      <div>
        <span className="text-slate-800">{row.reasoning}</span>
        {!row.factuality_ok && (
          <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
            factuality?
          </span>
        )}
      </div>
    )
  }
  // fallback
  return (
    <div>
      <span className="text-slate-700">{row.reasoning}</span>
      <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        fallback
      </span>
    </div>
  )
}

const POLL_INTERVAL_MS = 2000

const SCORE_WEIGHTS: Record<string, number> = {
  volume: 0.20,
  recency: 0.15,
  hs_fit: 0.25,
  competitive: 0.15,
  reachability: 0.15,
  seniority: 0.10,
}

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString()
}

function StageHeader({ n, title, badge }: { n: number; title: string; badge?: React.ReactNode }) {
  return (
    <div className="mb-1.5 flex items-center gap-2">
      <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 text-[10px] font-semibold text-white">
        {n}
      </span>
      <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-700">{title}</h4>
      {badge}
    </div>
  )
}

function SourceBadge({ source }: { source: string }) {
  const map: Record<string, { bg: string; fg: string }> = {
    llm: { bg: 'bg-emerald-100', fg: 'text-emerald-800' },
    fallback_no_key: { bg: 'bg-slate-200', fg: 'text-slate-700' },
    fallback_error: { bg: 'bg-amber-100', fg: 'text-amber-800' },
    fallback_factuality: { bg: 'bg-red-100', fg: 'text-red-800' },
    pending: { bg: 'bg-slate-100', fg: 'text-slate-500' },
    fallback: { bg: 'bg-slate-200', fg: 'text-slate-700' },
  }
  const c = map[source] ?? { bg: 'bg-slate-100', fg: 'text-slate-600' }
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ${c.bg} ${c.fg}`}>
      {source}
    </span>
  )
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-0.5">
      <span className="w-44 shrink-0 text-[11px] text-slate-500">{k}</span>
      <span className="min-w-0 flex-1 font-mono text-[11px] text-slate-800 break-words">{v ?? '—'}</span>
    </div>
  )
}

function TraceExpander({ row, trace, error }: { row: RankedLead; trace: RationaleTrace | null; error: string | null }) {
  if (error) return <div className="px-4 py-3 text-xs text-red-600">trace error: {error}</div>
  if (!trace) return <div className="px-4 py-3 text-xs text-slate-500">loading trace…</div>

  const f = row.features as Record<string, unknown>
  const synced = !!f.is_synced_to_crm

  return (
    <div className="space-y-5 bg-slate-50 px-5 py-4 text-[11px]">
      {/* Stage 1: Features */}
      <div>
        <StageHeader n={1} title="Features (deterministic extraction)" />
        <div className="rounded-md border border-slate-200 bg-white p-3">
          {(['status', 'total_shipments', 'matching_shipments', 'days_since_recent', 'lead_hs_codes', 'top_ports', 'hs_overlap', 'hs_overlap_ratio', 'competitor_count', 'top_competitor_name', 'max_competitor_share_pct', 'contact_count', 'has_email', 'senior_contact_title', 'senior_contact_name'] as const).map((k) => {
            const v = f[k]
            const display = Array.isArray(v) ? (v.length ? v.join(', ') : '—') : v === null || v === undefined || v === '' ? '—' : String(v)
            return <KV key={k} k={k} v={display} />
          })}
        </div>
      </div>

      {/* Stage 2: Scoring */}
      <div>
        <StageHeader n={2} title="Scoring (weighted composition)" />
        <div className="rounded-md border border-slate-200 bg-white p-3">
          <div className="grid grid-cols-[1fr_60px_60px_80px] gap-x-4 gap-y-1 font-mono">
            <div className="text-[10px] uppercase tracking-wider text-slate-500">component</div>
            <div className="text-right text-[10px] uppercase tracking-wider text-slate-500">value</div>
            <div className="text-right text-[10px] uppercase tracking-wider text-slate-500">× weight</div>
            <div className="text-right text-[10px] uppercase tracking-wider text-slate-500">= contrib.</div>
            {Object.entries(row.components).map(([k, v]) => {
              const w = SCORE_WEIGHTS[k] ?? 0
              const contrib = v * w
              return (
                <React.Fragment key={k}>
                  <div className="text-slate-700">{k}</div>
                  <div className="text-right text-slate-700">{v.toFixed(3)}</div>
                  <div className="text-right text-slate-500">{w.toFixed(2)}</div>
                  <div className="text-right font-semibold text-slate-900">{contrib.toFixed(3)}</div>
                </React.Fragment>
              )
            })}
            <div className="col-span-3 border-t border-slate-200 pt-1 text-right text-[10px] uppercase tracking-wider text-slate-500">
              {synced && 'composite × 0.5 (synced_to_crm penalty) ='}
              {!synced && 'composite ='}
            </div>
            <div className="border-t border-slate-200 pt-1 text-right font-mono font-semibold text-slate-900">{row.score.toFixed(3)}</div>
          </div>
        </div>
      </div>

      {/* Stage 3: LLM prompt */}
      <div>
        <StageHeader n={3} title="LLM prompt (input to Claude)" />
        <div className="space-y-2 rounded-md border border-slate-200 bg-white p-3">
          <KV k="system_prompt (excerpt)" v={<span className="italic text-slate-600">{trace.system_prompt_excerpt}</span>} />
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-500">user_payload</div>
            <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2 font-mono text-[10px] leading-relaxed text-slate-700">{trace.user_payload}</pre>
          </div>
        </div>
      </div>

      {/* Stage 4: LLM response */}
      <div>
        <StageHeader
          n={4}
          title="LLM response"
          badge={trace.llm ? null : <span className="text-[10px] text-slate-500">(skipped — no API key)</span>}
        />
        {trace.llm ? (
          <div className="rounded-md border border-slate-200 bg-white p-3">
            <KV k="model" v={trace.llm.model} />
            <KV k="latency_ms" v={trace.llm.latency_ms} />
            <KV k="input_tokens" v={trace.llm.input_tokens} />
            <KV k="output_tokens" v={trace.llm.output_tokens} />
            <KV k="cache_read_input_tokens" v={trace.llm.cache_read_input_tokens} />
            <KV k="cache_creation_input_tokens" v={trace.llm.cache_creation_input_tokens} />
            {trace.llm.error && <KV k="error" v={<span className="text-red-700">{trace.llm.error}</span>} />}
            {trace.llm.raw_response_text != null && (
              <div className="mt-2">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">raw_response_text</div>
                <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2 font-mono text-[10px] leading-relaxed text-slate-700">{trace.llm.raw_response_text}</pre>
              </div>
            )}
          </div>
        ) : (
          <div className="rounded-md border border-slate-200 bg-white p-3 text-slate-500">
            ANTHROPIC_API_KEY not set; pipeline went straight to deterministic fallback.
          </div>
        )}
      </div>

      {/* Stage 5: Factuality */}
      <div>
        <StageHeader
          n={5}
          title="Factuality check"
          badge={
            trace.factuality
              ? trace.factuality.ok
                ? <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800">PASS</span>
                : <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-800">FAIL</span>
              : <span className="text-[10px] text-slate-500">(n/a — no LLM call)</span>
          }
        />
        {trace.factuality ? (
          <div className="rounded-md border border-slate-200 bg-white p-3">
            <KV k="cited_hs_codes" v={trace.factuality.cited_hs_codes.join(', ') || '—'} />
            <KV k="invalid_hs_codes" v={trace.factuality.invalid_hs_codes.length ? <span className="text-red-700">{trace.factuality.invalid_hs_codes.join(', ')}</span> : '—'} />
            <KV k="cited_ports" v={trace.factuality.cited_ports.join(', ') || '—'} />
            <KV k="cited_competitors" v={trace.factuality.cited_competitors.join(', ') || '—'} />
            <KV k="cited_titles" v={trace.factuality.cited_titles.join(', ') || '—'} />
            <KV k="has_anchor" v={String(trace.factuality.has_anchor)} />
            {trace.factuality.reason && <KV k="reject_reason" v={<span className="text-red-700">{trace.factuality.reason}</span>} />}
          </div>
        ) : (
          <div className="rounded-md border border-slate-200 bg-white p-3 text-slate-500">
            no LLM call to verify.
          </div>
        )}
      </div>

      {/* Stage 6: Outcome */}
      <div>
        <StageHeader n={6} title="Outcome" badge={<SourceBadge source={trace.final_source} />} />
        <div className="rounded-md border border-slate-200 bg-white p-3">
          <KV k="final_text" v={<span className="text-slate-900">{trace.final_text}</span>} />
          {trace.fallback_text && trace.fallback_text !== trace.final_text && (
            <KV k="fallback_text (alt)" v={<span className="text-slate-600">{trace.fallback_text}</span>} />
          )}
          <KV k="generated_at" v={fmtTs(trace.generated_at)} />
        </div>
      </div>
    </div>
  )
}

function DeliverablePage() {
  const [resp, setResp] = useState<RankedResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [traces, setTraces] = useState<Record<string, RationaleTrace>>({})
  const [traceErrors, setTraceErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const fetchOnce = () => {
      api
        .get<RankedResponse>('/leads/ranked?limit=50')
        .then((r) => {
          if (stopped) return
          setResp(r)
          if (r.pending_count > 0) {
            timer = setTimeout(fetchOnce, POLL_INTERVAL_MS)
          }
        })
        .catch((e) => {
          if (stopped) return
          setError(String(e))
        })
    }

    fetchOnce()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  const fetchTrace = (leadId: string, force = false) => {
    if (!force && traces[leadId]) return
    api
      .get<RationaleTrace>(`/leads/${leadId}/trace`)
      .then((t) => setTraces((s) => ({ ...s, [leadId]: t })))
      .catch((e) => setTraceErrors((s) => ({ ...s, [leadId]: String(e) })))
  }

  const toggleExpand = (leadId: string, source: RationaleSource) => {
    setExpanded((s) => {
      const next = { ...s, [leadId]: !s[leadId] }
      return next
    })
    if (!expanded[leadId] && source !== 'pending') fetchTrace(leadId)
  }

  useEffect(() => {
    // When a pending row finishes, refresh its trace if currently expanded.
    if (!resp) return
    for (const r of resp.rows) {
      if (expanded[r.lead_id] && r.rationale_source !== 'pending' && !traces[r.lead_id]) {
        fetchTrace(r.lead_id)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resp])

  const rows = resp?.rows ?? null
  const pending = resp?.pending_count ?? 0
  const llmDone = resp ? (resp.stats.llm ?? 0) : 0
  const fallbackDone = resp ? (resp.stats.fallback ?? 0) : 0

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="shrink-0">
        <h2 className="text-lg font-medium tracking-tight">Ranked outreach targets</h2>
        <p className="text-sm text-slate-600">
          Top US importers worth chasing for the factory. Score combines volume, recency, HS-code fit, competitor pressure, reachability, and seniority.
        </p>
        {resp && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
            <span>{rows?.length ?? 0} ranked</span>
            <span className="text-emerald-700">{llmDone} LLM</span>
            <span className="text-slate-500">{fallbackDone} fallback</span>
            {pending > 0 && (
              <span className="flex items-center gap-1.5 text-slate-700">
                <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700" />
                {pending} generating…
              </span>
            )}
          </div>
        )}
      </div>

      {error && <p className="shrink-0 text-red-600">error: {error}</p>}
      {!error && !rows && <p className="shrink-0 text-slate-500">loading…</p>}

      {rows && (
        <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full text-sm">
            <thead className="sticky top-0 z-10 bg-slate-50 text-slate-600 shadow-sm">
              <tr>
                <th className="w-8 px-2 py-2 text-left font-medium"></th>
                <th className="w-12 px-3 py-2 text-left font-medium">✓</th>
                <th className="w-20 px-3 py-2 text-left font-medium">score</th>
                <th className="w-64 px-3 py-2 text-left font-medium">company</th>
                <th className="w-64 px-3 py-2 text-left font-medium">components</th>
                <th className="px-3 py-2 text-left font-medium">reasoning</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const isOpen = !!expanded[r.lead_id]
                return (
                  <React.Fragment key={r.lead_id}>
                    <tr
                      className={
                        'border-t border-slate-100 cursor-pointer ' +
                        (isOpen ? 'bg-slate-50' : 'hover:bg-slate-50')
                      }
                      onClick={() => toggleExpand(r.lead_id, r.rationale_source)}
                    >
                      <td className="px-2 py-2 align-top text-slate-400">
                        <span className="inline-block w-3 select-none">{isOpen ? '▾' : '▸'}</span>
                      </td>
                      <td className="px-3 py-2 align-top" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={!!selected[r.lead_id]}
                          onChange={(e) => setSelected((s) => ({ ...s, [r.lead_id]: e.target.checked }))}
                          className="h-4 w-4 cursor-pointer accent-slate-900"
                        />
                      </td>
                      <td className="px-3 py-2 align-top font-mono text-slate-900">{r.score.toFixed(3)}</td>
                      <td className="px-3 py-2 align-top font-medium text-slate-900">{r.company}</td>
                      <td className="px-3 py-2 align-top">
                        <div className="space-y-0.5">
                          {Object.entries(r.components).map(([k, v]) => (
                            <ComponentBar key={k} name={k} value={v} />
                          ))}
                        </div>
                      </td>
                      <td className="px-3 py-2 align-top">
                        <RationaleCell row={r} />
                      </td>
                    </tr>
                    {isOpen && (
                      <tr key={r.lead_id + ':trace'} className="border-t border-slate-100">
                        <td colSpan={6} className="p-0">
                          <TraceExpander
                            row={r}
                            trace={traces[r.lead_id] ?? null}
                            error={traceErrors[r.lead_id] ?? null}
                          />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="shrink-0 text-xs text-slate-500">
        {Object.values(selected).filter(Boolean).length} selected for outreach
      </div>
    </div>
  )
}

function TablesPage() {
  const [active, setActive] = useState<TableKey>('leads')
  const [data, setData] = useState<Record<TableKey, Row[] | null>>({
    leads: null,
    personnel: null,
    competitors: null,
    lead_attributes: null,
    competitor_attributes: null,
    shipments: null,
  })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (data[active] !== null) return
    setError(null)
    api.get<Row[]>(TABLE_CONFIG[active].endpoint)
      .then((rows) => setData((d) => ({ ...d, [active]: rows })))
      .catch((e) => setError(String(e)))
  }, [active, data])

  const rows = data[active]
  const cfg = TABLE_CONFIG[active]

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex shrink-0 items-center gap-3">
        <label htmlFor="table-select" className="text-sm font-medium text-slate-700">Table:</label>
        <select
          id="table-select"
          value={active}
          onChange={(e) => setActive(e.target.value as TableKey)}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-slate-500 focus:outline-none"
        >
          {(Object.keys(TABLE_CONFIG) as TableKey[]).map((k) => (
            <option key={k} value={k}>{TABLE_CONFIG[k].label}</option>
          ))}
        </select>
        <span className="text-xs text-slate-500">
          {rows ? `${rows.length} rows` : 'loading…'}
        </span>
      </div>

      <div className="min-h-0 flex-1">
        {error && <p className="text-red-600">error: {error}</p>}
        {!error && rows && <DataTable columns={cfg.cols} rows={rows} />}
      </div>
    </div>
  )
}

type Shipment = {
  id: number
  importer_lead_id: string | null
  exporter_competitor_id: string | null
  importer_name: string
  exporter_name: string
  teu: number | null
  total_shipments: number | null
  shipments_12m: number | null
  share_pct: number | null
  trend_pct: number | null
  most_recent_shipment: string | null
  seen_in_lead_payload: boolean
  seen_in_lead_suppliers: boolean
  seen_in_competitor_customers: boolean
}

type GraphFilter = 'resolved_exporter' | 'both_resolved' | 'all'
type ViewMode = 'graph' | 'list'

function GraphPage() {
  const [shipments, setShipments] = useState<Shipment[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<GraphFilter>('resolved_exporter')
  const [view, setView] = useState<ViewMode>('graph')
  const [hoverEdge, setHoverEdge] = useState<Shipment | null>(null)
  const [hoverPos, setHoverPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 })

  useEffect(() => {
    api.get<Shipment[]>('/tables/shipments')
      .then(setShipments)
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return <p className="text-red-600">error: {error}</p>
  if (!shipments) return <p className="text-slate-500">loading…</p>

  const filtered = shipments.filter((s) => {
    if (filter === 'both_resolved') return s.importer_lead_id && s.exporter_competitor_id
    if (filter === 'resolved_exporter') return !!s.exporter_competitor_id
    return true
  })

  const totals = {
    edges: shipments.length,
    importerResolved: shipments.filter((s) => s.importer_lead_id).length,
    exporterResolved: shipments.filter((s) => s.exporter_competitor_id).length,
    bothResolved: shipments.filter((s) => s.importer_lead_id && s.exporter_competitor_id).length,
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="shrink-0">
        <h2 className="text-lg font-medium tracking-tight">Shipping graph</h2>
        <p className="text-sm text-slate-600">
          Bipartite graph: <span className="font-medium">exporter</span> (Chinese factory) → <span className="font-medium">importer</span> (US company).
          Edges deduped across <code className="rounded bg-slate-100 px-1">lead.bol_payload</code>,{' '}
          <code className="rounded bg-slate-100 px-1">lead.bol_suppliers</code>, and{' '}
          <code className="rounded bg-slate-100 px-1">competitor.us_customer_list</code>.
        </p>
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-600">
          <span>edges: <span className="font-mono text-slate-900">{totals.edges}</span></span>
          <span>importer FK: <span className="font-mono text-slate-900">{totals.importerResolved}</span></span>
          <span>exporter FK: <span className="font-mono text-slate-900">{totals.exporterResolved}</span></span>
          <span className="text-emerald-700">both: <span className="font-mono">{totals.bothResolved}</span></span>
          <span className="ml-auto">showing <span className="font-mono text-slate-900">{filtered.length}</span></span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">filter:</span>
          {([
            ['resolved_exporter', 'exporter resolved'],
            ['both_resolved', 'both resolved'],
            ['all', 'all (dense)'],
          ] as [GraphFilter, string][]).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setFilter(k)}
              className={
                'rounded-md px-2 py-1 ' +
                (filter === k ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200')
              }
            >
              {label}
            </button>
          ))}
          <span className="ml-4 text-slate-500">view:</span>
          {(['graph', 'list'] as ViewMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setView(m)}
              className={
                'rounded-md px-2 py-1 ' +
                (view === m ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200')
              }
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        {view === 'graph' ? (
          <BipartiteFlow
            edges={filtered}
            onHoverEdge={(s, pos) => {
              setHoverEdge(s)
              if (pos) setHoverPos(pos)
            }}
          />
        ) : (
          <AdjacencyList edges={filtered} />
        )}

        {view === 'graph' && hoverEdge && (
          <EdgeTooltip edge={hoverEdge} x={hoverPos.x} y={hoverPos.y} />
        )}
      </div>
    </div>
  )
}

// ---------- bipartite graph using @xyflow/react ----------

const COL_X_LEFT = 0
const COL_X_RIGHT = 800
const ROW_STEP = 28
const NODE_W = 220

function BipartiteFlow({
  edges,
  onHoverEdge,
}: {
  edges: Shipment[]
  onHoverEdge: (s: Shipment | null, pos?: { x: number; y: number }) => void
}) {
  const { nodes, flowEdges } = useMemo(() => {
    const expCount = new Map<string, number>()
    const impCount = new Map<string, number>()
    const expResolved = new Map<string, boolean>()
    const impResolved = new Map<string, boolean>()

    for (const e of edges) {
      expCount.set(e.exporter_name, (expCount.get(e.exporter_name) ?? 0) + 1)
      impCount.set(e.importer_name, (impCount.get(e.importer_name) ?? 0) + 1)
      if (e.exporter_competitor_id) expResolved.set(e.exporter_name, true)
      if (e.importer_lead_id) impResolved.set(e.importer_name, true)
    }

    const expSorted = [...expCount.keys()].sort((a, b) => expCount.get(b)! - expCount.get(a)!)
    const impSorted = [...impCount.keys()].sort((a, b) => impCount.get(b)! - impCount.get(a)!)

    const expY = new Map<string, number>()
    expSorted.forEach((n, i) => expY.set(n, i * ROW_STEP))
    const impY = new Map<string, number>()
    impSorted.forEach((n, i) => impY.set(n, i * ROW_STEP))

    const teuMax = Math.max(1, ...edges.map((e) => e.teu ?? 0))

    const nodes: Node[] = [
      ...expSorted.map((name): Node => ({
        id: `e:${name}`,
        position: { x: COL_X_LEFT, y: expY.get(name)! },
        data: { label: name, count: expCount.get(name) },
        type: 'default',
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: true,
        style: {
          width: NODE_W,
          fontSize: 11,
          padding: '4px 8px',
          background: expResolved.get(name) ? '#ecfdf5' : '#f8fafc',
          color: expResolved.get(name) ? '#047857' : '#334155',
          border: '1px solid ' + (expResolved.get(name) ? '#10b981' : '#cbd5e1'),
          borderRadius: 6,
          textAlign: 'right' as const,
        },
      })),
      ...impSorted.map((name): Node => ({
        id: `i:${name}`,
        position: { x: COL_X_RIGHT, y: impY.get(name)! },
        data: { label: name, count: impCount.get(name) },
        type: 'default',
        sourcePosition: Position.Left,
        targetPosition: Position.Right,
        draggable: true,
        style: {
          width: NODE_W,
          fontSize: 11,
          padding: '4px 8px',
          background: impResolved.get(name) ? '#ecfdf5' : '#f8fafc',
          color: impResolved.get(name) ? '#047857' : '#334155',
          border: '1px solid ' + (impResolved.get(name) ? '#10b981' : '#cbd5e1'),
          borderRadius: 6,
          textAlign: 'left' as const,
        },
      })),
    ]

    const flowEdges: Edge[] = edges.map((e) => {
      const both = !!(e.importer_lead_id && e.exporter_competitor_id)
      const expOk = !!e.exporter_competitor_id
      const color = both ? '#059669' : expOk ? '#475569' : '#cbd5e1'
      const t = e.teu ?? 1
      const width = 0.6 + 2.4 * (Math.log1p(t) / Math.log1p(teuMax))
      return {
        id: `s:${e.id}`,
        source: `e:${e.exporter_name}`,
        target: `i:${e.importer_name}`,
        type: 'bezier',
        animated: false,
        data: { shipment: e },
        style: {
          stroke: color,
          strokeWidth: width,
          opacity: 0.55,
        },
      }
    })

    return { nodes, flowEdges }
  }, [edges])

  const onEdgeEnter: EdgeMouseHandler = (ev, edge) => {
    const s = (edge.data as { shipment?: Shipment } | undefined)?.shipment
    if (s) onHoverEdge(s, { x: ev.clientX, y: ev.clientY })
  }
  const onEdgeLeave: EdgeMouseHandler = () => onHoverEdge(null)
  const onEdgeMove: EdgeMouseHandler = (ev, edge) => {
    const s = (edge.data as { shipment?: Shipment } | undefined)?.shipment
    if (s) onHoverEdge(s, { x: ev.clientX, y: ev.clientY })
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={flowEdges}
      fitView
      minZoom={0.1}
      maxZoom={3}
      onEdgeMouseEnter={onEdgeEnter}
      onEdgeMouseMove={onEdgeMove}
      onEdgeMouseLeave={onEdgeLeave}
      nodesConnectable={false}
      edgesFocusable={false}
      nodesFocusable={false}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={20} color="#e2e8f0" />
      <MiniMap pannable zoomable nodeStrokeWidth={2} />
      <Controls showInteractive={false} />
    </ReactFlow>
  )
}

function EdgeTooltip({ edge, x, y }: { edge: Shipment; x: number; y: number }) {
  const sources = [
    edge.seen_in_lead_payload && 'lead.bol_payload',
    edge.seen_in_lead_suppliers && 'lead.bol_suppliers',
    edge.seen_in_competitor_customers && 'competitor.us_customer_list',
  ].filter(Boolean) as string[]

  return (
    <div
      className="pointer-events-none fixed z-50 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg"
      style={{ left: x + 12, top: y + 12, maxWidth: 360 }}
    >
      <div className="font-medium text-slate-900">{edge.exporter_name}</div>
      <div className="text-slate-400">→</div>
      <div className="font-medium text-slate-900">{edge.importer_name}</div>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[11px]">
        {edge.teu != null && <><span className="text-slate-500">teu</span><span>{edge.teu.toFixed(2)}</span></>}
        {edge.total_shipments != null && <><span className="text-slate-500">total ships</span><span>{edge.total_shipments}</span></>}
        {edge.shipments_12m != null && <><span className="text-slate-500">ships 12m</span><span>{edge.shipments_12m}</span></>}
        {edge.share_pct != null && <><span className="text-slate-500">share %</span><span>{edge.share_pct.toFixed(2)}</span></>}
        {edge.trend_pct != null && <><span className="text-slate-500">trend %</span><span>{edge.trend_pct.toFixed(1)}</span></>}
        {edge.most_recent_shipment && <><span className="text-slate-500">last ship</span><span>{edge.most_recent_shipment.slice(0, 10)}</span></>}
      </div>
      <div className="mt-2 text-[10px] text-slate-500">
        FK importer: {edge.importer_lead_id ? <span className="text-emerald-700">resolved</span> : 'unresolved'}
        {' · '}
        FK exporter: {edge.exporter_competitor_id ? <span className="text-emerald-700">resolved</span> : 'unresolved'}
      </div>
      <div className="mt-1 text-[10px] text-slate-500">
        sources: {sources.join(', ')}
      </div>
    </div>
  )
}

// ---------- list view (previous adjacency list) ----------

function AdjacencyList({ edges }: { edges: Shipment[] }) {
  const groups = new Map<string, Shipment[]>()
  for (const s of edges) {
    if (!groups.has(s.exporter_name)) groups.set(s.exporter_name, [])
    groups.get(s.exporter_name)!.push(s)
  }
  const ordered = [...groups.entries()].sort((a, b) => {
    const aRes = a[1].some((x) => x.exporter_competitor_id) ? 1 : 0
    const bRes = b[1].some((x) => x.exporter_competitor_id) ? 1 : 0
    if (aRes !== bRes) return bRes - aRes
    return b[1].length - a[1].length
  })
  return (
    <ul className="h-full divide-y divide-slate-100 overflow-auto">
      {ordered.map(([exporter, eds]) => {
        const expResolved = eds.some((e) => e.exporter_competitor_id)
        return (
          <li key={exporter} className="px-4 py-3">
            <div className="flex items-baseline gap-2">
              <span className={'font-medium ' + (expResolved ? 'text-emerald-700' : 'text-slate-700')}>
                {exporter}
              </span>
              <span className="text-xs text-slate-400">({eds.length})</span>
            </div>
            <ul className="ml-4 mt-1 space-y-0.5">
              {eds.sort((a, b) => (b.teu ?? 0) - (a.teu ?? 0)).map((e) => {
                const impResolved = !!e.importer_lead_id
                return (
                  <li
                    key={e.id}
                    className={
                      'flex flex-wrap items-baseline gap-x-3 text-xs ' +
                      (impResolved && expResolved ? 'rounded bg-emerald-50 px-2 py-1 font-medium text-emerald-800' : 'text-slate-600')
                    }
                  >
                    <span className="text-slate-400">→</span>
                    <span className={impResolved ? 'font-medium text-slate-900' : ''}>{e.importer_name}</span>
                    {e.teu != null && <span className="font-mono text-slate-500">teu={e.teu.toFixed(1)}</span>}
                    {e.total_shipments != null && <span className="font-mono text-slate-500">ships={e.total_shipments}</span>}
                    {e.share_pct != null && <span className="font-mono text-slate-500">share={e.share_pct.toFixed(1)}%</span>}
                  </li>
                )
              })}
            </ul>
          </li>
        )
      })}
    </ul>
  )
}

const EVENT_KIND_COLOR: Record<string, string> = {
  ingest: 'bg-sky-100 text-sky-800',
  persona_inferred: 'bg-indigo-100 text-indigo-800',
  ranking_request: 'bg-slate-200 text-slate-700',
  batch_dispatched: 'bg-amber-100 text-amber-800',
  batch_complete: 'bg-emerald-100 text-emerald-800',
  llm_call: 'bg-violet-100 text-violet-800',
  factuality_fail: 'bg-red-100 text-red-800',
  fallback: 'bg-slate-200 text-slate-700',
  cache_cleared: 'bg-slate-100 text-slate-600',
}

function EventKindBadge({ kind }: { kind: string }) {
  const c = EVENT_KIND_COLOR[kind] ?? 'bg-slate-100 text-slate-600'
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ${c}`}>
      {kind}
    </span>
  )
}

function EventRow({ ev }: { ev: TraceEvent }) {
  const [open, setOpen] = useState(false)
  const hasPayload = ev.payload && Object.keys(ev.payload).length > 0
  return (
    <>
      <tr
        className={
          'border-t border-slate-100 ' +
          (hasPayload ? 'cursor-pointer hover:bg-slate-50' : '')
        }
        onClick={() => hasPayload && setOpen((o) => !o)}
      >
        <td className="w-12 px-3 py-1.5 align-top font-mono text-[11px] text-slate-400">{ev.seq}</td>
        <td className="w-20 px-3 py-1.5 align-top font-mono text-[11px] text-slate-500">{fmtTs(ev.ts)}</td>
        <td className="w-44 px-3 py-1.5 align-top">
          <EventKindBadge kind={ev.kind} />
        </td>
        <td className="px-3 py-1.5 align-top text-sm text-slate-800">
          {hasPayload && (
            <span className="mr-1.5 inline-block w-3 select-none text-slate-400">{open ? '▾' : '▸'}</span>
          )}
          {ev.summary}
        </td>
      </tr>
      {open && hasPayload && (
        <tr className="border-t border-slate-100 bg-slate-50">
          <td colSpan={4} className="px-12 py-2">
            <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[10px] leading-relaxed text-slate-700 ring-1 ring-slate-200">
              {JSON.stringify(ev.payload, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  )
}

const EVENTS_POLL_MS = 2000

function TracePage() {
  const [data, setData] = useState<EventsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = () => {
      api
        .get<EventsResponse>('/ranking/events')
        .then((d) => {
          if (stopped) return
          setData(d)
          timer = setTimeout(tick, EVENTS_POLL_MS)
        })
        .catch((e) => {
          if (stopped) return
          setError(String(e))
        })
    }

    tick()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  const events = data?.events ?? []
  // newest first for readability
  const sorted = [...events].sort((a, b) => b.seq - a.seq)

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="shrink-0">
        <h2 className="text-lg font-medium tracking-tight">Pipeline trace</h2>
        <p className="text-sm text-slate-600">
          System-level events across ingest, persona inference, ranking requests, and LLM batches.
          Click a row to inspect the event payload. For per-lead audit, expand a row on the Deliverable page.
        </p>
        {data && (
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
            <span>{events.length} events</span>
            <span>{data.trace_count} per-lead traces cached</span>
            <span className="text-emerald-700">{data.cache_stats.llm ?? 0} llm</span>
            <span className="text-slate-500">{data.cache_stats.fallback ?? 0} fallback</span>
            <span className="text-amber-700">{data.cache_stats.pending ?? 0} pending</span>
          </div>
        )}
      </div>

      {error && <p className="shrink-0 text-red-600">error: {error}</p>}
      {!error && !data && <p className="shrink-0 text-slate-500">loading…</p>}

      {data && (
        <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full text-sm">
            <thead className="sticky top-0 z-10 bg-slate-50 text-slate-600 shadow-sm">
              <tr>
                <th className="w-12 px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wider">seq</th>
                <th className="w-20 px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wider">time</th>
                <th className="w-44 px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wider">kind</th>
                <th className="px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wider">summary</th>
              </tr>
            </thead>
            <tbody>
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-center text-slate-400">no events yet</td>
                </tr>
              )}
              {sorted.map((ev) => <EventRow key={ev.seq} ev={ev} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const PAGE_LABEL: Record<Page, string> = {
  deliverable: 'Deliverable',
  tables: 'Tables',
  graph: 'Graph',
  trace: 'Trace',
}

function App() {
  const [page, setPage] = useState<Page>('deliverable')

  return (
    <div className="flex h-screen flex-col bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-50 shrink-0 border-b border-slate-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-350 items-center justify-between px-6 py-3">
          <h1 className="text-base font-semibold tracking-tight">Prelude Case Study</h1>
          <nav className="flex gap-1">
            {(['deliverable', 'tables', 'graph', 'trace'] as Page[]).map((p) => (
              <button
                key={p}
                onClick={() => setPage(p)}
                className={
                  'rounded-md px-3 py-1.5 text-sm font-medium transition-colors ' +
                  (page === p
                    ? 'bg-slate-900 text-white'
                    : 'text-slate-600 hover:bg-slate-100')
                }
              >
                {PAGE_LABEL[p]}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-hidden">
        <div className="mx-auto h-full max-w-350 px-6 py-6">
          {page === 'deliverable' && <DeliverablePage />}
          {page === 'tables' && <TablesPage />}
          {page === 'graph' && <GraphPage />}
          {page === 'trace' && <TracePage />}
        </div>
      </main>
    </div>
  )
}

export default App
