import { type EdgeProps, BaseEdge, getSmoothStepPath } from "@xyflow/react";
import { useGraphStore } from "../store/useGraphStore";

export default function AnimatedEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  sourceHandleId,
  targetHandleId,
  style,
  markerEnd,
  source,
  target,
}: EdgeProps) {
  const nodeStatuses = useGraphStore((s) => s.nodeStatuses);
  const runStatus = useGraphStore((s) => s.runStatus);
  const allEdges = useGraphStore((s) => s.edges);

  const siblingEdgesFromSource = allEdges.filter((e) => e.source === source);
  const sourceIndex = siblingEdgesFromSource.findIndex((e) => e.id === id);
  const sourceCount = siblingEdgesFromSource.length;

  const siblingEdgesToTarget = allEdges.filter((e) => e.target === target);
  const targetIndex = siblingEdgesToTarget.findIndex((e) => e.id === id);
  const targetCount = siblingEdgesToTarget.length;

  const sourceOffset = sourceCount > 1 ? (sourceIndex - (sourceCount - 1) / 2) * 8 : 0;
  const targetOffset = targetCount > 1 ? (targetIndex - (targetCount - 1) / 2) * 8 : 0;

  const [edgePath] = getSmoothStepPath({
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
    </>
  );
}
