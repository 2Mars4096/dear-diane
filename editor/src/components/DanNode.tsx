import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { portHandleId } from "../lib/graphAdapter";
import { useGraphStore } from "../store/useGraphStore";
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
  const d = data as unknown as DanNode;
  const color = TYPE_COLORS[d.node_type] ?? "#94a3b8";
  const status = nodeStatuses[id];
  const ringClass = STATUS_RING[status] ?? "";

  const hasBodyGraph = "body_graph" in d && !!(d as Record<string, unknown>).body_graph;

  return (
    <div
      className={`rounded-lg shadow-md bg-white border-2 min-w-[160px] ${ringClass}`}
      style={{ borderColor: selected ? "#2563eb" : color }}
    >
      {/* Header */}
      <div
        className="px-3 py-1.5 rounded-t-md text-white text-xs font-semibold flex items-center justify-between"
        style={{ backgroundColor: color }}
      >
        <span className="truncate">{d.name || d.node_type}</span>
        {hasBodyGraph && (
          <span className="ml-1 text-[10px] opacity-80" title="Has sub-graph">&#x25B6;</span>
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

      {/* Status indicator */}
      {status && (
        <div className="px-2 pb-1 text-[10px] text-gray-400">
          {status.replace("node_", "")}
        </div>
      )}
    </div>
  );
}

export default memo(DanNodeComponent);
