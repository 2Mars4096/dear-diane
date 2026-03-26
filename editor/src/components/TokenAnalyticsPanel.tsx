import { useEffect, useMemo, useState } from "react";
import {
  fetchTelemetryAnalytics,
  type TelemetryAnalyticsResponse,
} from "../lib/api";
import { useGraphStore } from "../store/useGraphStore";
import type { WasteFinding, OptimizationMutation } from "../types/graph";

// ---------------------------------------------------------------------------
// Inline SVG icons
// ---------------------------------------------------------------------------

function AlertTriangleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

function ZapIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  );
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function LoaderIcon({ className }: { className?: string }) {
  return (
    <svg className={`${className ?? ""} animate-spin`} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12a9 9 0 11-6.219-8.56" />
    </svg>
  );
}

function ShieldIcon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CATEGORY_COLORS: Record<string, string> = {
  unused_context: "text-orange-600 bg-orange-50",
  duplicate: "text-purple-600 bg-purple-50",
  loop_growth: "text-red-600 bg-red-50",
  oversized_system: "text-amber-600 bg-amber-50",
  unused_memory_rag: "text-blue-600 bg-blue-50",
  jit_opportunity: "text-cyan-600 bg-cyan-50",
  memoization_opportunity: "text-indigo-600 bg-indigo-50",
  reference_opportunity: "text-teal-600 bg-teal-50",
};

const CATEGORY_LABELS: Record<string, string> = {
  unused_context: "Unused Context",
  duplicate: "Duplicate Info",
  loop_growth: "Loop Growth",
  oversized_system: "Large System Prompt",
  unused_memory_rag: "Unused Memory/RAG",
  jit_opportunity: "JIT Loading",
  memoization_opportunity: "Memoization",
  reference_opportunity: "Pass-by-Reference",
};

const SAVINGS_FACTORS: Record<string, string> = {
  jit_opportunity: "tool schema tokens removed on-demand",
  memoization_opportunity: "avg tokens saved via cache hits",
  unused_context: "unused context tokens dropped",
  duplicate: "deduped payload tokens",
  loop_growth: "bounded iteration token growth",
  oversized_system: "system prompt tokens trimmed",
  unused_memory_rag: "unused retrieval tokens removed",
  reference_opportunity: "pass-by-ref instead of copy",
};

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-100 text-green-700",
  pending: "bg-amber-100 text-amber-700",
  applied: "bg-blue-100 text-blue-700",
  expired: "bg-gray-100 text-gray-500",
};

const TELEMETRY_WINDOW_HOURS = 24;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}k`;
  return n.toLocaleString();
}

function formatCurrency(value: number): string {
  return value >= 0.01 ? `$${value.toFixed(2)}` : `$${value.toFixed(4)}`;
}

function formatTelemetryHour(value: string): string {
  if (!value) return "unknown";
  if (value.length >= 13) {
    return `${value.slice(11, 13)}:00 UTC`;
  }
  return value;
}

async function requestEditorTelemetrySummary() {
  return fetchTelemetryAnalytics({
    surface: "editor",
    hours: TELEMETRY_WINDOW_HOURS,
  });
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FindingCard({
  finding,
  mutation,
  mutationIndex,
  nodeName,
  nodeTokens,
}: {
  finding: WasteFinding;
  mutation?: OptimizationMutation;
  mutationIndex?: number;
  nodeName: string;
  nodeTokens: number;
}) {
  const applyOptimizationMutation = useGraphStore((s) => s.applyOptimizationMutation);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState(false);

  const colorClass = CATEGORY_COLORS[finding.category] ?? "text-gray-600 bg-gray-50";
  const label = CATEGORY_LABELS[finding.category] ?? finding.category;

  const handleApply = async () => {
    if (mutationIndex == null) return;
    setApplying(true);
    try {
      await applyOptimizationMutation(mutationIndex);
      setApplied(true);
    } finally {
      setApplying(false);
    }
  };

  const savingsLabel = SAVINGS_FACTORS[finding.category];
  const estSavings = finding.estimated_saveable_tokens;
  const pct = nodeTokens > 0 ? ((estSavings / nodeTokens) * 100).toFixed(1) : null;

  return (
    <div className="border border-gray-200 rounded-md p-2 hover:border-gray-300 transition-colors">
      <div className="flex items-start gap-2">
        <AlertTriangleIcon className="text-amber-500 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${colorClass}`}>
              {label}
            </span>
            <span className="text-[10px] text-gray-400 truncate">
              {nodeName}
            </span>
          </div>
          <p className="text-[11px] text-gray-700 mt-0.5 leading-snug">
            {finding.description}
          </p>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <span className="text-[10px] text-amber-600 font-medium">
              ~{finding.estimated_saveable_tokens.toLocaleString()} tokens saveable
            </span>
            {finding.suggestion && (
              <span className="text-[10px] text-gray-500 italic truncate">
                {finding.suggestion}
              </span>
            )}
          </div>
          {/* 18-4 task 4: Before/after token estimation */}
          {nodeTokens > 0 ? (
            <div className="mt-1 flex items-center gap-1.5 text-[10px]">
              <span className="text-indigo-600 font-medium">
                Est. savings: ~{formatTokens(estSavings)} tokens
                {pct ? ` (${pct}%)` : ""}
              </span>
              {savingsLabel && (
                <span className="text-gray-400 italic">(estimated — {savingsLabel})</span>
              )}
            </div>
          ) : (
            <div className="mt-1 text-[10px] text-gray-400 italic">
              Run a workflow first to see estimates
            </div>
          )}
        </div>
        {mutation && mutationIndex != null && !applied && (
          <button
            onClick={handleApply}
            disabled={applying}
            className="shrink-0 flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded bg-indigo-50 text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {applying ? (
              <LoaderIcon className="text-indigo-500" />
            ) : (
              <ZapIcon className="text-indigo-500" />
            )}
            Apply
          </button>
        )}
        {applied && (
          <span className="shrink-0 flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-green-700">
            <CheckIcon className="text-green-500" />
            Applied
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Optimization Rules Dashboard (18-4 task 5-6)
// ---------------------------------------------------------------------------

interface OptimizationRule {
  id: string;
  name: string;
  category: string;
  status: "active" | "pending" | "applied";
  affectedNodes: string[];
  tokensSaved: number;
  effectiveness: number;
  mutationIndex?: number;
}

function RulesDashboard({
  rules,
  totalSaved,
  nodeNameMap,
  applyingId,
  setApplyingId,
  disabledIds,
  setDisabledIds,
}: {
  rules: OptimizationRule[];
  totalSaved: number;
  nodeNameMap: Record<string, string>;
  applyingId: string | null;
  setApplyingId: (id: string | null) => void;
  disabledIds: Set<string>;
  setDisabledIds: (updater: (prev: Set<string>) => Set<string>) => void;
}) {
  const applyOptimizationMutation = useGraphStore((s) => s.applyOptimizationMutation);

  const handleApply = async (rule: OptimizationRule) => {
    if (rule.mutationIndex == null) return;
    setApplyingId(rule.id);
    try {
      await applyOptimizationMutation(rule.mutationIndex);
    } finally {
      setApplyingId(null);
    }
  };

  const handleDisable = (ruleId: string) => {
    setDisabledIds((prev) => new Set([...prev, ruleId]));
  };

  const activeRules = rules.filter((r) => r.status !== "applied" && !disabledIds.has(r.id));
  const pendingRules = activeRules.filter((r) => r.status === "pending");
  const appliedOrDisabled = rules.filter((r) => r.status === "applied" || disabledIds.has(r.id));

  if (rules.length === 0) {
    return (
      <div className="px-3 py-4 text-center">
        <ShieldIcon className="text-gray-300 mx-auto mb-2" />
        <p className="text-[11px] text-gray-400">
          No optimization rules active. Run workflows to generate recommendations.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Cumulative savings banner */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-emerald-50/60 rounded text-[11px]">
        <CheckIcon className="text-emerald-500" />
        <span className="text-emerald-700 font-medium">
          Total savings: ~{formatTokens(totalSaved)} tokens across {rules.length} rule{rules.length !== 1 ? "s" : ""}
        </span>
        {pendingRules.length > 0 && (
          <>
            <span className="text-gray-300">|</span>
            <span className="text-amber-600">{pendingRules.length} pending approval</span>
          </>
        )}
      </div>

      {/* Rules table */}
      <div className="border border-gray-200 rounded-md overflow-hidden">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="bg-gray-50 text-gray-500 uppercase tracking-wide">
              <th className="text-left px-2 py-1.5 font-medium">Rule</th>
              <th className="text-left px-2 py-1.5 font-medium">Status</th>
              <th className="text-left px-2 py-1.5 font-medium">Nodes</th>
              <th className="text-right px-2 py-1.5 font-medium">Saved</th>
              <th className="text-right px-2 py-1.5 font-medium">Eff. %</th>
              <th className="px-2 py-1.5 font-medium w-16" />
            </tr>
          </thead>
          <tbody>
            {activeRules.map((rule) => {
              const colorClass = CATEGORY_COLORS[rule.category] ?? "text-gray-600 bg-gray-50";
              return (
                <tr key={rule.id} className="border-t border-gray-100 hover:bg-gray-50/50">
                  <td className="px-2 py-1.5">
                    <span className={`inline-block px-1 py-0.5 rounded text-[9px] font-medium ${colorClass}`}>
                      {rule.name}
                    </span>
                  </td>
                  <td className="px-2 py-1.5">
                    <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-medium ${STATUS_COLORS[rule.status] ?? "bg-gray-100 text-gray-600"}`}>
                      {rule.status}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-gray-600 max-w-[120px] truncate" title={rule.affectedNodes.map((n) => nodeNameMap[n] ?? n).join(", ")}>
                    {rule.affectedNodes.slice(0, 2).map((n) => nodeNameMap[n] ?? n.slice(0, 8)).join(", ")}
                    {rule.affectedNodes.length > 2 && ` +${rule.affectedNodes.length - 2}`}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-indigo-600">
                    ~{formatTokens(rule.tokensSaved)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-gray-600">
                    {rule.effectiveness.toFixed(0)}%
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    {rule.status === "pending" && rule.mutationIndex != null && (
                      <button
                        onClick={() => handleApply(rule)}
                        disabled={applyingId === rule.id}
                        className="px-1.5 py-0.5 text-[9px] font-medium rounded bg-indigo-50 text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 transition-colors"
                      >
                        {applyingId === rule.id ? "..." : "Apply"}
                      </button>
                    )}
                    {rule.status === "active" && (
                      <button
                        onClick={() => handleDisable(rule.id)}
                        className="px-1.5 py-0.5 text-[9px] font-medium rounded bg-gray-100 text-gray-600 hover:bg-red-50 hover:text-red-600 transition-colors"
                      >
                        Disable
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {appliedOrDisabled.map((rule) => (
              <tr key={rule.id} className="border-t border-gray-100 opacity-50">
                <td className="px-2 py-1.5 text-gray-400">{rule.name}</td>
                <td className="px-2 py-1.5">
                  <span className="inline-block px-1.5 py-0.5 rounded text-[9px] font-medium bg-gray-100 text-gray-500">
                    {disabledIds.has(rule.id) ? "disabled" : "applied"}
                  </span>
                </td>
                <td className="px-2 py-1.5 text-gray-400" />
                <td className="px-2 py-1.5 text-right font-mono text-gray-400">~{formatTokens(rule.tokensSaved)}</td>
                <td className="px-2 py-1.5 text-right font-mono text-gray-400">{rule.effectiveness.toFixed(0)}%</td>
                <td className="px-2 py-1.5 text-right">
                  {disabledIds.has(rule.id) && (
                    <button
                      onClick={() => setDisabledIds((prev) => {
                        const next = new Set(prev);
                        next.delete(rule.id);
                        return next;
                      })}
                      className="px-1.5 py-0.5 text-[9px] font-medium rounded bg-gray-100 text-gray-600 hover:bg-green-50 hover:text-green-600 transition-colors"
                    >
                      Re-enable
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TelemetrySummaryStrip({
  summary,
  loading,
  error,
  onRefresh,
}: {
  summary: TelemetryAnalyticsResponse | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => Promise<void>;
}) {
  const totals = summary?.totals;
  const modelRows = summary?.models.slice(0, 2) ?? [];
  const modeRows = summary?.modes.slice(0, 2) ?? [];
  const recentHours = summary?.activity_by_hour.slice(-3) ?? [];

  return (
    <div className="px-3 py-2 border-b border-slate-200 bg-slate-50/80 shrink-0">
      <div className="flex items-center gap-2 text-[11px] flex-wrap">
        <ShieldIcon className="text-slate-500" />
        <span className="text-slate-700 font-medium">
          Last {summary?.window_hours ?? TELEMETRY_WINDOW_HOURS}h telemetry
        </span>
        {loading ? (
          <span className="inline-flex items-center gap-1 text-slate-500">
            <LoaderIcon className="text-slate-400" />
            Loading...
          </span>
        ) : totals && totals.events > 0 ? (
          <>
            <span className="text-gray-400">|</span>
            <span className="text-slate-600">
              {totals.chat_turns} turns
            </span>
            <span className="text-gray-400">|</span>
            <span className="text-slate-600">
              {totals.gateway_calls} gateway calls
            </span>
            <span className="text-gray-400">|</span>
            <span className="text-slate-600">
              {formatTokens(totals.total_tokens)} tokens
            </span>
            <span className="text-gray-400">|</span>
            <span className="text-emerald-600">
              {formatCurrency(totals.total_cost)}
            </span>
          </>
        ) : (
          <span className="text-slate-500">No recent editor telemetry.</span>
        )}
        <button
          onClick={() => { void onRefresh(); }}
          className="ml-auto text-[10px] text-indigo-500 hover:text-indigo-700"
        >
          Refresh
        </button>
      </div>
      {summary && summary.totals.events > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5 text-[10px]">
          {modelRows.map((row) => (
            <span
              key={`model-${row.group_key.model_used ?? "unknown"}`}
              className="px-1.5 py-0.5 rounded bg-white border border-slate-200 text-slate-600"
            >
              {row.group_key.model_used ?? "unknown"}: {row.count} calls
            </span>
          ))}
          {modeRows.map((row) => (
            <span
              key={`mode-${row.group_key.chat_mode ?? "unknown"}`}
              className="px-1.5 py-0.5 rounded bg-white border border-slate-200 text-slate-600"
            >
              {row.group_key.chat_mode ?? "unknown"}: {row.count} turns
            </span>
          ))}
          {recentHours.map((row) => (
            <span
              key={`hour-${row.group_key.hour ?? "unknown"}`}
              className="px-1.5 py-0.5 rounded bg-white border border-slate-200 text-slate-500"
            >
              {formatTelemetryHour(row.group_key.hour ?? "")}: {row.count}
            </span>
          ))}
        </div>
      )}
      {error && (
        <div className="mt-1 text-[10px] text-red-600">
          {error}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function TokenAnalyticsPanel() {
  const wasteFindings = useGraphStore((s) => s.wasteFindings);
  const optimizationMutations = useGraphStore((s) => s.optimizationMutations);
  const analyticsLoading = useGraphStore((s) => s.analyticsLoading);
  const runStatus = useGraphStore((s) => s.runStatus);
  const runId = useGraphStore((s) => s.runId);
  const nodes = useGraphStore((s) => s.nodes);
  const fetchTokenAnalytics = useGraphStore((s) => s.fetchTokenAnalytics);
  const nodeCosts = useGraphStore((s) => s.nodeCosts);
  const nodeUsage = useGraphStore((s) => s.nodeUsage);
  const [categoryFilter, setCategoryFilter] = useState<string>("");
  const [activeSection, setActiveSection] = useState<"findings" | "rules">("findings");
  const [rulesApplyingId, setRulesApplyingId] = useState<string | null>(null);
  const [rulesDisabledIds, setRulesDisabledIds] = useState<Set<string>>(new Set());
  const [telemetrySummary, setTelemetrySummary] = useState<TelemetryAnalyticsResponse | null>(null);
  const [telemetryLoading, setTelemetryLoading] = useState(false);
  const [telemetryError, setTelemetryError] = useState<string | null>(null);

  const nodeNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const n of nodes) {
      const d = n.data as Record<string, unknown>;
      map[n.id] = (d?.name ?? d?.label ?? n.id) as string;
    }
    return map;
  }, [nodes]);

  const mutationByFinding = useMemo(() => {
    const map = new Map<string, { mutation: OptimizationMutation; index: number }>();
    for (let i = 0; i < optimizationMutations.length; i++) {
      const m = optimizationMutations[i];
      const key = `${m.finding.node_id}::${m.finding.category}::${m.finding.description}`;
      map.set(key, { mutation: m, index: i });
    }
    return map;
  }, [optimizationMutations]);

  const categories = useMemo(() => {
    const s = new Set<string>();
    for (const f of wasteFindings) s.add(f.category);
    return [...s].sort();
  }, [wasteFindings]);

  const filteredFindings = useMemo(() => {
    let result = wasteFindings;
    if (categoryFilter) {
      result = result.filter((f) => f.category === categoryFilter);
    }
    return [...result].sort((a, b) => b.estimated_saveable_tokens - a.estimated_saveable_tokens);
  }, [wasteFindings, categoryFilter]);

  const totalWaste = wasteFindings.reduce((a, f) => a + f.estimated_saveable_tokens, 0);
  const totalTokens = Object.values(nodeUsage).reduce((a, u) => a + u.total_tokens, 0);
  const totalCost = Object.values(nodeCosts).reduce((a, c) => a + c, 0);

  const refreshTelemetrySummary = async () => {
    setTelemetryLoading(true);
    setTelemetryError(null);
    try {
      const next = await requestEditorTelemetrySummary();
      setTelemetrySummary(next);
    } catch (error) {
      setTelemetryError(error instanceof Error ? error.message : "Failed to load telemetry");
    } finally {
      setTelemetryLoading(false);
    }
  };

  const handleRefresh = async () => {
    await Promise.all([fetchTokenAnalytics(), refreshTelemetrySummary()]);
  };

  useEffect(() => {
    if (!runId) return;
    void refreshTelemetrySummary();
  }, [runId]);

  // Build optimization rules from findings + mutations
  const optimizationRules = useMemo(() => {
    const ruleMap = new Map<string, OptimizationRule>();

    for (const finding of wasteFindings) {
      const ruleKey = finding.category;
      const existing = ruleMap.get(ruleKey);
      const findingKey = `${finding.node_id}::${finding.category}::${finding.description}`;
      const mutEntry = mutationByFinding.get(findingKey);

      if (existing) {
        if (!existing.affectedNodes.includes(finding.node_id)) {
          existing.affectedNodes.push(finding.node_id);
        }
        existing.tokensSaved += finding.estimated_saveable_tokens;
        if (mutEntry && existing.status !== "pending") {
          existing.status = "pending";
          existing.mutationIndex = mutEntry.index;
        }
      } else {
        ruleMap.set(ruleKey, {
          id: ruleKey,
          name: CATEGORY_LABELS[ruleKey] ?? ruleKey,
          category: ruleKey,
          status: mutEntry ? "pending" : "active",
          affectedNodes: [finding.node_id],
          tokensSaved: finding.estimated_saveable_tokens,
          effectiveness: 0,
          mutationIndex: mutEntry?.index,
        });
      }
    }

    // Recalculate effectiveness as overall
    for (const rule of ruleMap.values()) {
      if (totalTokens > 0) {
        rule.effectiveness = (rule.tokensSaved / totalTokens) * 100;
      }
    }

    return [...ruleMap.values()].sort((a, b) => b.tokensSaved - a.tokensSaved);
  }, [wasteFindings, mutationByFinding, nodeUsage, totalTokens]);

  if (!runId || (runStatus !== "completed" && runStatus !== "failed")) {
    return (
      <div className="p-3 text-xs text-gray-400 text-center">
        Run a workflow to see token optimization suggestions.
      </div>
    );
  }

  if (analyticsLoading) {
    return (
      <div className="p-3 text-xs text-gray-400 text-center flex items-center justify-center gap-2">
        <LoaderIcon className="text-gray-400" />
        Analyzing token usage...
      </div>
    );
  }

  if (wasteFindings.length === 0) {
    return (
      <div className="flex flex-col h-full">
        <TelemetrySummaryStrip
          summary={telemetrySummary}
          loading={telemetryLoading}
          error={telemetryError}
          onRefresh={handleRefresh}
        />
        <div className="px-3 py-2 border-b border-gray-200 bg-gray-50 shrink-0">
          <div className="flex items-center gap-2 text-[11px]">
            <CheckIcon className="text-green-500" />
            <span className="text-green-700 font-medium">No token waste detected</span>
            <span className="text-gray-400">|</span>
            <span className="text-gray-500">{totalTokens.toLocaleString()} tokens total</span>
            {totalCost > 0 && (
              <>
                <span className="text-gray-400">|</span>
                <span className="text-emerald-600">~${totalCost.toFixed(4)}</span>
              </>
            )}
          </div>
        </div>
        <div className="p-3 text-xs text-gray-400 text-center flex-1">
          Token usage looks efficient. No optimization recommendations at this time.
        </div>
        <div className="px-3 py-1.5 border-t border-gray-100 bg-gray-50 text-[10px] text-gray-400 text-right">
          <button
            onClick={() => { void handleRefresh(); }}
            className="text-indigo-500 hover:text-indigo-700"
          >
            Re-analyze
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <TelemetrySummaryStrip
        summary={telemetrySummary}
        loading={telemetryLoading}
        error={telemetryError}
        onRefresh={handleRefresh}
      />
      {/* Summary bar */}
      <div className="px-3 py-2 border-b border-gray-200 bg-amber-50/50 shrink-0">
        <div className="flex items-center gap-2 text-[11px] flex-wrap">
          <AlertTriangleIcon className="text-amber-500" />
          <span className="text-amber-800 font-medium">
            {wasteFindings.length} optimization{wasteFindings.length > 1 ? "s" : ""} found
          </span>
          <span className="text-gray-400">|</span>
          <span className="text-amber-600 font-medium">
            ~{totalWaste.toLocaleString()} tokens saveable
          </span>
          <span className="text-gray-400">|</span>
          <span className="text-gray-500">
            {totalTokens > 0 ? `${((totalWaste / totalTokens) * 100).toFixed(1)}% waste` : "—"}
          </span>
          {optimizationMutations.length > 0 && (
            <>
              <span className="text-gray-400">|</span>
              <span className="text-indigo-600">
                {optimizationMutations.length} auto-fixable
              </span>
            </>
          )}
        </div>
      </div>

      {/* Section tabs: Findings / Rules */}
      <div className="flex items-center gap-0 border-b border-gray-100 bg-gray-50/80 shrink-0 px-2">
        <button
          onClick={() => setActiveSection("findings")}
          className={`px-2.5 py-1 text-[10px] font-medium border-b-2 transition-colors ${
            activeSection === "findings"
              ? "border-indigo-500 text-indigo-600"
              : "border-transparent text-gray-400 hover:text-gray-600"
          }`}
        >
          Findings ({filteredFindings.length})
        </button>
        <button
          onClick={() => setActiveSection("rules")}
          className={`px-2.5 py-1 text-[10px] font-medium border-b-2 transition-colors flex items-center gap-1 ${
            activeSection === "rules"
              ? "border-indigo-500 text-indigo-600"
              : "border-transparent text-gray-400 hover:text-gray-600"
          }`}
        >
          Rules ({optimizationRules.length})
        </button>

        {/* Category filter (only in findings view) */}
        {activeSection === "findings" && categories.length > 1 && (
          <div className="flex items-center gap-1 ml-auto">
            <button
              onClick={() => setCategoryFilter("")}
              className={`px-1.5 py-0.5 text-[10px] rounded ${!categoryFilter ? "bg-indigo-100 text-indigo-700" : "text-gray-500 hover:bg-gray-100"}`}
            >
              All
            </button>
            {categories.map((cat) => (
              <button
                key={cat}
                onClick={() => setCategoryFilter(cat === categoryFilter ? "" : cat)}
                className={`px-1.5 py-0.5 text-[10px] rounded ${cat === categoryFilter ? "bg-indigo-100 text-indigo-700" : "text-gray-500 hover:bg-gray-100"}`}
              >
                {CATEGORY_LABELS[cat] ?? cat}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Content area */}
      <div className="overflow-y-auto flex-1 min-h-0 p-2">
        {activeSection === "findings" && (
          <div className="space-y-1.5">
            {filteredFindings.map((finding, i) => {
              const key = `${finding.node_id}::${finding.category}::${finding.description}`;
              const entry = mutationByFinding.get(key);
              const nodeTotal = nodeUsage[finding.node_id]?.total_tokens ?? 0;
              return (
                <FindingCard
                  key={`${key}-${i}`}
                  finding={finding}
                  mutation={entry?.mutation}
                  mutationIndex={entry?.index}
                  nodeName={nodeNameMap[finding.node_id] ?? finding.node_id}
                  nodeTokens={nodeTotal}
                />
              );
            })}
          </div>
        )}
        {activeSection === "rules" && (
          <RulesDashboard
            rules={optimizationRules}
            totalSaved={totalWaste}
            nodeNameMap={nodeNameMap}
            applyingId={rulesApplyingId}
            setApplyingId={setRulesApplyingId}
            disabledIds={rulesDisabledIds}
            setDisabledIds={setRulesDisabledIds}
          />
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-1.5 border-t border-gray-100 bg-gray-50 text-[10px] text-gray-400 flex items-center justify-between">
        <span>
          {activeSection === "findings"
            ? `${filteredFindings.length} of ${wasteFindings.length} findings shown`
            : `${optimizationRules.length} rule${optimizationRules.length !== 1 ? "s" : ""}`}
        </span>
        <button
          onClick={() => { void handleRefresh(); }}
          className="text-indigo-500 hover:text-indigo-700"
        >
          Re-analyze
        </button>
      </div>
    </div>
  );
}
