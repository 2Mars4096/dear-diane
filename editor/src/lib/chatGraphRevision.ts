import type { Edge, Node } from "@xyflow/react";

import type { DanGraph, LoopGroup } from "../types/graph";
import {
  deepSetSubGraph,
  reactFlowToDanGraph,
  resolveGraphAtStack,
  type LayerStackEntry,
} from "./graphAdapter";

function normalizeForCanonicalJson(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => normalizeForCanonicalJson(item));
  }
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const normalized: Record<string, unknown> = {};
    for (const key of Object.keys(obj).sort()) {
      const nextValue = obj[key];
      if (nextValue === undefined) continue;
      normalized[key] = normalizeForCanonicalJson(nextValue);
    }
    return normalized;
  }
  return value;
}

export async function computeLocalGraphRevision(
  graph: unknown,
): Promise<string | undefined> {
  try {
    if (!globalThis.crypto?.subtle) return undefined;
    const canonical = JSON.stringify(normalizeForCanonicalJson(graph));
    const data = new TextEncoder().encode(canonical);
    const digest = await globalThis.crypto.subtle.digest("SHA-256", data);
    const hex = Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    return hex.slice(0, 16);
  } catch {
    return undefined;
  }
}

export async function getClientGraphRevision(
  graph: unknown,
  serverGraphRevision?: string | null,
  options?: { preferLocal?: boolean },
): Promise<string | undefined> {
  if (!options?.preferLocal && serverGraphRevision) return serverGraphRevision;
  return computeLocalGraphRevision(graph);
}

function applyLoopGroupMetadata(graph: DanGraph, loopGroups: LoopGroup[]): DanGraph {
  if (loopGroups.length > 0) {
    return { ...graph, metadata: { ...graph.metadata, loop_groups: loopGroups } };
  }
  const { loop_groups: _removed, ...restMeta } =
    (graph.metadata ?? {}) as Record<string, unknown>;
  return { ...graph, metadata: restMeta as DanGraph["metadata"] };
}

export function deriveGraphRevisionSource(args: {
  baseGraph: DanGraph | null;
  nodes: Node[];
  edges: Edge[];
  layerStack: LayerStackEntry[];
  loopGroups: LoopGroup[];
}): DanGraph | null {
  const { baseGraph, nodes, edges, layerStack, loopGroups } = args;
  if (!baseGraph) return null;

  const cleanEdges = edges
    .filter((e) => !e.data?.synthetic && !e.data?.loopGroupEdge)
    .map((e) =>
      e.data?._groupHidden
        ? { ...e, hidden: false, data: { ...e.data, _groupHidden: undefined } }
        : e,
    );
  const cleanNodes = nodes
    .filter((n) => n.type !== "loopGroup")
    .map((n) => (n.hidden ? { ...n, hidden: false } : n));

  if (layerStack.length === 0) {
    return applyLoopGroupMetadata(
      reactFlowToDanGraph(cleanNodes, cleanEdges, baseGraph),
      loopGroups,
    );
  }

  const subBase = resolveGraphAtStack(baseGraph, layerStack);
  if (!subBase) return baseGraph;

  const updatedSub = applyLoopGroupMetadata(
    reactFlowToDanGraph(cleanNodes, cleanEdges, subBase),
    loopGroups,
  );
  return deepSetSubGraph(baseGraph, layerStack, updatedSub);
}
