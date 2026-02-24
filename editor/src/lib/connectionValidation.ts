import type { Node, Edge, Connection } from "@xyflow/react";

export function isValidConnection(
  params: Connection,
  nodes: Node[],
  edges: Edge[],
): boolean {
  if (!params.source || !params.target) return false;
  if (params.source === params.target) return false;

  const dup = edges.some(
    (e) =>
      e.source === params.source &&
      e.target === params.target &&
      e.sourceHandle === params.sourceHandle &&
      e.targetHandle === params.targetHandle,
  );
  if (dup) return false;

  const sourceNode = nodes.find((n) => n.id === params.source);
  const targetNode = nodes.find((n) => n.id === params.target);
  if (!sourceNode || !targetNode) return false;

  return true;
}
