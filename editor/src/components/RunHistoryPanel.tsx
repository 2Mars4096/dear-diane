import { useCallback, useEffect, useMemo, useState } from "react";
import { useGraphStore } from "../store/useGraphStore";
import * as api from "../lib/api";
import { resolveCompareSelection } from "../lib/runHistory";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type View = "list" | "replay" | "compare";

interface HistoryState {
  runs: api.RunSummary[];
  total: number;
  loading: boolean;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatElapsed(sec: number | null | undefined): string {
  if (sec == null) return "—";
  if (sec < 60) return `${sec.toFixed(1)}s`;
  return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
}

function formatTokens(n: number | undefined): string {
  if (!n) return "—";
  return n.toLocaleString();
}

function formatCost(c: number | null | undefined): string {
  if (c == null || c === 0) return "";
  return c >= 0.01 ? `$${c.toFixed(2)}` : `$${c.toFixed(4)}`;
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: "bg-green-100 text-green-700",
    failed: "bg-red-100 text-red-700",
    running: "bg-blue-100 text-blue-700",
    pending: "bg-yellow-100 text-yellow-700",
  };
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${colors[status] ?? "bg-gray-100 text-gray-600"}`}>
      {status}
    </span>
  );
}

function DeltaBadge({ value, unit, invert }: { value: number; unit: string; invert?: boolean }) {
  if (value === 0) return <span className="text-gray-400 text-[10px]">—</span>;
  const positive = invert ? value < 0 : value > 0;
  const color = positive ? "text-red-500" : "text-green-600";
  const sign = value > 0 ? "+" : "";
  return <span className={`text-[10px] font-medium ${color}`}>{sign}{typeof value === "number" && Math.abs(value) < 1 ? value.toFixed(4) : value.toLocaleString()}{unit}</span>;
}

// ---------------------------------------------------------------------------
// Run List View
// ---------------------------------------------------------------------------

function RunListView({
  state,
  statusFilter,
  setStatusFilter,
  onSelect,
  onCompare,
  onRefresh,
}: {
  state: HistoryState;
  statusFilter: string;
  setStatusFilter: (v: string) => void;
  onSelect: (run: api.RunSummary) => void;
  onCompare: (runAId: string, runB: api.RunSummary) => void;
  onRefresh: () => void;
}) {
  const [compareSource, setCompareSource] = useState<string | null>(null);

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-2 py-1 border-b border-gray-200 bg-gray-50 shrink-0 flex-wrap">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="text-[11px] px-1 py-0.5 rounded border border-gray-300 bg-white text-gray-700 outline-none focus:ring-1 focus:ring-blue-400"
        >
          <option value="">All statuses</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
        </select>
        <button
          onClick={onRefresh}
          className="text-[11px] px-2 py-0.5 rounded border border-gray-300 bg-white text-gray-600 hover:bg-gray-100"
        >
          Refresh
        </button>
        {compareSource && (
          <span className="text-[10px] text-indigo-600 ml-auto">
            Select run to compare with {compareSource.slice(0, 8)}…
            <button onClick={() => setCompareSource(null)} className="ml-1 text-gray-400 hover:text-gray-600">×</button>
          </span>
        )}
        <span className="text-[10px] text-gray-400 ml-auto">{state.total} run{state.total !== 1 ? "s" : ""}</span>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {state.loading && (
          <div className="p-3 text-xs text-gray-400 text-center">Loading…</div>
        )}
        {state.error && (
          <div className="p-3 text-xs text-red-500 text-center">{state.error}</div>
        )}
        {!state.loading && state.runs.length === 0 && (
          <div className="p-3 text-xs text-gray-400 text-center">No runs yet.</div>
        )}
        {state.runs.map((run) => (
          <div
            key={run.run_id}
            className="flex items-center gap-2 px-2 py-1.5 text-[11px] border-b border-gray-100 hover:bg-gray-50 cursor-pointer group"
            onClick={() => {
              if (compareSource) {
                if (compareSource !== run.run_id) {
                  onCompare(compareSource, run);
                }
                setCompareSource(null);
              } else {
                onSelect(run);
              }
            }}
          >
            <StatusBadge status={run.status} />
            <span className="text-gray-500 shrink-0">{formatDate(run.started_at)}</span>
            <span className="text-gray-700 truncate flex-1">{run.graph_id}</span>
            <span className="text-gray-400 shrink-0">{formatElapsed(run.elapsed_seconds)}</span>
            <span className="text-indigo-500 shrink-0">{formatTokens(run.total_tokens)} tok</span>
            {run.total_cost != null && run.total_cost > 0 && (
              <span className="text-emerald-600 shrink-0">{formatCost(run.total_cost)}</span>
            )}
            <button
              onClick={(e) => {
                e.stopPropagation();
                setCompareSource(run.run_id);
              }}
              className="opacity-0 group-hover:opacity-100 text-[10px] text-indigo-500 hover:text-indigo-700 px-1 shrink-0"
              title="Compare with another run"
            >
              Compare…
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Replay View — loads events for a historical run
// ---------------------------------------------------------------------------

function ReplayView({
  run,
  onBack,
}: {
  run: api.RunSummary;
  onBack: () => void;
}) {
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [nodeFilter, setNodeFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.getRunEvents(run.run_id).then((res) => {
      if (!cancelled) {
        setEvents(res.events);
        setLoading(false);
      }
    }).catch(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [run.run_id]);

  const nodeIds = useMemo(() => {
    const s = new Set<string>();
    for (const e of events) {
      const nid = e.node_id as string | undefined;
      if (nid) s.add(nid);
    }
    return [...s].sort();
  }, [events]);

  const eventTypes = useMemo(() => {
    const s = new Set<string>();
    for (const e of events) {
      const t = e.event_type as string | undefined;
      if (t) s.add(t);
    }
    return [...s].sort();
  }, [events]);

  const filtered = useMemo(() => {
    let result = events;
    if (nodeFilter) result = result.filter((e) => e.node_id === nodeFilter);
    if (typeFilter) result = result.filter((e) => e.event_type === typeFilter);
    return result;
  }, [events, nodeFilter, typeFilter]);

  const grouped = useMemo(() => {
    const map = new Map<string, Array<Record<string, unknown>>>();
    for (const e of filtered) {
      const key = (e.node_id as string) ?? "__run__";
      const arr = map.get(key);
      if (arr) arr.push(e);
      else map.set(key, [e]);
    }
    return map;
  }, [filtered]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-2 py-1 border-b border-gray-200 bg-amber-50 shrink-0 flex-wrap">
        <button onClick={onBack} className="text-[11px] text-indigo-600 hover:text-indigo-800 font-medium">← Back</button>
        <span className="text-[10px] text-amber-700 font-medium px-1.5 py-0.5 bg-amber-100 rounded">REPLAY</span>
        <StatusBadge status={run.status} />
        <span className="text-[11px] text-gray-500">{formatDate(run.started_at)}</span>
        <span className="text-[11px] text-gray-700 font-medium">{run.graph_id}</span>
        <span className="text-[10px] text-gray-400 ml-auto">{formatElapsed(run.elapsed_seconds)}</span>
        <span className="text-[10px] text-indigo-500">{formatTokens(run.total_tokens)} tok</span>
        {run.total_cost != null && run.total_cost > 0 && (
          <span className="text-[10px] text-emerald-600">{formatCost(run.total_cost)}</span>
        )}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 px-2 py-0.5 border-b border-gray-100 bg-gray-50 shrink-0">
        <select value={nodeFilter} onChange={(e) => setNodeFilter(e.target.value)} className="text-[11px] px-1 py-0.5 rounded border border-gray-300 bg-white text-gray-700">
          <option value="">All nodes</option>
          {nodeIds.map((nid) => <option key={nid} value={nid}>{nid}</option>)}
        </select>
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="text-[11px] px-1 py-0.5 rounded border border-gray-300 bg-white text-gray-700">
          <option value="">All types</option>
          {eventTypes.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <span className="text-[10px] text-gray-400 ml-auto">{filtered.length}/{events.length} events</span>
      </div>

      {/* Events */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {loading && <div className="p-3 text-xs text-gray-400 text-center">Loading events…</div>}
        {!loading && filtered.length === 0 && (
          <div className="p-3 text-xs text-gray-400 text-center">No events found.</div>
        )}
        {!loading && [...grouped.entries()].map(([key, evts]) => (
          <ReplayNodeGroup key={key} nodeId={key} events={evts} />
        ))}
      </div>
    </div>
  );
}

function ReplayNodeGroup({
  nodeId,
  events,
}: {
  nodeId: string;
  events: Array<Record<string, unknown>>;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="border-b border-gray-100 last:border-b-0">
      <button
        className="flex items-center gap-1.5 w-full px-2 py-1 text-[11px] font-semibold text-gray-700 hover:bg-gray-50 text-left"
        onClick={() => setOpen(!open)}
      >
        <span className={`transition-transform text-gray-400 ${open ? "rotate-90" : ""}`}>▶</span>
        <span className="truncate">{nodeId === "__run__" ? "Run" : nodeId}</span>
        <span className="text-gray-400 font-normal ml-auto">{events.length}</span>
      </button>
      {open && (
        <div className="pl-4 pr-2 pb-1">
          {events.map((evt, i) => {
            const ts = evt.timestamp as number | undefined;
            const type = evt.event_type as string;
            const data = evt.data as Record<string, unknown> | undefined;
            return (
              <div key={i} className="py-0.5 text-[11px] font-mono leading-tight flex items-start gap-1.5">
                <span className="text-gray-400 shrink-0">{ts ? new Date(ts * 1000).toLocaleTimeString(undefined, { hour12: false, fractionalSecondDigits: 1 }) : ""}</span>
                <span className="text-indigo-500 shrink-0">{type}</span>
                {data && (
                  <span className="text-gray-500 truncate">{JSON.stringify(data).slice(0, 120)}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compare View
// ---------------------------------------------------------------------------

function CompareView({
  runA,
  runB,
  onBack,
}: {
  runA: api.RunSummary;
  runB: api.RunSummary;
  onBack: () => void;
}) {
  const [data, setData] = useState<api.CompareRunsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.compareRuns(runA.run_id, runB.run_id).then((res) => {
      if (!cancelled) { setData(res); setLoading(false); }
    }).catch((err) => {
      if (!cancelled) { setError(String(err)); setLoading(false); }
    });
    return () => { cancelled = true; };
  }, [runA.run_id, runB.run_id]);

  if (loading) return <div className="p-3 text-xs text-gray-400 text-center">Loading comparison…</div>;
  if (error) return <div className="p-3 text-xs text-red-500 text-center">{error}</div>;
  if (!data) return null;

  const { summary, node_diffs } = data;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-2 py-1 border-b border-gray-200 bg-violet-50 shrink-0 flex-wrap">
        <button onClick={onBack} className="text-[11px] text-indigo-600 hover:text-indigo-800 font-medium">← Back</button>
        <span className="text-[10px] text-violet-700 font-medium px-1.5 py-0.5 bg-violet-100 rounded">COMPARE</span>
        <span className="text-[10px] text-gray-500">A: {runA.run_id.slice(0, 8)}… vs B: {runB.run_id.slice(0, 8)}…</span>
      </div>

      {/* Summary deltas */}
      <div className="flex items-center gap-4 px-3 py-1.5 border-b border-gray-100 bg-gray-50 text-[11px] shrink-0">
        <div className="flex items-center gap-1">
          <span className="text-gray-500">Time:</span>
          <DeltaBadge value={summary.elapsed_delta} unit="s" invert />
        </div>
        <div className="flex items-center gap-1">
          <span className="text-gray-500">Tokens:</span>
          <DeltaBadge value={summary.token_delta} unit="" invert />
        </div>
        <div className="flex items-center gap-1">
          <span className="text-gray-500">Cost:</span>
          <DeltaBadge value={summary.cost_delta} unit="" invert />
        </div>
        <div className="flex items-center gap-1">
          <span className="text-gray-500">A:</span>
          <StatusBadge status={summary.status_a} />
        </div>
        <div className="flex items-center gap-1">
          <span className="text-gray-500">B:</span>
          <StatusBadge status={summary.status_b} />
        </div>
      </div>

      {/* Node diffs table */}
      <div className="flex-1 overflow-y-auto min-h-0">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-gray-50 border-b border-gray-200">
            <tr className="text-gray-500 text-left">
              <th className="px-2 py-1 font-medium">Node</th>
              <th className="px-2 py-1 font-medium">Status A</th>
              <th className="px-2 py-1 font-medium">Status B</th>
              <th className="px-2 py-1 font-medium text-right">Tokens A</th>
              <th className="px-2 py-1 font-medium text-right">Tokens B</th>
              <th className="px-2 py-1 font-medium text-right">Delta</th>
            </tr>
          </thead>
          <tbody>
            {node_diffs.map((nd) => (
              <tr key={nd.node_id} className={`border-b border-gray-50 ${nd.status_changed ? "bg-yellow-50" : "hover:bg-gray-50"}`}>
                <td className="px-2 py-1 font-mono text-gray-700 truncate max-w-[200px]">{nd.node_id}</td>
                <td className="px-2 py-1">{nd.status_a ? <StatusBadge status={nd.status_a.replace("node_", "")} /> : <span className="text-gray-300">—</span>}</td>
                <td className="px-2 py-1">{nd.status_b ? <StatusBadge status={nd.status_b.replace("node_", "")} /> : <span className="text-gray-300">—</span>}</td>
                <td className="px-2 py-1 text-right text-gray-600">{nd.tokens_a.toLocaleString()}</td>
                <td className="px-2 py-1 text-right text-gray-600">{nd.tokens_b.toLocaleString()}</td>
                <td className="px-2 py-1 text-right">
                  <DeltaBadge value={nd.token_delta} unit="" invert />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {node_diffs.length === 0 && (
          <div className="p-3 text-xs text-gray-400 text-center">No node data to compare.</div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Panel
// ---------------------------------------------------------------------------

export default function RunHistoryPanel() {
  const graphId = useGraphStore((s) => s.graphId);
  const historyFocusCounter = useGraphStore((s) => s.historyFocusCounter);
  const historyFocusRunId = useGraphStore((s) => s.historyFocusRunId);

  const [view, setView] = useState<View>("list");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedRun, setSelectedRun] = useState<api.RunSummary | null>(null);
  const [compareRunA, setCompareRunA] = useState<api.RunSummary | null>(null);
  const [compareRunB, setCompareRunB] = useState<api.RunSummary | null>(null);
  const [state, setState] = useState<HistoryState>({ runs: [], total: 0, loading: false, error: null });

  useEffect(() => {
    if (historyFocusCounter > 0 && historyFocusRunId) {
      const existing = state.runs.find((r) => r.run_id === historyFocusRunId);
      if (existing) {
        setSelectedRun(existing);
        setView("replay");
      } else {
        api.getRun(historyFocusRunId)
          .then((run) => { setSelectedRun(run as api.RunSummary); setView("replay"); })
          .catch(() => {});
      }
    }
  }, [historyFocusCounter, historyFocusRunId]);

  const fetchRuns = useCallback(() => {
    setState((s) => ({ ...s, loading: true, error: null }));
    const filters: api.RunListFilters = { limit: 200 };
    if (graphId) filters.workflow_id = graphId;
    if (statusFilter) filters.status = statusFilter;
    api.listRuns(filters)
      .then((res) => setState({ runs: res.runs, total: res.total, loading: false, error: null }))
      .catch((err) => setState((s) => ({ ...s, loading: false, error: String(err) })));
  }, [graphId, statusFilter]);

  useEffect(() => { fetchRuns(); }, [fetchRuns]);

  const handleSelect = useCallback((run: api.RunSummary) => {
    setSelectedRun(run);
    setView("replay");
  }, []);

  const handleCompare = useCallback((runAId: string, runB: api.RunSummary) => {
    const selection = resolveCompareSelection(state.runs, runAId, runB);
    if (!selection) return;
    setCompareRunA(selection.runA);
    setCompareRunB(selection.runB);
    setView("compare");
  }, [state.runs]);

  const handleBack = useCallback(() => {
    setView("list");
    setSelectedRun(null);
    setCompareRunA(null);
    setCompareRunB(null);
  }, []);

  if (view === "replay" && selectedRun) {
    return <ReplayView run={selectedRun} onBack={handleBack} />;
  }

  if (view === "compare" && compareRunA && compareRunB) {
    return <CompareView runA={compareRunA} runB={compareRunB} onBack={handleBack} />;
  }

  return (
    <RunListView
      state={state}
      statusFilter={statusFilter}
      setStatusFilter={setStatusFilter}
      onSelect={handleSelect}
      onCompare={handleCompare}
      onRefresh={fetchRuns}
    />
  );
}
