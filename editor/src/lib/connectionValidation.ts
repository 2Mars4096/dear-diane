import type { Node, Edge, Connection } from "@xyflow/react";

interface PortDef {
  name: string;
  json_schema?: Record<string, unknown>;
  schema?: Record<string, unknown>;
}

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

  // Port existence: source must have a matching output port, target a matching input port
  const sourcePortName = params.sourceHandle?.replace("port:", "") ?? "";
  const targetPortName = params.targetHandle?.replace("port:", "") ?? "";
  const srcData = sourceNode.data as Record<string, unknown>;
  const tgtData = targetNode.data as Record<string, unknown>;
  const outputPorts = (srcData.output_ports ?? []) as PortDef[];
  const inputPorts = (tgtData.input_ports ?? []) as PortDef[];
  const srcPort = outputPorts.find((p) => p.name === sourcePortName);
  const tgtPort = inputPorts.find((p) => p.name === targetPortName);
  if (!srcPort || !tgtPort) return false;

  // Single incoming edge: each input port accepts at most one incoming edge
  const hasIncoming = edges.some(
    (e) => e.target === params.target && e.targetHandle === params.targetHandle,
  );
  if (hasIncoming) return false;

  // Schema compatibility (best-effort): reject if top-level types differ
  const srcSchema = srcPort.json_schema ?? srcPort.schema;
  const tgtSchema = tgtPort.json_schema ?? tgtPort.schema;
  if (
    srcSchema && tgtSchema &&
    Object.keys(srcSchema).length > 0 && Object.keys(tgtSchema).length > 0
  ) {
    if (srcSchema.type && tgtSchema.type && srcSchema.type !== tgtSchema.type) {
      return false;
    }
  }

  return true;
}
