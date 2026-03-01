import { useGraphStore } from "../store/useGraphStore";
import type { DanNode } from "../types/graph";

const TYPE_COLORS: Record<string, string> = {
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
  composite: "#10b981",
};

function formatDuration(startSec: number, endSec: number): string {
  const ms = (endSec - startSec) * 1000;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

export default function ExecutionTimeline() {
  const nodeTimings = useGraphStore((s) => s.nodeTimings);
  const nodes = useGraphStore((s) => s.nodes);
  const setSelectedNode = useGraphStore((s) => s.setSelectedNode);
  const runStatus = useGraphStore((s) => s.runStatus);

  const entries = Object.entries(nodeTimings);
  if (entries.length === 0) return null;

  const sorted = entries.sort(([, a], [, b]) => a.start - b.start);
  const minStart = sorted[0][1].start;
  const now = Date.now() / 1000;
  const maxEnd = sorted.reduce(
    (acc, [, t]) => Math.max(acc, t.end ?? now),
    minStart,
  );
  const span = maxEnd - minStart || 1;

  return (
    <div className="px-2 py-1 border-b border-gray-200 bg-gray-50">
      <div className="text-[9px] text-gray-400 mb-0.5 font-medium uppercase tracking-wide">
        Timeline
      </div>
      <div className="relative h-5 bg-gray-200 rounded overflow-hidden">
        {sorted.map(([nodeId, timing]) => {
          const rfNode = nodes.find((n) => n.id === nodeId);
          const d = rfNode?.data as unknown as DanNode | undefined;
          const color = TYPE_COLORS[d?.node_type ?? ""] ?? "#94a3b8";
          const label = d?.name || nodeId.slice(0, 8);
          const end = timing.end ?? now;
          const left = ((timing.start - minStart) / span) * 100;
          const width = Math.max(((end - timing.start) / span) * 100, 3);
          const isRunning = !timing.end && runStatus === "running";

          return (
            <div
              key={nodeId}
              className={`absolute top-0 bottom-0 rounded-sm cursor-pointer hover:brightness-110 flex items-center px-1 text-white text-[9px] font-medium truncate ${isRunning ? "opacity-70" : ""}`}
              style={{
                left: `${left}%`,
                width: `${width}%`,
                backgroundColor: color,
                minWidth: 20,
              }}
              onClick={() => setSelectedNode(nodeId)}
              title={`${label}: ${timing.end ? formatDuration(timing.start, timing.end) : "running..."}`}
            >
              {label}
            </div>
          );
        })}
      </div>
    </div>
  );
}
