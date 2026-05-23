import { useEffect, useMemo, useState } from 'react'
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
type Page = 'deliverable' | 'tables' | 'graph'
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

type RankedLead = {
  lead_id: string
  company: string
  score: number
  components: Record<string, number>
  features: Record<string, unknown>
  reasoning: string
  selected: boolean
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

function DeliverablePage() {
  const [ranked, setRanked] = useState<RankedLead[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Record<string, boolean>>({})

  useEffect(() => {
    api.get<RankedLead[]>('/leads/ranked?limit=50')
      .then(setRanked)
      .catch((e) => setError(String(e)))
  }, [])

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="shrink-0">
        <h2 className="text-lg font-medium tracking-tight">Ranked outreach targets</h2>
        <p className="text-sm text-slate-600">
          Top US importers worth chasing for the factory. Score combines volume, recency, HS-code fit, competitor pressure, reachability, and seniority.
          <span className="ml-1 italic text-slate-400">(rationale layer stubbed — LLM not wired yet)</span>
        </p>
      </div>

      {error && <p className="shrink-0 text-red-600">error: {error}</p>}
      {!error && !ranked && <p className="shrink-0 text-slate-500">loading…</p>}

      {ranked && (
        <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full text-sm">
            <thead className="sticky top-0 z-10 bg-slate-50 text-slate-600 shadow-sm">
              <tr>
                <th className="w-12 px-3 py-2 text-left font-medium">✓</th>
                <th className="w-20 px-3 py-2 text-left font-medium">score</th>
                <th className="w-64 px-3 py-2 text-left font-medium">company</th>
                <th className="w-64 px-3 py-2 text-left font-medium">components</th>
                <th className="px-3 py-2 text-left font-medium">reasoning</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((r) => (
                <tr key={r.lead_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-3 py-2 align-top">
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
                  <td className="px-3 py-2 align-top text-slate-700">{r.reasoning}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="shrink-0 text-xs text-slate-500">
        {Object.values(selected).filter(Boolean).length} selected for outreach
        {ranked && <span className="ml-3">· {ranked.length} ranked</span>}
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

const PAGE_LABEL: Record<Page, string> = {
  deliverable: 'Deliverable',
  tables: 'Tables',
  graph: 'Graph',
}

function App() {
  const [page, setPage] = useState<Page>('deliverable')

  return (
    <div className="flex h-screen flex-col bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-50 shrink-0 border-b border-slate-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-350 items-center justify-between px-6 py-3">
          <h1 className="text-base font-semibold tracking-tight">Prelude Case Study</h1>
          <nav className="flex gap-1">
            {(['deliverable', 'tables', 'graph'] as Page[]).map((p) => (
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
        </div>
      </main>
    </div>
  )
}

export default App
