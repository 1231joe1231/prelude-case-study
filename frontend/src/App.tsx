import { useEffect, useState } from 'react'
import { api } from './lib/api'

type Row = Record<string, unknown>
type Page = 'deliverable' | 'tables'
type TableKey = 'leads' | 'personnel' | 'competitors'

const LEAD_COLS = [
  'id', 'company_name', 'city_state', 'data_source', 'website', 'status',
  'legacy_score', 'created_at', 'updated_at', 'is_test',
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

const TABLE_CONFIG: Record<TableKey, { label: string; cols: string[]; endpoint: string }> = {
  leads:       { label: 'leads',       cols: LEAD_COLS,       endpoint: '/tables/leads' },
  personnel:   { label: 'personnel',   cols: PERSONNEL_COLS,  endpoint: '/tables/personnel' },
  competitors: { label: 'competitors', cols: COMPETITOR_COLS, endpoint: '/tables/competitors' },
}

type RankedLead = {
  lead_id: string
  company: string
  score: number
  reasoning: string
}

const DUMMY_RANKED: RankedLead[] = [
  { lead_id: 'L-001', company: 'Evergreen Holiday Imports',  score: 0.94, reasoning: 'High recent BOL volume on HS 950510 (artificial trees); 4 shipments in last 30d via LA/Long Beach. Single dominant competitor — switchable.' },
  { lead_id: 'L-014', company: 'NorthStar Decor Co.',         score: 0.89, reasoning: 'Buys HS 950300, 940542 — direct product fit. Decision-maker email present (VP Sourcing). No prior CRM contact.' },
  { lead_id: 'L-027', company: 'Yuletide Wholesale LLC',      score: 0.82, reasoning: 'Steady year-round shipments, low competitor concentration (3 MED-bucket suppliers). Two named buyers in personnel.' },
  { lead_id: 'L-038', company: 'Pinecrest Trading',           score: 0.76, reasoning: 'Matches HS 940350 (decorative lighting). Recent shipment 12d ago. Contact title = Procurement Manager.' },
  { lead_id: 'L-052', company: 'Holly & Bough Distributors',  score: 0.71, reasoning: 'Mid-volume but exclusive supplier relationship with one HIGH-bucket competitor — winnable with price leverage.' },
  { lead_id: 'L-061', company: 'Snowdrift Seasonal Goods',    score: 0.66, reasoning: 'HS 060490 (cut foliage) overlap. Email reachable. Last shipment 45d ago — re-engagement window.' },
  { lead_id: 'L-073', company: 'Cardinal Crafts Inc.',        score: 0.61, reasoning: 'Product fit on HS 950510. Only one personnel record, generic info@ email — lower reachability.' },
  { lead_id: 'L-088', company: 'Glacier Trim & Ornament',     score: 0.55, reasoning: 'Newer importer (created 60d ago), small volume but growing month-over-month. Worth early relationship.' },
]

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

function DeliverablePage() {
  const [selected, setSelected] = useState<Record<string, boolean>>({})

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="shrink-0">
        <h2 className="text-lg font-medium tracking-tight">Ranked outreach targets</h2>
        <p className="text-sm text-slate-600">
          Top US importers worth chasing for the factory. Scores combine BOL volume, HS-code fit, competitor pressure, and contact reachability.
          <span className="ml-1 italic text-slate-400">(dummy data — agent not wired yet)</span>
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full text-sm">
          <thead className="sticky top-0 z-10 bg-slate-50 text-slate-600 shadow-sm">
            <tr>
              <th className="w-12 px-3 py-2 text-left font-medium">✓</th>
              <th className="w-20 px-3 py-2 text-left font-medium">score</th>
              <th className="w-64 px-3 py-2 text-left font-medium">company</th>
              <th className="px-3 py-2 text-left font-medium">reasoning</th>
            </tr>
          </thead>
          <tbody>
            {DUMMY_RANKED.map((r) => (
              <tr key={r.lead_id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-3 py-2 align-top">
                  <input
                    type="checkbox"
                    checked={!!selected[r.lead_id]}
                    onChange={(e) => setSelected((s) => ({ ...s, [r.lead_id]: e.target.checked }))}
                    className="h-4 w-4 cursor-pointer accent-slate-900"
                  />
                </td>
                <td className="px-3 py-2 align-top font-mono text-slate-900">{r.score.toFixed(2)}</td>
                <td className="px-3 py-2 align-top font-medium text-slate-900">{r.company}</td>
                <td className="px-3 py-2 align-top text-slate-700">{r.reasoning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="shrink-0 text-xs text-slate-500">
        {Object.values(selected).filter(Boolean).length} selected for outreach
      </div>
    </div>
  )
}

function TablesPage() {
  const [active, setActive] = useState<TableKey>('leads')
  const [data, setData] = useState<Record<TableKey, Row[] | null>>({
    leads: null, personnel: null, competitors: null,
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

function App() {
  const [page, setPage] = useState<Page>('deliverable')

  return (
    <div className="flex h-screen flex-col bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-50 shrink-0 border-b border-slate-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-350 items-center justify-between px-6 py-3">
          <h1 className="text-base font-semibold tracking-tight">Prelude Case Study</h1>
          <nav className="flex gap-1">
            {(['deliverable', 'tables'] as Page[]).map((p) => (
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
                {p === 'deliverable' ? 'Deliverable' : 'Tables'}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-hidden">
        <div className="mx-auto h-full max-w-350 px-6 py-6">
          {page === 'deliverable' ? <DeliverablePage /> : <TablesPage />}
        </div>
      </main>
    </div>
  )
}

export default App
