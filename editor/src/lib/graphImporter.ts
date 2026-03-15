/**
 * graphImporter.ts — converts a saved DAN graph into a CompositeNode
 * insertion payload (node + sub_graphs) for the "workflow as node" feature.
 */

import type { DanGraph, DanNode, DanEdge } from "../types/graph";

interface ImportResult {
  node: DanNode;
  subGraphs: Record<string, unknown>;
}

function namespaceGraph(
  graph: DanGraph,
  prefix: string,
): { namespacedGraph: DanGraph; idMap: Map<string, string> } {
  const idMap = new Map<string, string>();

  for (const node of graph.nodes) {
    idMap.set(node.id, `${prefix}${node.id}`);
  }

  const namespacedNodes = graph.nodes.map((node) => {
    const newNode = { ...node, id: idMap.get(node.id)! };
    if ((newNode as unknown as Record<string, unknown>).body_graph) {
      (newNode as unknown as Record<string, unknown>).body_graph =
        `${prefix}${(node as unknown as Record<string, unknown>).body_graph}`;
    }
    return newNode;
  });

  const namespacedEdges = graph.edges.map((edge) => {
    const e = edge as unknown as Record<string, unknown>;
    return {
      ...e,
      id: `${prefix}${e.id}`,
      source_node_id: idMap.get(e.source_node_id as string) ?? e.source_node_id,
      target_node_id: idMap.get(e.target_node_id as string) ?? e.target_node_id,
    } as unknown as DanEdge;
  });

  const namespacedEntry = (graph.entry_points ?? []).map(
    (ep) => idMap.get(ep) ?? ep,
  );
  const namespacedExit = (graph.exit_points ?? []).map(
    (ep) => idMap.get(ep) ?? ep,
  );

  const namespacedSubGraphs: Record<string, unknown> = {};
  if (graph.sub_graphs) {
    for (const [key, subGraph] of Object.entries(graph.sub_graphs)) {
      const newKey = `${prefix}${key}`;
      const { namespacedGraph: nsSubGraph } = namespaceGraph(
        subGraph as unknown as DanGraph,
        prefix,
      );
      namespacedSubGraphs[newKey] = nsSubGraph;
    }
  }

  return {
    namespacedGraph: {
      ...graph,
      nodes: namespacedNodes,
      edges: namespacedEdges,
      entry_points: namespacedEntry,
      exit_points: namespacedExit,
      sub_graphs: namespacedSubGraphs as Record<string, DanGraph>,
    },
    idMap,
  };
}

function derivePorts(
  graph: DanGraph,
): {
  inputPorts: Array<{ name: string; schema: Record<string, unknown>; required?: boolean }>;
  outputPorts: Array<{ name: string; schema: Record<string, unknown> }>;
  inputMappings: Record<string, string>;
  outputMappings: Record<string, string>;
} {
  const entryNodes = graph.nodes.filter((n) =>
    (graph.entry_points ?? []).includes(n.id),
  );
  const exitNodes = graph.nodes.filter((n) =>
    (graph.exit_points ?? []).includes(n.id),
  );

  const inputPorts: Array<{ name: string; schema: Record<string, unknown>; required?: boolean }> = [];
  const outputPorts: Array<{ name: string; schema: Record<string, unknown> }> = [];
  const inputMappings: Record<string, string> = {};
  const outputMappings: Record<string, string> = {};
  const usedInputNames = new Set<string>();
  const usedOutputNames = new Set<string>();

  const wiredEntries = entryNodes.filter((n) => (n.input_ports ?? []).length > 0);
  for (const node of wiredEntries) {
    for (const port of node.input_ports ?? []) {
      let outerName = port.name;
      if (usedInputNames.has(outerName)) {
        outerName = `${node.id}__${port.name}`;
      }
      usedInputNames.add(outerName);
      inputPorts.push({ name: outerName, schema: port.schema ?? {}, required: false });
      inputMappings[outerName] = `${node.id}::${port.name}`;
    }
  }

  const reversedExits = [...exitNodes].reverse();
  for (const node of reversedExits) {
    for (const port of node.output_ports ?? []) {
      let outerName = port.name;
      if (usedOutputNames.has(outerName)) {
        outerName = `${node.id}__${port.name}`;
      }
      usedOutputNames.add(outerName);
      outputPorts.push({ name: outerName, schema: port.schema ?? {} });
      outputMappings[`${node.id}::${port.name}`] = outerName;
    }
  }

  return { inputPorts, outputPorts, inputMappings, outputMappings };
}

export function graphAsCompositeNode(
  graphId: string,
  importedGraph: DanGraph,
  position: { x: number; y: number },
): ImportResult {
  const prefix = `wf_${graphId.replace(/[^a-zA-Z0-9_]/g, "_")}__`;
  const bodyKey = `workflow__${graphId}__${Date.now()}`;

  const { namespacedGraph } = namespaceGraph(importedGraph, prefix);

  const nodeIds = new Set(namespacedGraph.nodes.map((n) => n.id));
  for (const ep of namespacedGraph.entry_points ?? []) {
    if (!nodeIds.has(ep)) {
      throw new Error(`Entry point '${ep}' not found in imported graph nodes`);
    }
  }
  for (const xp of namespacedGraph.exit_points ?? []) {
    if (!nodeIds.has(xp)) {
      throw new Error(`Exit point '${xp}' not found in imported graph nodes`);
    }
  }

  const { inputPorts, outputPorts, inputMappings, outputMappings } =
    derivePorts(namespacedGraph);

  const graphName =
    (importedGraph.metadata as unknown as Record<string, unknown>)?.name ??
    graphId;

  const compositeNode: DanNode = {
    id: `wf_node_${graphId}_${Date.now()}`,
    node_type: "composite",
    name: String(graphName),
    description: `Imported workflow: ${graphId}`,
    input_ports: inputPorts,
    output_ports: outputPorts,
    position,
    ui: {},
    metadata: { source_graph_id: graphId },
    body_graph: bodyKey,
    input_mappings: inputMappings,
    output_mappings: outputMappings,
    is_blackbox: false,
  } as DanNode;

  const cleanedBody = { ...namespacedGraph };
  delete (cleanedBody as Record<string, unknown>).sub_graphs;

  const subGraphs: Record<string, unknown> = {
    [bodyKey]: cleanedBody,
    ...(namespacedGraph.sub_graphs ?? {}),
  };

  return { node: compositeNode, subGraphs };
}
