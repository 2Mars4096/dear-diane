import { type EdgeProps, BaseEdge, getSmoothStepPath, EdgeLabelRenderer } from "@xyflow/react";
import { useGraphStore } from "../store/useGraphStore";

function formatTokenCount(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1000).toFixed(1)}k`;
  return String(count);
}

export default function AnimatedEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  source,
  target,
}: EdgeProps) {
  const nodeStatuses = useGraphStore((s) => s.nodeStatuses);
  const runStatus = useGraphStore((s) => s.runStatus);
  const allEdges = useGraphStore((s) => s.edges);
  const edgeTokenCounts = useGraphStore((s) => s.edgeTokenCounts);
  const showEdgeTokenLabels = useGraphStore((s) => s.showEdgeTokenLabels);

  const siblingEdgesFromSource = allEdges.filter((e) => e.source === source);
  const sourceIndex = siblingEdgesFromSource.findIndex((e) => e.id === id);
  const sourceCount = siblingEdgesFromSource.length;

  const siblingEdgesToTarget = allEdges.filter((e) => e.target === target);
  const targetIndex = siblingEdgesToTarget.findIndex((e) => e.id === id);
  const targetCount = siblingEdgesToTarget.length;

  const sourceOffset = sourceCount > 1 ? (sourceIndex - (sourceCount - 1) / 2) * 8 : 0;
  const targetOffset = targetCount > 1 ? (targetIndex - (targetCount - 1) / 2) * 8 : 0;

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY: sourceY + sourceOffset,
    targetX,
    targetY: targetY + targetOffset,
    sourcePosition,
    targetPosition,
    borderRadius: 8,
  });

  const isActive =
    runStatus === "running" &&
    nodeStatuses[source] === "node_completed" &&
    nodeStatuses[target] === "node_started";

  const isDimmed =
    runStatus === "running" && !nodeStatuses[source] && !nodeStatuses[target];

  const tokenCount = edgeTokenCounts[id];
  const showLabel =
    showEdgeTokenLabels &&
    tokenCount != null &&
    tokenCount > 0 &&
    (runStatus === "completed" || runStatus === "failed");

  return (
    <>
      <BaseEdge
        path={edgePath}
        style={{ ...style, opacity: isDimmed ? 0.3 : 1 }}
        markerEnd={markerEnd}
      />
      {isActive && (
        <circle r="4" fill="#6366f1">
          <animateMotion
            dur="1.5s"
            repeatCount="indefinite"
            path={edgePath}
          />
        </circle>
      )}
      {showLabel && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: "none",
            }}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-gray-800/60 text-gray-100 backdrop-blur-sm shadow-sm whitespace-nowrap"
          >
            {formatTokenCount(tokenCount)} tok
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
