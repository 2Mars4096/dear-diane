import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { portHandleId } from "../lib/graphAdapter";
import { useGraphStore } from "../store/useGraphStore";
import { NodeIcon } from "../lib/nodeIcons";
import type { DanNode } from "../types/graph";

const TYPE_COLORS: Record<string, string> = {
  llm_operator: "#6366f1",
  tool_operator: "#8b5cf6",
  code_operator: "#3b82f6",
  if_else: "#f59e0b",
  while_loop: "#f97316",
  for_each: "#ef4444",
  reduce: "#ec4899",
  router: "#14b8a6",
  human_in_the_loop: "#06b6d4",
  composite: "#10b981",
};

const STATUS_RING: Record<string, string> = {
  node_started: "ring-2 ring-yellow-400",
  node_completed: "ring-2 ring-green-500",
  node_failed: "ring-2 ring-red-500",
  node_skipped: "ring-2 ring-gray-400",
};

function DanNodeComponent({ id, data, selected }: NodeProps) {
  const nodeStatuses = useGraphStore((s) => s.nodeStatuses);
  const runStatus = useGraphStore((s) => s.runStatus);
  const nodeTimings = useGraphStore((s) => s.nodeTimings);
  const d = data as unknown as DanNode;
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

  const isBlackbox = !!(d as Record<string, unknown>).is_blackbox;
  const hasBodyGraph =
    (d.node_type === "while_loop" ||
      d.node_type === "for_each" ||
      d.node_type === "composite") &&
    !!d.body_graph;

  return (
    <div
      className={`rounded-lg shadow-md bg-white border-2 min-w-[160px] ${ringClass} ${animClass} ${dimmed ? "opacity-40" : ""}`}
      style={{ borderColor: selected ? "#2563eb" : color }}
    >
      {/* Header */}
      <div
        className="px-3 py-1.5 rounded-t-md text-white text-xs font-semibold flex items-center gap-1.5"
        style={{ backgroundColor: color }}
      >
        <NodeIcon type={d.node_type} className="text-white/90 shrink-0" />
        <span className="truncate">{d.name || d.node_type}</span>
        {/* 5-1: show lock for blackbox, play icon for drillable */}
        {hasBodyGraph && isBlackbox && (
          <span className="ml-1 text-[10px] opacity-80" title="Blackbox — no drill-in">&#x1F512;</span>
        )}
        {hasBodyGraph && !isBlackbox && (
          <span className="ml-1 text-[10px] opacity-80" title="Double-click to drill in">&#x25B6;</span>
        )}
      </div>

      {/* Body — port labels */}
      <div className="flex justify-between px-2 py-1.5 text-[11px] text-gray-600 gap-4">
        <div className="flex flex-col gap-0.5">
          {(d.input_ports ?? []).map((p) => (
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
          {(d.output_ports ?? []).map((p) => (
            <div key={p.name} className="relative">
              <span className="pr-3">{p.name}</span>
              <Handle
                type="source"
                position={Position.Right}
                id={portHandleId(p.name)}
                className="!w-2.5 !h-2.5 !bg-gray-400 !border-white"
              />
            </div>
          ))}
        </div>
      </div>

      {/* Status indicator + 5-2 duration badge */}
      {status && (
        <div className="px-2 pb-1 text-[10px] text-gray-400 flex items-center justify-between">
          <span>{status.replace("node_", "")}</span>
          {durationLabel && (
            <span className="bg-gray-100 text-gray-500 px-1 rounded">
              {durationLabel}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(DanNodeComponent);
