import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Clock3,
  GitBranch,
  Loader2,
  RefreshCcw,
} from "lucide-react";

import {
  fetchOrganismLogAnalysis,
  fetchOrganismLogs,
  type OrganismLogAnalysisResponse,
  type OrganismLogBlockingChain,
  type OrganismLogSpanAnalysis,
  type OrganismLogSummary,
} from "../../lib/api";
import { useCodeStore } from "../../store/useCodeStore";

const DISCOVERY_LIMIT = 24;
const WINDOWS_ABSOLUTE_PATH_RE = /^[A-Za-z]:[\\/]/;

function formatDuration(value: number | null | undefined): string {
  if (value == null) return "n/a";
  if (value >= 60_000) return `${(value / 60_000).toFixed(1)}m`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.round(value)}ms`;
}

function formatRelativeTime(value: string | null | undefined): string {
  if (!value) return "No recent activity";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "No recent activity";
  const deltaMs = Date.now() - timestamp;
  const deltaMinutes = Math.floor(deltaMs / 60_000);
  if (deltaMinutes < 1) return "just now";
  if (deltaMinutes < 60) return `${deltaMinutes}m ago`;
  const deltaHours = Math.floor(deltaMinutes / 60);
  if (deltaHours < 24) return `${deltaHours}h ago`;
  const deltaDays = Math.floor(deltaHours / 24);
  if (deltaDays < 7) return `${deltaDays}d ago`;
  return new Date(timestamp).toLocaleDateString();
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "n/a";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Date(timestamp).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatBytes(value: number | null | undefined): string {
  if (value == null) return "n/a";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} MB`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)} KB`;
  return `${value} B`;
}

function productTone(product: string, streamKind: string): string {
  if (streamKind === "control_plane") {
    return "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200";
  }
  if (product === "dan_code") {
    return "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200";
  }
  if (product === "dan_research") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200";
  }
  return "border-gray-200 bg-white text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200";
}

function productLabel(product: string, streamKind: string): string {
  if (streamKind === "control_plane") return "Control plane";
  if (product === "dan_code") return "DAN Code";
  if (product === "dan_research") return "DAN Research";
  return product || "Organism";
}

function spanTone(
  span: OrganismLogSpanAnalysis,
  selected: boolean,
  critical: boolean,
): string {
  if (selected) {
    return "border-blue-500 bg-blue-600 text-white shadow-[0_0_0_1px_rgba(59,130,246,0.3)]";
  }
  if (span.status === "failed" || span.status === "blocked" || span.status === "timeout") {
    return "border-red-300 bg-red-500/90 text-white";
  }
  if (span.span_kind === "tool_call") {
    return critical
      ? "border-amber-200 bg-amber-500 text-amber-950"
      : "border-amber-200 bg-amber-100 text-amber-900 dark:border-amber-500/40 dark:bg-amber-400/30 dark:text-amber-50";
  }
  if (span.span_kind === "model_call") {
    return critical
      ? "border-sky-200 bg-sky-500 text-sky-950"
      : "border-sky-200 bg-sky-100 text-sky-900 dark:border-sky-500/40 dark:bg-sky-400/30 dark:text-sky-50";
  }
  if (critical) {
    return "border-fuchsia-200 bg-fuchsia-500 text-fuchsia-950";
  }
  return "border-gray-300 bg-gray-100 text-gray-900 dark:border-gray-600 dark:bg-gray-700/80 dark:text-gray-50";
}

function StatCard({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "critical";
}) {
  const classes =
    tone === "critical"
      ? "border-fuchsia-200 bg-fuchsia-50 dark:border-fuchsia-500/30 dark:bg-fuchsia-500/10"
      : "border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900/70";
  return (
    <div className={`rounded-xl border px-3 py-2 ${classes}`}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
        {label}
      </div>
      <div className="mt-1 text-sm font-semibold text-gray-900 dark:text-gray-100">
        {value}
      </div>
    </div>
  );
}

function EmptyState({
  title,
  body,
}: {
  title: string;
  body: string;
}) {
  return (
    <div className="rounded-xl border border-dashed border-gray-300 bg-white/80 px-4 py-6 text-center dark:border-gray-700 dark:bg-gray-900/60">
      <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
        {title}
      </div>
      <div className="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">
        {body}
      </div>
    </div>
  );
}

export default function OrganismLogTimelinePanel() {
  const pinnedRoots = useCodeStore((state) => state.pinnedRoots);
  const [selectedRoot, setSelectedRoot] = useState<string>("");
  const [manualPath, setManualPath] = useState("");
  const [logs, setLogs] = useState<OrganismLogSummary[]>([]);
  const [analysisResponse, setAnalysisResponse] =
    useState<OrganismLogAnalysisResponse | null>(null);
  const [selectedLogPath, setSelectedLogPath] = useState<string | null>(null);
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingAnalysis, setLoadingAnalysis] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (pinnedRoots.length === 0) {
      setSelectedRoot("");
      return;
    }
    setSelectedRoot((current) =>
      current && pinnedRoots.includes(current) ? current : pinnedRoots[0],
    );
  }, [pinnedRoots]);

  const loadAnalysis = useCallback(
    async (path: string, overrideRoot?: string) => {
      const effectivePath = path.trim();
      if (!effectivePath) return;
      setLoadingAnalysis(true);
      setError(null);
      try {
        const response = await fetchOrganismLogAnalysis(effectivePath, {
          rootPath: overrideRoot || selectedRoot || undefined,
        });
        setAnalysisResponse(response);
        setSelectedLogPath(response.log.path);
        setSelectedSpanId((current) => {
          const availableIds = new Set(
            response.analysis.timeline.spans.map((span) => span.span_id),
          );
          if (current && availableIds.has(current)) return current;
          return (
            response.analysis.graph.critical_path_span_ids[0] ??
            response.analysis.timeline.spans[0]?.span_id ??
            null
          );
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to analyze log.");
      } finally {
        setLoadingAnalysis(false);
      }
    },
    [selectedRoot],
  );

  const refreshLogs = useCallback(async () => {
    if (!selectedRoot) {
      setLogs([]);
      return;
    }
    setRefreshing(true);
    setError(null);
    try {
      const response = await fetchOrganismLogs(selectedRoot, {
        limit: DISCOVERY_LIMIT,
      });
      setLogs(response.logs);

      const keepCurrent = Boolean(
        selectedLogPath && response.logs.some((log) => log.path === selectedLogPath),
      );
      if (keepCurrent && selectedLogPath) {
        await loadAnalysis(selectedLogPath, selectedRoot);
      } else if (response.logs[0]) {
        await loadAnalysis(response.logs[0].path, selectedRoot);
      } else {
        setAnalysisResponse(null);
        setSelectedLogPath(null);
        setSelectedSpanId(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to discover logs.");
    } finally {
      setRefreshing(false);
    }
  }, [loadAnalysis, selectedLogPath, selectedRoot]);

  useEffect(() => {
    if (!selectedRoot) {
      setLogs([]);
      return;
    }
    void refreshLogs();
  }, [refreshLogs, selectedRoot]);

  const analysis = analysisResponse?.analysis ?? null;
  const selectedLog =
    analysisResponse?.log ??
    logs.find((entry) => entry.path === selectedLogPath) ??
    null;

  const spans = analysis?.timeline.spans ?? [];
  const spansById = useMemo(
    () => new Map(spans.map((span) => [span.span_id, span])),
    [spans],
  );
  const criticalPathSet = useMemo(
    () => new Set(analysis?.graph.critical_path_span_ids ?? []),
    [analysis],
  );

  const selectedSpan =
    (selectedSpanId ? spansById.get(selectedSpanId) : undefined) ??
    (analysis?.graph.critical_path_span_ids[0]
      ? spansById.get(analysis.graph.critical_path_span_ids[0])
      : undefined) ??
    spans[0];

  const totalDuration =
    analysis?.timeline.duration_ms ??
    spans.reduce((max, span) => Math.max(max, span.end_ms ?? 0), 0) ??
    0;

  const blockerChains = analysis?.graph.blocker_chains ?? [];
  const manualPathTrimmed = manualPath.trim();
  const manualPathLooksAbsolute =
    manualPathTrimmed.startsWith("/") ||
    manualPathTrimmed.startsWith("~/") ||
    WINDOWS_ABSOLUTE_PATH_RE.test(manualPathTrimmed);
  const canManualAnalyze =
    manualPathTrimmed.length > 0 &&
    (Boolean(selectedRoot) || manualPathLooksAbsolute);
  const manualPathHint =
    manualPathTrimmed.length === 0
      ? "Paste a log path to open. Relative paths need a pinned workspace root; absolute paths work directly."
      : !selectedRoot && !manualPathLooksAbsolute
        ? "This is a relative path. Pin a workspace root first, or paste an absolute path."
        : "Open this log path directly.";

  const handleManualAnalyze = useCallback(async () => {
    if (!manualPathTrimmed) {
      setError("Paste a log path to open. The sample text is only a placeholder.");
      return;
    }
    if (!selectedRoot && !manualPathLooksAbsolute) {
      setError(
        "Relative log paths need a pinned workspace root. Pin the workspace first, or paste an absolute path.",
      );
      return;
    }
    await loadAnalysis(manualPathTrimmed, selectedRoot || undefined);
  }, [loadAnalysis, manualPathLooksAbsolute, manualPathTrimmed, selectedRoot]);

  return (
    <div className="flex h-full flex-col bg-gradient-to-b from-gray-50 via-white to-gray-100 text-xs dark:from-gray-950 dark:via-gray-950 dark:to-gray-900">
      <div className="border-b border-gray-200 bg-white/90 px-3 py-3 backdrop-blur dark:border-gray-800 dark:bg-gray-950/90">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
              Organism Logs
            </div>
            <div className="mt-1 text-[11px] leading-5 text-gray-500 dark:text-gray-400">
              Visualize LLM calls, tool spans, blocker chains, and critical path timing from
              `organism_log_v1`.
            </div>
          </div>
          <button
            onClick={() => void refreshLogs()}
            title="Refresh logs"
            className="rounded-md border border-gray-200 p-1.5 text-gray-500 transition-colors hover:border-gray-300 hover:text-gray-900 dark:border-gray-700 dark:text-gray-400 dark:hover:border-gray-600 dark:hover:text-gray-100"
          >
            {refreshing ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RefreshCcw size={14} />
            )}
          </button>
        </div>

        <div className="mt-3 flex flex-col gap-2">
          <label className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
            Workspace Root
          </label>
          <select
            value={selectedRoot}
            onChange={(event) => setSelectedRoot(event.target.value)}
            className="rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-xs text-gray-900 outline-none transition focus:border-blue-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
          >
            {pinnedRoots.length === 0 ? (
              <option value="">No pinned roots</option>
            ) : (
              pinnedRoots.map((root) => (
                <option key={root} value={root}>
                  {root}
                </option>
              ))
            )}
          </select>
          {pinnedRoots.length === 0 && (
            <div className="text-[11px] leading-5 text-amber-700 dark:text-amber-200">
              Pin this repo as a workspace root if you want to open relative paths like
              ` .dan-super/runs/turn-01/events.jsonl ` or
              ` .dan-research/runs/turn-01/events.jsonl `. Without a pinned root, paste
              an absolute path instead.
            </div>
          )}
        </div>

        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void handleManualAnalyze();
          }}
        >
          <input
            value={manualPath}
            onChange={(event) => setManualPath(event.target.value)}
            placeholder={
              selectedRoot
                ? ".dan-super/runs/turn-01/events.jsonl"
                : "/absolute/path/to/.dan-super/runs/turn-01/events.jsonl"
            }
            className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-xs text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-blue-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:placeholder:text-gray-500"
          />
          <button
            type="submit"
            disabled={!canManualAnalyze || loadingAnalysis}
            title={manualPathHint}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-[11px] font-semibold text-gray-700 transition hover:border-gray-300 hover:text-gray-900 disabled:cursor-not-allowed disabled:border-gray-200 disabled:bg-gray-50 disabled:text-gray-400 disabled:opacity-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-gray-600 dark:disabled:border-gray-800 dark:disabled:bg-gray-900 dark:disabled:text-gray-500"
          >
            Open
          </button>
        </form>
        <div className="mt-2 text-[11px] leading-5 text-gray-500 dark:text-gray-400">
          {manualPathHint}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {error && (
          <div className="mb-3 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <div className="min-w-0 leading-5">{error}</div>
          </div>
        )}

        <div className="space-y-3">
          <section className="rounded-2xl border border-gray-200 bg-white/90 shadow-sm dark:border-gray-800 dark:bg-gray-900/70">
            <div className="border-b border-gray-200 px-3 py-2 dark:border-gray-800">
              <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                Discovered Logs
              </div>
            </div>
            <div className="max-h-64 overflow-y-auto px-2 py-2">
              {logs.length === 0 ? (
                <EmptyState
                  title="No DAN logs discovered yet"
                  body="Auto-discovery looks under `.dan-code/runs`, `.dan-super/runs`, and `.dan-research`. You can still paste a log path above, or normalize a foreign trace first with `dan-organism-log import`."
                />
              ) : (
                <div className="space-y-2">
                  {logs.map((entry) => {
                    const selected = entry.path === selectedLogPath;
                    return (
                      <button
                        key={entry.path}
                        onClick={() => void loadAnalysis(entry.path, selectedRoot)}
                        className={`w-full rounded-xl border px-3 py-2 text-left transition ${
                          selected
                            ? "border-blue-400 bg-blue-50 shadow-sm dark:border-blue-500/50 dark:bg-blue-500/10"
                            : "border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50 dark:border-gray-800 dark:bg-gray-900/60 dark:hover:border-gray-700 dark:hover:bg-gray-900"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
                              {entry.display_name}
                            </div>
                            <div className="mt-0.5 truncate text-[11px] text-gray-500 dark:text-gray-400">
                              {entry.relative_path}
                            </div>
                          </div>
                          <span
                            className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${productTone(entry.product, entry.stream_kind)}`}
                          >
                            {productLabel(entry.product, entry.stream_kind)}
                          </span>
                        </div>
                        <div className="mt-2 flex items-center gap-3 text-[10px] uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
                          <span>{entry.span_count} spans</span>
                          <span>{entry.event_count} events</span>
                          <span>{formatRelativeTime(entry.updated_at)}</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          {!analysis || !selectedLog ? (
            <EmptyState
              title="No analysis loaded"
              body="Pick a discovered run log or paste a log path above. The panel will project lanes, dependencies, blockers, and critical-path spans from the shared organism log."
            />
          ) : (
            <>
              <section className="rounded-2xl border border-gray-200 bg-white/90 shadow-sm dark:border-gray-800 dark:bg-gray-900/70">
                <div className="border-b border-gray-200 px-3 py-3 dark:border-gray-800">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
                        {selectedLog.display_name}
                      </div>
                      <div className="mt-1 truncate text-[11px] text-gray-500 dark:text-gray-400">
                        {selectedLog.relative_path}
                      </div>
                    </div>
                    {loadingAnalysis && <Loader2 size={14} className="mt-1 animate-spin text-gray-500" />}
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <StatCard label="Run duration" value={formatDuration(analysis.timeline.duration_ms)} />
                    <StatCard label="Critical path" value={formatDuration(analysis.graph.critical_path_duration_ms)} tone="critical" />
                    <StatCard label="Span count" value={String(analysis.span_count)} />
                    <StatCard label="Max parallel" value={String(analysis.timeline.max_parallel_spans)} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-gray-500 dark:text-gray-400">
                    <span>Started {formatTimestamp(selectedLog.started_at)}</span>
                    <span>•</span>
                    <span>Updated {formatRelativeTime(selectedLog.updated_at)}</span>
                    <span>•</span>
                    <span>{formatBytes(selectedLog.size_bytes)}</span>
                  </div>
                </div>
              </section>

              <section className="rounded-2xl border border-gray-200 bg-white/90 shadow-sm dark:border-gray-800 dark:bg-gray-900/70">
                <div className="border-b border-gray-200 px-3 py-2 dark:border-gray-800">
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                    <Clock3 size={13} />
                    Execution Lanes
                  </div>
                </div>
                <div className="overflow-x-auto px-3 py-3">
                  <div className="min-w-[480px] space-y-3">
                    {analysis.timeline.lanes.map((lane) => {
                      const laneSpans = spans.filter((span) => span.lane_id === lane.lane_id);
                      return (
                        <div
                          key={lane.lane_id}
                          className="grid grid-cols-[112px_minmax(0,1fr)] items-center gap-3"
                        >
                          <div className="min-w-0">
                            <div className="truncate text-[11px] font-semibold text-gray-700 dark:text-gray-200">
                              {lane.label}
                            </div>
                            <div className="truncate text-[10px] text-gray-500 dark:text-gray-400">
                              {lane.span_ids.length} spans
                            </div>
                          </div>
                          <div className="relative h-10 rounded-xl border border-gray-200 bg-gray-100/80 dark:border-gray-800 dark:bg-gray-950/70">
                            {laneSpans.map((span) => {
                              const start = span.start_ms ?? 0;
                              const rawDuration =
                                span.duration_ms ??
                                ((span.end_ms ?? start) - start);
                              const widthPct = totalDuration > 0
                                ? Math.max((Math.max(rawDuration, 1) / totalDuration) * 100, 2)
                                : 100;
                              const leftPct = totalDuration > 0 ? (start / totalDuration) * 100 : 0;
                              const isCritical = criticalPathSet.has(span.span_id);
                              const isSelected = span.span_id === selectedSpan?.span_id;
                              return (
                                <button
                                  key={span.span_id}
                                  onClick={() => setSelectedSpanId(span.span_id)}
                                  className={`absolute top-1/2 flex h-7 -translate-y-1/2 items-center rounded-lg border px-2 text-[10px] font-semibold shadow-sm transition hover:brightness-105 ${spanTone(
                                    span,
                                    isSelected,
                                    isCritical,
                                  )}`}
                                  style={{
                                    left: `${leftPct}%`,
                                    width: `${widthPct}%`,
                                    minWidth: "30px",
                                  }}
                                  title={`${span.label} • ${formatDuration(span.duration_ms)}${
                                    span.waiting_duration_ms
                                      ? ` • wait ${formatDuration(span.waiting_duration_ms)}`
                                      : ""
                                  }`}
                                >
                                  <span className="truncate">{span.label}</span>
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </section>

              <section className="grid gap-3">
                <div className="rounded-2xl border border-gray-200 bg-white/90 shadow-sm dark:border-gray-800 dark:bg-gray-900/70">
                  <div className="border-b border-gray-200 px-3 py-2 dark:border-gray-800">
                    <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                      <GitBranch size={13} />
                      Critical Path
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 px-3 py-3">
                    {analysis.graph.critical_path_span_ids.length === 0 ? (
                      <div className="text-[11px] text-gray-500 dark:text-gray-400">
                        No critical path detected.
                      </div>
                    ) : (
                      analysis.graph.critical_path_span_ids.map((spanId, index) => {
                        const span = spansById.get(spanId);
                        if (!span) return null;
                        return (
                          <div key={spanId} className="flex items-center gap-2">
                            <button
                              onClick={() => setSelectedSpanId(spanId)}
                              className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
                                selectedSpan?.span_id === spanId
                                  ? "border-fuchsia-400 bg-fuchsia-500 text-white"
                                  : "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700 hover:border-fuchsia-300 dark:border-fuchsia-500/30 dark:bg-fuchsia-500/10 dark:text-fuchsia-200"
                              }`}
                            >
                              {index + 1}. {span.label}
                            </button>
                            {index < analysis.graph.critical_path_span_ids.length - 1 && (
                              <ArrowRight size={12} className="text-gray-400" />
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>

                <div className="rounded-2xl border border-gray-200 bg-white/90 shadow-sm dark:border-gray-800 dark:bg-gray-900/70">
                  <div className="border-b border-gray-200 px-3 py-2 dark:border-gray-800">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                      Blockers
                    </div>
                  </div>
                  <div className="space-y-2 px-3 py-3">
                    {blockerChains.length === 0 ? (
                      <div className="text-[11px] text-gray-500 dark:text-gray-400">
                        No direct blockers inferred from this trace.
                      </div>
                    ) : (
                      blockerChains.slice(0, 6).map((chain) => {
                        const target = spansById.get(chain.target_span_id);
                        return (
                          <BlockerChainCard
                            key={chain.target_span_id}
                            chain={chain}
                            target={target}
                            spansById={spansById}
                            selectedSpanId={selectedSpan?.span_id ?? null}
                            onSelectSpan={setSelectedSpanId}
                          />
                        );
                      })
                    )}
                  </div>
                </div>
              </section>

              {selectedSpan && (
                <section className="rounded-2xl border border-gray-200 bg-white/90 shadow-sm dark:border-gray-800 dark:bg-gray-900/70">
                  <div className="border-b border-gray-200 px-3 py-3 dark:border-gray-800">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
                          {selectedSpan.label}
                        </div>
                        <div className="mt-1 truncate text-[11px] text-gray-500 dark:text-gray-400">
                          {selectedSpan.summary || selectedSpan.event || selectedSpan.span_kind || selectedSpan.span_id}
                        </div>
                      </div>
                      <span
                        className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${
                          criticalPathSet.has(selectedSpan.span_id)
                            ? "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700 dark:border-fuchsia-500/30 dark:bg-fuchsia-500/10 dark:text-fuchsia-200"
                            : "border-gray-200 bg-white text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
                        }`}
                      >
                        {selectedSpan.status || "span"}
                      </span>
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2">
                      <StatCard label="Duration" value={formatDuration(selectedSpan.duration_ms)} />
                      <StatCard label="Exclusive" value={formatDuration(selectedSpan.exclusive_duration_ms)} />
                      <StatCard label="Waiting" value={formatDuration(selectedSpan.waiting_duration_ms)} />
                    </div>
                  </div>
                  <div className="space-y-3 px-3 py-3">
                    <DetailRow label="Span ID" value={selectedSpan.span_id} mono />
                    <DetailRow label="Lane" value={selectedSpan.lane_label || selectedSpan.lane_id || "n/a"} />
                    <DetailRow label="Started" value={formatTimestamp(selectedSpan.start_timestamp)} />
                    <DetailRow label="Finished" value={formatTimestamp(selectedSpan.end_timestamp)} />
                    <DetailRow
                      label="Identifiers"
                      value={
                        [
                          selectedSpan.model_call_id && `model ${selectedSpan.model_call_id}`,
                          selectedSpan.tool_call_id && `tool ${selectedSpan.tool_call_id}`,
                          selectedSpan.contract_id && `contract ${selectedSpan.contract_id}`,
                        ]
                          .filter(Boolean)
                          .join(" • ") || "n/a"
                      }
                      mono
                    />
                    <SpanLinks
                      label="Direct blockers"
                      spanIds={selectedSpan.direct_blocker_span_ids}
                      spansById={spansById}
                      onSelectSpan={setSelectedSpanId}
                    />
                    <SpanLinks
                      label="Dependents"
                      spanIds={selectedSpan.dependent_span_ids}
                      spansById={spansById}
                      onSelectSpan={setSelectedSpanId}
                    />
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function BlockerChainCard({
  chain,
  target,
  spansById,
  selectedSpanId,
  onSelectSpan,
}: {
  chain: OrganismLogBlockingChain;
  target: OrganismLogSpanAnalysis | undefined;
  spansById: Map<string, OrganismLogSpanAnalysis>;
  selectedSpanId: string | null;
  onSelectSpan: (spanId: string) => void;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50/80 px-3 py-2 dark:border-gray-800 dark:bg-gray-950/40">
      <div className="flex items-center justify-between gap-3">
        <button
          onClick={() => onSelectSpan(chain.target_span_id)}
          className={`truncate text-left text-sm font-semibold ${
            selectedSpanId === chain.target_span_id
              ? "text-blue-700 dark:text-blue-200"
              : "text-gray-900 dark:text-gray-100"
          }`}
        >
          {target?.label || chain.target_span_id}
        </button>
        <div className="shrink-0 text-[11px] text-gray-500 dark:text-gray-400">
          wait {formatDuration(chain.waiting_duration_ms)}
        </div>
      </div>
      <div className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
        {chain.wait_reason || target?.summary || target?.event || target?.span_kind || "Blocked on upstream work."}
      </div>
      {chain.blocker_chain_span_ids.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {chain.blocker_chain_span_ids.map((spanId, index) => {
            const span = spansById.get(spanId);
            return (
              <div key={`${chain.target_span_id}:${spanId}`} className="flex items-center gap-2">
                <button
                  onClick={() => onSelectSpan(spanId)}
                  className="rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10px] font-medium text-gray-700 transition hover:border-gray-300 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
                >
                  {span?.label || spanId}
                </button>
                {index < chain.blocker_chain_span_ids.length - 1 && (
                  <ArrowRight size={11} className="text-gray-400" />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="grid grid-cols-[84px_minmax(0,1fr)] gap-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
        {label}
      </div>
      <div
        className={`min-w-0 truncate text-[11px] text-gray-700 dark:text-gray-200 ${
          mono ? "font-mono" : ""
        }`}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function SpanLinks({
  label,
  spanIds,
  spansById,
  onSelectSpan,
}: {
  label: string;
  spanIds: string[];
  spansById: Map<string, OrganismLogSpanAnalysis>;
  onSelectSpan: (spanId: string) => void;
}) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
        {label}
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {spanIds.length === 0 ? (
          <span className="text-[11px] text-gray-500 dark:text-gray-400">none</span>
        ) : (
          spanIds.map((spanId) => (
            <button
              key={`${label}:${spanId}`}
              onClick={() => onSelectSpan(spanId)}
              className="rounded-full border border-gray-200 bg-white px-2 py-1 text-[10px] font-medium text-gray-700 transition hover:border-gray-300 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
            >
              {spansById.get(spanId)?.label || spanId}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
