import { memo, useState, useRef, useEffect, useMemo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { portHandleId } from "../lib/graphAdapter";
import { orderPorts } from "../lib/portOrdering";
import { computePortReorder } from "../lib/layout";
import { useGraphStore } from "../store/useGraphStore";
import { NodeIcon } from "../lib/nodeIcons";
import type { DanNode, InputVariable } from "../types/graph";

const TYPE_COLORS: Record<string, string> = {
  input: "#0ea5e9",
  llm_operator: "#6366f1",
  tool_operator: "#8b5cf6",
  code_operator: "#3b82f6",
  if_else: "#f59e0b",
  while_loop: "#f97316",
  for_each: "#ef4444",
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
  const nodeErrors = validationErrors[id];
  const d = data as unknown as DanNode;

  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
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

  const iteration = nodeIterations[id];
  const portReorderMap = useMemo(() => computePortReorder(allNodes, allEdges), [allNodes, allEdges]);
  const myReorder = portReorderMap.get(id);
  const orderedInputPorts = orderPorts(d.input_ports ?? [], allEdges, allNodes, id, "input", d.node_type, myReorder);
  const orderedOutputPorts = orderPorts(d.output_ports ?? [], allEdges, allNodes, id, "output", d.node_type);

  const isBlackbox = !!(d as Record<string, unknown>).is_blackbox;
  const hasBodyGraph =
    (d.node_type === "while_loop" ||
      d.node_type === "for_each" ||
      d.node_type === "composite") &&
    !!d.body_graph;

  return (
    <div
      className={`relative rounded-lg shadow-md bg-white border-2 min-w-[160px] ${ringClass} ${animClass} ${dimmed ? "opacity-40" : ""}`}
      style={{ borderColor: selected ? "#2563eb" : color }}
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
        {hasBodyGraph && !isBlackbox && (
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
              className="bg-indigo-50 text-indigo-600 px-1 rounded"
              title={usage ? `Prompt: ${usage.prompt_tokens.toLocaleString()}, Completion: ${usage.completion_tokens.toLocaleString()}, Total: ${usage.total_tokens.toLocaleString()}` : undefined}
            >
              {tokenLabel}
            </span>
          )}
          {costLabel && (
            <span className="bg-emerald-50 text-emerald-600 px-1 rounded">
              {costLabel}
            </span>
          )}
        </div>
      )}

      {/* 6-4: Validation error badge */}
      {nodeErrors && nodeErrors.length > 0 && (
        <div
          className="absolute -top-1 -right-1 w-3 h-3 bg-red-500 rounded-full border border-white"
          title={nodeErrors.join("\n")}
        />
      )}
    </div>
  );
}

export default memo(DanNodeComponent);
