import { useMemo, useState } from "react";
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

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FindingCard({
  finding,
  mutation,
  mutationIndex,
  nodeName,
}: {
  finding: WasteFinding;
  mutation?: OptimizationMutation;
  mutationIndex?: number;
  nodeName: string;
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
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] text-amber-600 font-medium">
              ~{finding.estimated_saveable_tokens.toLocaleString()} tokens saveable
            </span>
            {finding.suggestion && (
              <span className="text-[10px] text-gray-500 italic truncate">
                {finding.suggestion}
              </span>
            )}
          </div>
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

  const nodeNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const n of nodes) {
      const d = n.data as Record<string, unknown>;
      map[n.id] = (d?.name ?? d?.label ?? n.id) as string;
    }
    return map;
  }, [nodes]);

  // Build mutation lookup: finding → mutation index
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
    // Sort by saveable tokens descending
    return [...result].sort((a, b) => b.estimated_saveable_tokens - a.estimated_saveable_tokens);
  }, [wasteFindings, categoryFilter]);

  const totalWaste = wasteFindings.reduce((a, f) => a + f.estimated_saveable_tokens, 0);
  const totalTokens = Object.values(nodeUsage).reduce((a, u) => a + u.total_tokens, 0);
  const totalCost = Object.values(nodeCosts).reduce((a, c) => a + c, 0);

  // No run completed yet
  if (!runId || (runStatus !== "completed" && runStatus !== "failed")) {
    return (
      <div className="p-3 text-xs text-gray-400 text-center">
        Run a workflow to see token optimization suggestions.
      </div>
    );
  }

  // Loading state
  if (analyticsLoading) {
    return (
      <div className="p-3 text-xs text-gray-400 text-center flex items-center justify-center gap-2">
        <LoaderIcon className="text-gray-400" />
        Analyzing token usage...
      </div>
    );
  }

  // No findings
  if (wasteFindings.length === 0) {
    return (
      <div className="flex flex-col h-full">
        {/* Summary */}
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
            onClick={fetchTokenAnalytics}
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

      {/* Filter bar */}
      {categories.length > 1 && (
        <div className="px-3 py-1 border-b border-gray-100 bg-gray-50 shrink-0 flex items-center gap-1">
          <span className="text-[10px] text-gray-400">Filter:</span>
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

      {/* Findings list */}
      <div className="overflow-y-auto flex-1 min-h-0 p-2 space-y-1.5">
        {filteredFindings.map((finding, i) => {
          const key = `${finding.node_id}::${finding.category}::${finding.description}`;
          const entry = mutationByFinding.get(key);
          return (
            <FindingCard
              key={`${key}-${i}`}
              finding={finding}
              mutation={entry?.mutation}
              mutationIndex={entry?.index}
              nodeName={nodeNameMap[finding.node_id] ?? finding.node_id}
            />
          );
        })}
      </div>

      {/* Footer */}
      <div className="px-3 py-1.5 border-t border-gray-100 bg-gray-50 text-[10px] text-gray-400 flex items-center justify-between">
        <span>
          {filteredFindings.length} of {wasteFindings.length} findings shown
        </span>
        <button
          onClick={fetchTokenAnalytics}
          className="text-indigo-500 hover:text-indigo-700"
        >
          Re-analyze
        </button>
      </div>
    </div>
  );
}
