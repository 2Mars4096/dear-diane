import { memo, useState, useRef, useEffect, useMemo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { portHandleId } from "../lib/graphAdapter";
import { orderPorts } from "../lib/portOrdering";
import { computePortReorder } from "../lib/layout";
import { useGraphStore } from "../store/useGraphStore";
import { NodeIcon } from "../lib/nodeIcons";
import type { DanNode, InputVariable, TokenBreakdown, TierInfo, WasteFinding } from "../types/graph";

const TYPE_COLORS: Record<string, string> = {
  input: "#0ea5e9",
  llm_operator: "#6366f1",
  tool_operator: "#8b5cf6",
  code_operator: "#3b82f6",
  if_else: "#f59e0b",
  while_loop: "#f97316",
  for_each: "#ef4444",
  parallel_subagents: "#d946ef",
  reduce: "#ec4899",
  router: "#14b8a6",
  human_in_the_loop: "#06b6d4",
  gate: "#eab308",
  rag_operator: "#7c3aed",
  validator: "#059669",
  composite: "#10b981",
};

const STATUS_RING: Record<string, string> = {
  node_started: "ring-2 ring-yellow-400",
  node_completed: "ring-2 ring-green-500",
  node_failed: "ring-2 ring-red-500",
  node_skipped: "ring-2 ring-gray-400",
};

// -- 18-4: Heatmap color interpolation (green -> yellow -> red) ---------------

function heatmapColor(intensity: number): string {
  // intensity: 0 (low) to 1 (high)
  const t = Math.max(0, Math.min(1, intensity));
  if (t <= 0.5) {
    // green -> yellow
    const r = Math.round(34 + (234 - 34) * (t * 2));
    const g = Math.round(197 + (179 - 197) * (t * 2));
    const b = Math.round(94 + (8 - 94) * (t * 2));
    return `rgba(${r}, ${g}, ${b}, 0.12)`;
  } else {
    // yellow -> red
    const r = Math.round(234 + (239 - 234) * ((t - 0.5) * 2));
    const g = Math.round(179 + (68 - 179) * ((t - 0.5) * 2));
    const b = Math.round(8 + (68 - 8) * ((t - 0.5) * 2));
    return `rgba(${r}, ${g}, ${b}, 0.15)`;
  }
}

// -- 18-4: Waste category display labels --------------------------------------

const WASTE_CATEGORY_LABELS: Record<string, string> = {
  unused_context: "Unused Context",
  duplicate: "Duplicate Info",
  loop_growth: "Loop Growth",
  oversized_system: "Large System Prompt",
  unused_memory_rag: "Unused Memory/RAG",
  jit_opportunity: "JIT Opportunity",
  memoization_opportunity: "Memoize Opportunity",
  reference_opportunity: "Reference Opportunity",
};

// -- 18-4: Token breakdown tooltip component ----------------------------------

function TokenTooltip({ breakdown, model, cost }: {
  breakdown: TokenBreakdown;
  model?: string;
  cost?: number;
}) {
  const rows: Array<[string, number | string]> = [];
  if (breakdown.total_input_tokens > 0)
    rows.push(["Input tokens", breakdown.total_input_tokens.toLocaleString()]);
  if (breakdown.total_output_tokens > 0)
    rows.push(["Output tokens", breakdown.total_output_tokens.toLocaleString()]);
  if (breakdown.system_tokens && breakdown.system_tokens > 0)
    rows.push(["  System", breakdown.system_tokens.toLocaleString()]);
  if (breakdown.user_tokens && breakdown.user_tokens > 0)
    rows.push(["  User", breakdown.user_tokens.toLocaleString()]);
  if (breakdown.context_edge_tokens && breakdown.context_edge_tokens > 0)
    rows.push(["  Context edges", breakdown.context_edge_tokens.toLocaleString()]);
  if (breakdown.hyperedge_tokens && breakdown.hyperedge_tokens > 0)
    rows.push(["  Hyperedges", breakdown.hyperedge_tokens.toLocaleString()]);
  if (breakdown.memory_tokens && breakdown.memory_tokens > 0)
    rows.push(["  Memory", breakdown.memory_tokens.toLocaleString()]);
  if (breakdown.rag_tokens && breakdown.rag_tokens > 0)
    rows.push(["  RAG", breakdown.rag_tokens.toLocaleString()]);
  if (model) rows.push(["Model", model]);
  if (cost != null && cost > 0)
    rows.push(["Cost", cost >= 0.01 ? `$${cost.toFixed(2)}` : `$${cost.toFixed(4)}`]);
  if (breakdown.cache_hit != null)
    rows.push(["Cache", breakdown.cache_hit ? "hit" : "miss"]);

  return (
    <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 px-2.5 py-1.5 bg-gray-900 text-white text-[10px] rounded-md shadow-lg whitespace-nowrap pointer-events-none">
      <div className="font-semibold mb-0.5">Token Breakdown</div>
      {rows.map(([label, val], i) => (
        <div key={i} className="flex justify-between gap-3">
          <span className="text-gray-300">{label}</span>
          <span className="font-mono">{val}</span>
        </div>
      ))}
      <div className="absolute left-1/2 -translate-x-1/2 top-full w-0 h-0 border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent border-t-gray-900" />
    </div>
  );
}

// -- 18-4: Waste tooltip component --------------------------------------------

function WasteTooltip({ findings }: { findings: WasteFinding[] }) {
  return (
    <div className="absolute z-50 bottom-full right-0 mb-2 px-2.5 py-1.5 bg-amber-900 text-white text-[10px] rounded-md shadow-lg max-w-[240px] pointer-events-none">
      <div className="font-semibold mb-0.5">Token Waste Detected</div>
      {findings.map((f, i) => (
        <div key={i} className="mt-1 border-t border-amber-700 pt-1">
          <div className="font-medium text-amber-200">
            {WASTE_CATEGORY_LABELS[f.category] ?? f.category}
          </div>
          <div className="text-gray-200 leading-tight">{f.description}</div>
          <div className="text-amber-300 mt-0.5">
            ~{f.estimated_saveable_tokens.toLocaleString()} tokens saveable
          </div>
        </div>
      ))}
      <div className="absolute right-2 top-full w-0 h-0 border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent border-t-amber-900" />
    </div>
  );
}

// -- 18-5: Tier badge styling -------------------------------------------------

const TIER_BADGE: Record<string, { label: string; bg: string; text: string; border: string }> = {
  micro:     { label: "L0", bg: "bg-green-50",  text: "text-green-700",  border: "border-green-200" },
  routine:   { label: "L1", bg: "bg-blue-50",   text: "text-blue-700",   border: "border-blue-200" },
  reasoning: { label: "L2", bg: "bg-orange-50", text: "text-orange-700", border: "border-orange-200" },
  critical:  { label: "L3", bg: "bg-red-50",    text: "text-red-700",    border: "border-red-200" },
};

function TierTooltip({ info }: { info: TierInfo }) {
  const rows: Array<[string, string]> = [
    ["Tier", `${info.tier} (${TIER_BADGE[info.tier]?.label ?? "?"})`],
    ["Score", info.tier_score.toFixed(3)],
    ["Difficulty", info.difficulty.toFixed(3)],
    ["Impact", info.impact.toFixed(3)],
    ["Recoverability", info.recoverability.toFixed(3)],
    ["Model", info.model],
  ];
  return (
    <div className="absolute z-50 bottom-full left-0 mb-2 px-2.5 py-1.5 bg-gray-900 text-white text-[10px] rounded-md shadow-lg whitespace-nowrap pointer-events-none">
      <div className="font-semibold mb-0.5">Tier Scoring</div>
      {rows.map(([label, val], i) => (
        <div key={i} className="flex justify-between gap-3">
          <span className="text-gray-300">{label}</span>
          <span className="font-mono">{val}</span>
        </div>
      ))}
      <div className="absolute left-4 top-full w-0 h-0 border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent border-t-gray-900" />
    </div>
  );
}

function DanNodeComponent({ id, data, selected }: NodeProps) {
  const nodeStatuses = useGraphStore((s) => s.nodeStatuses);
  const runStatus = useGraphStore((s) => s.runStatus);
  const nodeTimings = useGraphStore((s) => s.nodeTimings);
  const nodeIterations = useGraphStore((s) => s.nodeIterations);
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const inputNodeValues = useGraphStore((s) => s.inputNodeValues);
  const setInputNodeValue = useGraphStore((s) => s.setInputNodeValue);
  const allEdges = useGraphStore((s) => s.edges);
  const allNodes = useGraphStore((s) => s.nodes);
  const validationErrors = useGraphStore((s) => s.validationErrors);
  const nodeUsage = useGraphStore((s) => s.nodeUsage);
  const nodeCosts = useGraphStore((s) => s.nodeCosts);
  // -- 18-4: Token analytics
  const tokenBreakdowns = useGraphStore((s) => s.tokenBreakdowns);
  const wasteFindings = useGraphStore((s) => s.wasteFindings);
  const tokenHeatmapEnabled = useGraphStore((s) => s.tokenHeatmapEnabled);
  // -- 18-5: Model tier info
  const nodeTiers = useGraphStore((s) => s.nodeTiers);
  const nodeErrors = validationErrors[id];
  const d = data as unknown as DanNode;

  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [showTokenTooltip, setShowTokenTooltip] = useState(false);
  const [showWasteTooltip, setShowWasteTooltip] = useState(false);
  const [showTierTooltip, setShowTierTooltip] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef(false);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const commitRename = () => {
    if (cancelRef.current) {
      cancelRef.current = false;
      setEditing(false);
      return;
    }
    const trimmed = editName.trim();
    if (trimmed && trimmed !== (d.name || d.node_type)) {
      updateNodeData(id, { name: trimmed } as Partial<DanNode>);
    }
    setEditing(false);
  };

  const color = TYPE_COLORS[d.node_type] ?? "#94a3b8";
  const status = nodeStatuses[id];
  const ringClass = STATUS_RING[status] ?? "";

  // -- 5-2: Live execution viz
  const animClass =
    status === "node_started"
      ? "dan-node-active"
      : status === "node_completed"
        ? "dan-node-complete-flash"
        : "";
  const dimmed = runStatus === "running" && !status;
  const timing = nodeTimings[id];
  let durationLabel: string | null = null;
  if (status === "node_completed" && timing?.end != null) {
    const ms = (timing.end - timing.start) * 1000;
    durationLabel = ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
  }

  const usage = nodeUsage[id];
  const cost = nodeCosts[id];
  let tokenLabel: string | null = null;
  if (usage && usage.total_tokens > 0) {
    const t = usage.total_tokens;
    tokenLabel = t >= 1000 ? `${(t / 1000).toFixed(1)}k tok` : `${t} tok`;
  }
  let costLabel: string | null = null;
  if (cost != null && cost > 0) {
    costLabel = cost >= 0.01 ? `$${cost.toFixed(2)}` : `$${cost.toFixed(4)}`;
  }

  // -- 18-4: Token heatmap intensity
  const heatmapBg = useMemo(() => {
    if (!tokenHeatmapEnabled || !usage || usage.total_tokens === 0) return undefined;
    // Find max token usage across all nodes to normalize
    const allUsage = useGraphStore.getState().nodeUsage;
    let maxTokens = 0;
    for (const u of Object.values(allUsage)) {
      if (u.total_tokens > maxTokens) maxTokens = u.total_tokens;
    }
    if (maxTokens === 0) return undefined;
    const intensity = usage.total_tokens / maxTokens;
    return heatmapColor(intensity);
  }, [tokenHeatmapEnabled, usage]);

  // -- 18-4: Token breakdown for this node
  const breakdown = tokenBreakdowns[id] as TokenBreakdown | undefined;
  const nodeModel = breakdown?.model;

  // -- 18-4: Waste findings for this node
  const nodeWasteFindings = useMemo(() => {
    return wasteFindings.filter((f) => f.node_id === id);
  }, [wasteFindings, id]);
  const hasWaste = nodeWasteFindings.length > 0;

  const iteration = nodeIterations[id];
  const portReorderMap = useMemo(() => computePortReorder(allNodes, allEdges), [allNodes, allEdges]);
  const myReorder = portReorderMap.get(id);
  const orderedInputPorts = orderPorts(d.input_ports ?? [], allEdges, allNodes, id, "input", d.node_type, myReorder);
  const orderedOutputPorts = orderPorts(d.output_ports ?? [], allEdges, allNodes, id, "output", d.node_type);

  const tierInfo = nodeTiers[id];
  const tierStyle = tierInfo ? TIER_BADGE[tierInfo.tier] : null;

  const dRecord = d as unknown as Record<string, unknown>;
  const metadata = dRecord.metadata as Record<string, unknown> | undefined;
  const blockName = metadata?.block_name as string | undefined;
  const blockVersion = metadata?.block_version as string | undefined;
  const isBlackbox = !!dRecord.is_blackbox;
  const hasBodyGraph =
    (d.node_type === "while_loop" ||
      d.node_type === "for_each" ||
      d.node_type === "composite") &&
    !!dRecord.body_graph;
  const hasBranchGraphs =
    d.node_type === "parallel_subagents" &&
    Array.isArray(dRecord.branch_graphs) &&
    (dRecord.branch_graphs as string[]).length > 0;

  return (
    <div
      className={`relative rounded-lg shadow-md border-2 min-w-[160px] ${ringClass} ${animClass} ${dimmed ? "opacity-40" : ""}`}
      style={{
        borderColor: selected ? "#2563eb" : color,
        backgroundColor: heatmapBg ?? "white",
      }}
    >
      {/* Header */}
      <div
        className="px-3 py-1.5 rounded-t-md text-white text-xs font-semibold flex items-center gap-1.5"
        style={{ backgroundColor: color }}
      >
        <NodeIcon type={d.node_type} className="text-white/90 shrink-0" />
        {editing ? (
          <input
            ref={inputRef}
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") {
                cancelRef.current = true;
                (e.target as HTMLInputElement).blur();
              }
            }}
            onMouseDown={(e) => e.stopPropagation()}
            className="bg-transparent text-white text-xs font-semibold flex-1 min-w-0 p-0 border-0 outline-none"
          />
        ) : (
          <span
            className="truncate"
            onDoubleClick={(e) => {
              e.stopPropagation();
              setEditing(true);
              setEditName(d.name || d.node_type);
            }}
          >
            {d.name || d.node_type}
          </span>
        )}
        {/* 5-1: show lock for blackbox, play icon for drillable */}
        {hasBodyGraph && isBlackbox && (
          <span className="ml-1 text-[10px] opacity-80" title="Blackbox — no drill-in">&#x1F512;</span>
        )}
        {(hasBodyGraph || hasBranchGraphs) && !isBlackbox && (
          <span className="ml-1 text-[10px] opacity-80" title="Double-click to drill in">&#x25B6;</span>
        )}
        {(d.node_type === "while_loop" || d.node_type === "for_each") && (
          <span className="ml-auto text-[10px] opacity-80" title="Loop node">&#x21BB;</span>
        )}
        {d.node_type === "gate" && (
          <span className="ml-auto text-[10px] font-bold opacity-90">
            {d.gate_mode === "while" ? "WHILE" : "IF"}
          </span>
        )}
      </div>

      {/* 6-6: Loop badges */}
      {d.node_type === "while_loop" && (
        <div className="px-2 py-0.5 text-[10px] text-orange-600 bg-orange-50 flex items-center gap-1.5">
          <span className="opacity-70">&#x21BB;</span>
          {!!(d as unknown as Record<string, unknown>).condition && (
            <span className="truncate" title={String((d as unknown as Record<string, unknown>).condition)}>
              while: {String((d as unknown as Record<string, unknown>).condition).slice(0, 30)}
            </span>
          )}
          {iteration && (
            <span className="ml-auto font-mono bg-orange-100 px-1 rounded">
              iter {iteration.current}/{iteration.total ?? "?"}
            </span>
          )}
        </div>
      )}
      {d.node_type === "for_each" && (
        <div className="px-2 py-0.5 text-[10px] text-red-600 bg-red-50 flex items-center gap-1.5">
          <span className="opacity-70">&#x21BB;</span>
          <span>for_each</span>
          {iteration && (
            <span className="ml-auto font-mono bg-red-100 px-1 rounded">
              {iteration.current}/{iteration.total ?? "?"}
            </span>
          )}
        </div>
      )}

      {d.node_type === "parallel_subagents" && (
        <div className="px-2 py-0.5 text-[10px] text-purple-600 bg-purple-50 flex items-center gap-1.5">
          <span className="opacity-70">&#x2225;</span>
          <span>parallel</span>
          {hasBranchGraphs && (
            <span className="ml-auto font-mono bg-purple-100 px-1 rounded">
              {(dRecord.branch_graphs as string[])?.length ?? 0} branches
            </span>
          )}
        </div>
      )}

      {/* Gate condition badge */}
      {d.node_type === "gate" && (
        <div className="px-2 py-0.5 text-[10px] text-yellow-700 bg-yellow-50 flex items-center gap-1.5">
          <span className="font-semibold">{d.gate_mode === "if_else" ? "IF" : "WHILE"}</span>
          {d.condition && (
            <span className="truncate" title={d.condition}>
              {d.condition.slice(0, 30)}{d.condition.length > 30 ? "\u2026" : ""}
            </span>
          )}
          {d.gate_mode === "while" && iteration && (
            <span className="ml-auto font-mono bg-yellow-100 px-1 rounded">
              iter {iteration.current}/{iteration.total ?? "?"}
            </span>
          )}
        </div>
      )}

      {/* Body — port labels */}
      <div className="flex justify-between px-2 py-1.5 text-[11px] text-gray-600 gap-4">
        <div className="flex flex-col gap-0.5">
          {orderedInputPorts.map((p) => (
            <div key={p.name} className="relative">
              <Handle
                type="target"
                position={Position.Left}
                id={portHandleId(p.name)}
                className="!w-2.5 !h-2.5 !bg-gray-400 !border-white"
              />
              <span className="pl-3">{p.name}</span>
            </div>
          ))}
        </div>
        <div className="flex flex-col gap-0.5 items-end">
          {orderedOutputPorts.map((p) => {
            let handleColor = "!bg-gray-400";
            if (d.node_type === "gate") {
              if (p.name === "true" || p.name === "continue") handleColor = "!bg-green-400";
              else if (p.name === "false" || p.name === "done") handleColor = "!bg-red-400";
            }
            return (
              <div key={p.name} className="relative">
                <span className="pr-3">{p.name}</span>
                <Handle
                  type="source"
                  position={Position.Right}
                  id={portHandleId(p.name)}
                  className={`!w-2.5 !h-2.5 ${handleColor} !border-white`}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* InputNode: editable variable fields */}
      {d.node_type === "input" && (d as unknown as { variables: InputVariable[] }).variables?.length > 0 && (
        <div className="px-2 pb-1.5 space-y-1">
          {(d as unknown as { variables: InputVariable[] }).variables.map((v) => {
            const val = inputNodeValues[id]?.[v.name] ?? v.default ?? (v.type === "boolean" ? false : v.type === "number" ? 0 : "");
            return (
              <div key={v.name} className="flex items-center gap-1.5">
                <label className="text-[10px] text-gray-500 w-14 truncate shrink-0" title={v.description || v.name}>{v.name}</label>
                {v.type === "boolean" ? (
                  <input
                    type="checkbox"
                    checked={!!val}
                    onChange={(e) => { e.stopPropagation(); setInputNodeValue(id, v.name, e.target.checked); }}
                    onMouseDown={(e) => e.stopPropagation()}
                    className="h-3 w-3"
                  />
                ) : v.type === "number" ? (
                  <input
                    type="number"
                    value={val as number}
                    onChange={(e) => { e.stopPropagation(); setInputNodeValue(id, v.name, Number(e.target.value)); }}
                    onMouseDown={(e) => e.stopPropagation()}
                    className="flex-1 min-w-0 px-1 py-0.5 text-[10px] border border-gray-200 rounded focus:outline-none focus:border-indigo-400"
                  />
                ) : (
                  <input
                    type="text"
                    value={val as string}
                    onChange={(e) => { e.stopPropagation(); setInputNodeValue(id, v.name, e.target.value); }}
                    onMouseDown={(e) => e.stopPropagation()}
                    placeholder={v.description || v.name}
                    className="flex-1 min-w-0 px-1 py-0.5 text-[10px] border border-gray-200 rounded focus:outline-none focus:border-indigo-400"
                  />
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Status indicator + 5-2 duration badge + 7-4 token/cost badges */}
      {status && (
        <div className="px-2 pb-1 text-[10px] text-gray-400 flex items-center gap-1 flex-wrap">
          <span>{status.replace("node_", "")}</span>
          <span className="ml-auto" />
          {durationLabel && (
            <span className="bg-gray-100 text-gray-500 px-1 rounded">
              {durationLabel}
            </span>
          )}
          {tokenLabel && (
            <span
              className="bg-indigo-50 text-indigo-600 px-1 rounded cursor-default"
              onMouseEnter={() => breakdown && setShowTokenTooltip(true)}
              onMouseLeave={() => setShowTokenTooltip(false)}
            >
              {tokenLabel}
            </span>
          )}
          {costLabel && (
            <span className="bg-emerald-50 text-emerald-600 px-1 rounded">
              {costLabel}
            </span>
          )}
          {/* 18-5: Tier badge */}
          {tierInfo && tierStyle && (
            <span
              className={`${tierStyle.bg} ${tierStyle.text} px-1 rounded border ${tierStyle.border} cursor-default relative`}
              onMouseEnter={() => setShowTierTooltip(true)}
              onMouseLeave={() => setShowTierTooltip(false)}
            >
              {tierStyle.label}
              {showTierTooltip && <TierTooltip info={tierInfo} />}
            </span>
          )}
        </div>
      )}

      {/* 18-4: Token breakdown tooltip (appears on hover over token label) */}
      {showTokenTooltip && breakdown && (
        <TokenTooltip breakdown={breakdown} model={nodeModel} cost={cost} />
      )}

      {/* 6-4: Validation error badge with Fix This action */}
      {nodeErrors && nodeErrors.length > 0 && (
        <div
          className="absolute -top-1 -right-1 w-3 h-3 bg-red-500 rounded-full border border-white cursor-pointer"
          title={`${nodeErrors.join("\n")}\n\nClick to fix in Debug mode`}
          onClick={(e) => {
            e.stopPropagation();
            const firstLine = nodeErrors[0].split("\n")[0].slice(0, 120);
            useGraphStore.getState().openDebugWithError(
              `Fix error in ${d.name || d.node_type}: validation — ${firstLine}`,
            );
          }}
        />
      )}

      {/* 18-4: Waste warning badge */}
      {hasWaste && (
        <div
          className="absolute -top-1.5 -left-1.5 cursor-pointer"
          onMouseEnter={() => setShowWasteTooltip(true)}
          onMouseLeave={() => setShowWasteTooltip(false)}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" className="drop-shadow-sm">
            <path d="M12 2L1 21h22L12 2z" fill="#f59e0b" stroke="#d97706" strokeWidth="1.5" />
            <text x="12" y="18" textAnchor="middle" fill="white" fontSize="13" fontWeight="bold">!</text>
          </svg>
          {showWasteTooltip && <WasteTooltip findings={nodeWasteFindings} />}
        </div>
      )}

      {/* 21-5: Block badge */}
      {blockName && (
        <div
          className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-purple-500 rounded-full border-2 border-white flex items-center justify-center cursor-default drop-shadow-sm"
          title={`Block: ${blockName}@${blockVersion ?? "?"}`}
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
          </svg>
        </div>
      )}
    </div>
  );
}

export default memo(DanNodeComponent);
