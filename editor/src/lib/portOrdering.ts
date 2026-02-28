import type { Node, Edge } from "@xyflow/react";

interface PortDef {
  name: string;
  schema?: Record<string, unknown>;
  json_schema?: Record<string, unknown>;
  required?: boolean;
  description?: string;
}

const GATE_OUTPUT_ORDER: Record<string, number> = {
  true: 0,
  continue: 0,
  false: 1,
  done: 1,
};

const GATE_INPUT_ORDER: Record<string, number> = {
  input: 0,
};

// Score bands (guaranteed non-overlapping):
//   P0 gate-pins:   0..9
//   P1 connected:   1000..1999  (peer Y clamped to [0, 999])
//   P2 unconnected: 10000+
const P1_BASE = 1000;
const P1_MAX_RANGE = 999;
const P2_BASE = 10000;

function clampPeerY(y: number): number {
  return Math.max(0, Math.min(P1_MAX_RANGE, y));
}

/**
 * Deterministic port ordering for display.
 * Precedence: P0 gate-pins > P1 connected-by-peer-Y > P2 unconnected.
 * Tie-breaker: alphabetical by name.
 *
 * Optional `portReorder` hint (from computePortReorder) provides a
 * crossing-minimized order for specific ports — used as sub-sort
 * within the P1 band when available.
 */
export function orderPorts(
  ports: PortDef[],
  edges: Edge[],
  nodes: Node[],
  nodeId: string,
  direction: "input" | "output",
  nodeType?: string,
  portReorder?: string[],
): PortDef[] {
  if (ports.length <= 1) return ports;

  const isGate = nodeType === "gate";
  const pinOrder = direction === "output" ? GATE_OUTPUT_ORDER : GATE_INPUT_ORDER;

  const reorderIndex = portReorder
    ? new Map(portReorder.map((name, i) => [name, i]))
    : null;

  const peerYMap = new Map<string, number>();
  const nodePositions = new Map(nodes.map((n) => [n.id, n.position]));

  for (const edge of edges) {
    if (direction === "output" && edge.source === nodeId) {
      const portName = edge.sourceHandle?.replace("port:", "") ?? "";
      const peerPos = nodePositions.get(edge.target);
      if (peerPos && !peerYMap.has(portName)) {
        peerYMap.set(portName, peerPos.y);
      }
    } else if (direction === "input" && edge.target === nodeId) {
      const portName = edge.targetHandle?.replace("port:", "") ?? "";
      const peerPos = nodePositions.get(edge.source);
      if (peerPos && !peerYMap.has(portName)) {
        peerYMap.set(portName, peerPos.y);
      }
    }
  }

  const scored = ports.map((port) => {
    let score: number;

    if (isGate && port.name in pinOrder) {
      score = pinOrder[port.name];
    } else if (reorderIndex && reorderIndex.has(port.name)) {
      score = P1_BASE + reorderIndex.get(port.name)!;
    } else if (peerYMap.has(port.name)) {
      score = P1_BASE + clampPeerY(peerYMap.get(port.name) ?? 0);
    } else {
      score = P2_BASE;
    }

    return { port, score };
  });

  scored.sort((a, b) => {
    if (a.score !== b.score) return a.score - b.score;
    return a.port.name.localeCompare(b.port.name);
  });

  return scored.map((s) => s.port);
}
