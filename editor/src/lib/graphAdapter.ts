/**
 * Bidirectional conversion between DAN graph JSON and React Flow state.
 *
 * DAN nodes carry input_ports / output_ports arrays.
 * React Flow uses handles identified by sourceHandle / targetHandle strings.
 * Convention: handle id = "port:<port_name>"
 */

import { type Node, type Edge, MarkerType } from "@xyflow/react";
import type { DanGraph, DanNode, DanEdge, NodeTypeString } from "../types/graph";

// -- Handle ID helpers -------------------------------------------------------

export const portHandleId = (portName: string) => `port:${portName}`;

export const handleToPortName = (handleId: string | null | undefined): string =>
  handleId?.startsWith("port:") ? handleId.slice(5) : handleId ?? "";

// -- DAN → React Flow --------------------------------------------------------

export function danNodeToReactFlow(node: DanNode): Node {
  return {
    id: node.id,
    type: "danNode",
    position: { x: node.position.x, y: node.position.y },
    data: { ...node },
  };
}

export const EDGE_COLORS: Record<string, string> = {
  data: "#6366f1",
  control: "#f59e0b",
  context: "#10b981",
};

export function danEdgeToReactFlow(edge: DanEdge): Edge {
  return {
    id: edge.id,
    source: edge.source_node_id,
    target: edge.target_node_id,
    sourceHandle: portHandleId(edge.source_port),
    targetHandle: portHandleId(edge.target_port),
    type: "smoothstep",
    animated: edge.edge_type === "context",
    label:
      edge.edge_type !== "data"
        ? edge.edge_type
        : edge.source_port
          ? `${edge.source_port} → ${edge.target_port}`
          : undefined,
    style: { stroke: EDGE_COLORS[edge.edge_type] ?? "#94a3b8" },
    markerEnd: { type: MarkerType.ArrowClosed },
    data: { danEdge: edge },
  };
}

export function danGraphToReactFlow(graph: DanGraph): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: graph.nodes.map(danNodeToReactFlow),
    edges: graph.edges.map(danEdgeToReactFlow),
  };
}

// -- React Flow → DAN --------------------------------------------------------

export function reactFlowNodeToDan(rfNode: Node): DanNode {
  const { data } = rfNode;
  return {
    ...data,
    id: rfNode.id,
    position: { x: rfNode.position.x, y: rfNode.position.y },
  } as DanNode;
}

export function reactFlowEdgeToDan(rfEdge: Edge): DanEdge {
  if (rfEdge.data?.danEdge) {
    const de = { ...rfEdge.data.danEdge } as DanEdge;
    de.source_node_id = rfEdge.source;
    de.target_node_id = rfEdge.target;
    de.source_port = handleToPortName(rfEdge.sourceHandle);
    de.target_port = handleToPortName(rfEdge.targetHandle);
    return de;
  }
  return {
    id: rfEdge.id,
    edge_type: "data",
    source_node_id: rfEdge.source,
    source_port: handleToPortName(rfEdge.sourceHandle),
    target_node_id: rfEdge.target,
    target_port: handleToPortName(rfEdge.targetHandle),
    ui: {},
    metadata: {},
  };
}

export function reactFlowToDanGraph(
  nodes: Node[],
  edges: Edge[],
  base: DanGraph,
): DanGraph {
  return {
    ...base,
    nodes: nodes.map(reactFlowNodeToDan),
    edges: edges.map(reactFlowEdgeToDan),
  };
}

// -- Default node factory ----------------------------------------------------

export function createDefaultNode(
  nodeType: NodeTypeString,
  position: { x: number; y: number },
): DanNode {
  const id = `${nodeType}_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
  const base = {
    id,
    name: nodeType.replace(/_/g, " "),
    description: "",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [{ name: "output", schema: {} }],
    position,
    ui: {},
    metadata: {},
  };

  switch (nodeType) {
    case "llm_operator":
      return { ...base, node_type: "llm_operator", model: "", prompt_template: "", system_prompt: "", temperature: 0.7, max_tokens: null, output_json_schema: null };
    case "tool_operator":
      return { ...base, node_type: "tool_operator", tool_id: "", tool_config: {} };
    case "code_operator":
      return { ...base, node_type: "code_operator", code: "", language: "python", sandbox_config: {} };
    case "if_else":
      return { ...base, node_type: "if_else", condition: "", output_ports: [{ name: "true", schema: {} }, { name: "false", schema: {} }] };
    case "while_loop":
      return { ...base, node_type: "while_loop", condition: "", body_graph: "", max_iterations: 10 };
    case "for_each":
      return { ...base, node_type: "for_each", body_graph: "", parallelism: 1, merge_strategy: "append" };
    case "reduce":
      return { ...base, node_type: "reduce", reducer: "" };
    case "router":
      return { ...base, node_type: "router", model: "", route_descriptions: {} };
    case "human_in_the_loop":
      return { ...base, node_type: "human_in_the_loop", prompt: "", timeout_seconds: null, default_action: null };
    case "composite":
      return { ...base, node_type: "composite", body_graph: "", input_mappings: {}, output_mappings: {}, is_blackbox: false };
    case "input":
      return {
        ...base,
        node_type: "input",
        input_ports: [],
        output_ports: [{ name: "input", schema: {} }],
        variables: [{ name: "input", type: "string" as const, default: "", description: "" }],
      };
  }
}
