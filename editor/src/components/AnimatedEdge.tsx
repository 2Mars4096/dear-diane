import { type EdgeProps, BaseEdge, getSmoothStepPath } from "@xyflow/react";
import { useGraphStore } from "../store/useGraphStore";

export default function AnimatedEdge({
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

  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
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
