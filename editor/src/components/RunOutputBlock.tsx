import { useState } from "react";
import {
  Loader2,
  Check,
  X,
  ChevronDown,
  ChevronRight,
  Play,
  Clock,
} from "lucide-react";
import type { RunEventPayload } from "../types/chat";
import { useGraphStore } from "../store/useGraphStore";

interface NodeState {
  nodeId: string;
  status: "running" | "completed" | "failed";
  summary: string;
  output?: string;
  error?: string;
  startTime?: number;
  endTime?: number;
}

function deriveNodeStates(events: RunEventPayload[]): {
  nodes: NodeState[];
  runStatus: "running" | "completed" | "failed";
} {
  const nodeMap = new Map<string, NodeState>();
  let runStatus: "running" | "completed" | "failed" = "running";

  for (const evt of events) {
    const nodeId = evt.node_id;

    if (evt.event_type === "run_completed") {
      runStatus = "completed";
      continue;
    }
    if (evt.event_type === "run_failed") {
      runStatus = "failed";
      continue;
    }

    if (!nodeId) continue;

    if (!nodeMap.has(nodeId)) {
      nodeMap.set(nodeId, {
        nodeId,
        status: "running",
        summary: "",
      });
    }
    const node = nodeMap.get(nodeId)!;

    switch (evt.event_type) {
      case "node_started":
        node.status = "running";
        node.summary = evt.summary;
        node.startTime = Date.now();
        break;
      case "node_completed":
        node.status = "completed";
        node.summary = evt.summary;
        node.endTime = Date.now();
        break;
      case "node_failed":
        node.status = "failed";
        node.summary = evt.summary;
        node.error = evt.detail?.error as string | undefined;
        node.endTime = Date.now();
        break;
      case "node_output":
        if (evt.detail?.data) {
          node.output = JSON.stringify(evt.detail.data, null, 2).slice(0, 500);
        }
        break;
    }
  }

  return {
    nodes: Array.from(nodeMap.values()),
    runStatus,
  };
}

const RUN_STATUS_CONFIG = {
  running: {
    icon: <Loader2 size={12} className="animate-spin text-blue-500" />,
    label: "Running",
    color: "text-blue-600 bg-blue-50 border-blue-100",
  },
  completed: {
    icon: <Check size={12} className="text-green-500" />,
    label: "Completed",
    color: "text-green-600 bg-green-50 border-green-100",
  },
  failed: {
    icon: <X size={12} className="text-red-500" />,
    label: "Failed",
    color: "text-red-600 bg-red-50 border-red-100",
  },
} as const;

const NODE_STATUS_ICON = {
  running: <Loader2 size={10} className="animate-spin text-blue-400" />,
  completed: <Check size={10} className="text-green-400" />,
  failed: <X size={10} className="text-red-400" />,
} as const;

interface RunOutputBlockProps {
  events: RunEventPayload[];
  runRef?: { runId: string; scope: string; status: string } | null;
}

export default function RunOutputBlock({ events, runRef }: RunOutputBlockProps) {
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());

  if (events.length === 0 && !runRef) return null;

  const { nodes, runStatus } = deriveNodeStates(events);
  const hasError = nodes.some((n) => n.status === "failed");
  const [expanded, setExpanded] = useState(hasError);
  const finalStatus = runRef?.status === "completed" || runRef?.status === "failed"
    ? (runRef.status as "completed" | "failed")
    : runStatus;
  const cfg = RUN_STATUS_CONFIG[finalStatus];

  const toggleNode = (nodeId: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  };

  return (
    <div className={`my-2 rounded-lg border ${cfg.color} overflow-hidden`}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3 py-2 hover:bg-black/[0.02] transition-colors"
      >
        {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <Play size={11} className="flex-shrink-0" />
        <span className="text-xs font-medium flex-1 text-left">
          {expanded
            ? `Run ${runRef?.scope ? `(${runRef.scope})` : ""}`
            : `${nodes.length} node${nodes.length !== 1 ? "s" : ""} — ${cfg.label}`}
        </span>
        {cfg.icon}
        <span className="text-[10px] font-medium">{cfg.label}</span>
      </button>

      {/* Node list */}
      {expanded && nodes.length > 0 && (
        <div className="border-t border-gray-100">
          {nodes.map((node) => {
            const isExpanded = expandedNodes.has(node.nodeId);
            const elapsed =
              node.startTime && node.endTime
                ? node.endTime - node.startTime
                : null;

            return (
              <div key={node.nodeId} className="border-b border-gray-50 last:border-b-0">
                <button
                  onClick={() => toggleNode(node.nodeId)}
                  className="flex items-center gap-1.5 w-full px-3 py-1.5 hover:bg-gray-50/50 transition-colors"
                >
                  {NODE_STATUS_ICON[node.status]}
                  <span className="text-[11px] text-gray-700 flex-1 text-left truncate font-mono">
                    {node.nodeId}
                  </span>
                  {elapsed != null && (
                    <span className="flex items-center gap-0.5 text-[9px] text-gray-400 tabular-nums">
                      <Clock size={8} />
                      {elapsed < 1000 ? `${elapsed}ms` : `${(elapsed / 1000).toFixed(1)}s`}
                    </span>
                  )}
                  {(node.output || node.error) && (
                    isExpanded
                      ? <ChevronDown size={10} className="text-gray-300" />
                      : <ChevronRight size={10} className="text-gray-300" />
                  )}
                </button>

                {isExpanded && (node.output || node.error) && (
                  <div className="px-3 pb-2">
                    <pre
                      className={`text-[10px] rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono leading-relaxed ${
                        node.error
                          ? "text-red-600 bg-red-50"
                          : "text-gray-600 bg-gray-50"
                      }`}
                    >
                      {node.error || node.output}
                    </pre>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* View logs / history links */}
      {finalStatus !== "running" && (
        <div className="border-t border-gray-100 px-3 py-1.5 flex items-center gap-3">
          <button
            onClick={() => {
              useGraphStore.getState().focusLogPanel();
              setExpanded(true);
            }}
            className="text-[10px] text-indigo-600 hover:text-indigo-800 underline opacity-70 hover:opacity-100 transition"
          >
            View full logs
          </button>
          {runRef?.runId && (
            <button
              onClick={() => {
                useGraphStore.getState().focusHistoryPanel(runRef.runId);
              }}
              className="text-[10px] text-violet-600 hover:text-violet-800 underline opacity-70 hover:opacity-100 transition"
            >
              View in History
            </button>
          )}
        </div>
      )}
    </div>
  );
}
