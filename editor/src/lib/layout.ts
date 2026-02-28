import dagre from "@dagrejs/dagre";
import type { Node, Edge } from "@xyflow/react";

const NODE_WIDTH = 180;
const NODE_HEIGHT = 100;
const DEGENERATE_THRESHOLD = 50;

export function needsAutoLayout(nodes: Node[]): boolean {
  if (nodes.length <= 1) return false;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const n of nodes) {
    const x = n.position?.x;
    const y = n.position?.y;
    if (x == null || y == null || !isFinite(x) || !isFinite(y)) return true;
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  return (maxX - minX) < DEGENERATE_THRESHOLD && (maxY - minY) < DEGENERATE_THRESHOLD;
}

export function layoutGraph(nodes: Node[], edges: Edge[]): Node[] {
  if (nodes.length === 0) return nodes;

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 60 });

  for (const node of nodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of edges) {
    const srcPortName = edge.sourceHandle?.replace("port:", "") ?? "";
    const tgtPortName = edge.targetHandle?.replace("port:", "") ?? "";
    const weight = srcPortName && srcPortName === tgtPortName ? 3 : 1;
    g.setEdge(edge.source, edge.target, { weight, minlen: 1 });
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });
}

/**
 * Post-layout crossing minimization for multi-edge node pairs.
 * Returns a map of nodeId -> ordered port names that reduce crossings.
 * Runs once after layout; results cached by caller.
 *
 * For each (source, target) pair with 2+ edges, we look up the actual
 * port render positions on both sides. If the source-side order and
 * target-side order create crossings, we reorder the target ports to
 * match the source-side order, eliminating the cross.
 */
export function computePortReorder(
  nodes: Node[],
  edges: Edge[],
): Map<string, string[]> {
  const reorder = new Map<string, string[]>();

  const pairEdges = new Map<string, Edge[]>();
  for (const edge of edges) {
    const key = `${edge.source}::${edge.target}`;
    const arr = pairEdges.get(key) ?? [];
    arr.push(edge);
    pairEdges.set(key, arr);
  }

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  for (const [, edgesInPair] of pairEdges) {
    if (edgesInPair.length < 2) continue;

    const sourceNode = nodeMap.get(edgesInPair[0].source);
    const targetNode = nodeMap.get(edgesInPair[0].target);
    if (!sourceNode || !targetNode) continue;

    const srcPorts: string[] = ((sourceNode.data as Record<string, unknown>).output_ports as Array<{ name: string }> ?? []).map((p) => p.name);
    const tgtPorts: string[] = ((targetNode.data as Record<string, unknown>).input_ports as Array<{ name: string }> ?? []).map((p) => p.name);

    const srcPortIndex = new Map(srcPorts.map((name, i) => [name, i]));
    const tgtPortIndex = new Map(tgtPorts.map((name, i) => [name, i]));

    const edgePortPairs = edgesInPair.map((e) => ({
      srcPort: e.sourceHandle?.replace("port:", "") ?? "",
      tgtPort: e.targetHandle?.replace("port:", "") ?? "",
    }));

    const withIndices = edgePortPairs.map((ep) => ({
      ...ep,
      srcIdx: srcPortIndex.get(ep.srcPort) ?? 999,
      tgtIdx: tgtPortIndex.get(ep.tgtPort) ?? 999,
    }));

    let crossings = 0;
    for (let i = 0; i < withIndices.length; i++) {
      for (let j = i + 1; j < withIndices.length; j++) {
        if ((withIndices[i].srcIdx - withIndices[j].srcIdx) * (withIndices[i].tgtIdx - withIndices[j].tgtIdx) < 0) {
          crossings++;
        }
      }
    }

    if (crossings > 0) {
      const sorted = [...withIndices].sort((a, b) => a.srcIdx - b.srcIdx);
      const reorderedTargetPorts = sorted.map((s) => s.tgtPort);
      const targetNodeId = edgesInPair[0].target;
      const existing = reorder.get(targetNodeId) ?? [];
      reorder.set(targetNodeId, [...existing, ...reorderedTargetPorts]);
    }
  }

  return reorder;
}
