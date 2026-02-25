/**
 * Bidirectional conversion between DAN graph JSON and React Flow state.
 *
 * DAN nodes carry input_ports / output_ports arrays.
 * React Flow uses handles identified by sourceHandle / targetHandle strings.
 * Convention: handle id = "port:<port_name>"
 */

import { type Node, type Edge, MarkerType } from "@xyflow/react";
import type { DanGraph, DanNode, DanEdge, NodeTypeString, LoopGroup } from "../types/graph";

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
  const rfNodes = graph.nodes.map(danNodeToReactFlow);
  const rfEdges = graph.edges.map(danEdgeToReactFlow);

  const nodeMap = new Map(graph.nodes.map((n) => [n.id, n]));
  for (const rfEdge of rfEdges) {
    const sourceNode = nodeMap.get(rfEdge.source);
    if (
      sourceNode?.node_type === "gate" &&
      rfEdge.sourceHandle === portHandleId("continue")
    ) {
      rfEdge.type = "smoothstep";
      rfEdge.animated = true;
      rfEdge.style = {
        stroke: "#9ca3af",
        strokeDasharray: "5 5",
        strokeWidth: 2,
      };
      rfEdge.label = "loop back";
      rfEdge.labelStyle = { fontSize: 10, fill: "#9ca3af" };
      rfEdge.data = { ...rfEdge.data, isBackEdge: true };
    }
  }

  return { nodes: rfNodes, edges: rfEdges };
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
    nodes: nodes.filter((n) => n.type !== "loopGroup").map(reactFlowNodeToDan),
    edges: edges.map(reactFlowEdgeToDan),
  };
}

// -- Loop group helpers ------------------------------------------------------

const ESTIMATED_NODE_WIDTH = 220;
const ESTIMATED_NODE_HEIGHT = 100;
const GROUP_PADDING = 40;

/**
 * Inject visual loop-group nodes into the React Flow state.
 * Collapsed groups hide member nodes and redirect cross-boundary edges
 * through a synthetic placeholder. Expanded groups add a background rectangle.
 */
export function injectLoopGroups(
  baseNodes: Node[],
  baseEdges: Edge[],
  loopGroups: LoopGroup[],
): { nodes: Node[]; edges: Edge[] } {
  if (!loopGroups || loopGroups.length === 0) return { nodes: baseNodes, edges: baseEdges };

  const nodes = baseNodes.map((n) => ({ ...n }));
  const edges = baseEdges.map((e) => ({ ...e, data: e.data ? { ...e.data } : {} }));
  const newNodes: Node[] = [];
  const newEdges: Edge[] = [];

  for (const group of loopGroups) {
    const memberSet = new Set(group.memberNodeIds);
    const memberNodes = nodes.filter((n) => memberSet.has(n.id));
    if (memberNodes.length === 0) continue;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of memberNodes) {
      const w = n.measured?.width ?? ESTIMATED_NODE_WIDTH;
      const h = n.measured?.height ?? ESTIMATED_NODE_HEIGHT;
      minX = Math.min(minX, n.position.x);
      minY = Math.min(minY, n.position.y);
      maxX = Math.max(maxX, n.position.x + w);
      maxY = Math.max(maxY, n.position.y + h);
    }

    const groupNodeId = `loop-group-${group.id}`;

    if (group.collapsed) {
      for (const n of nodes) {
        if (memberSet.has(n.id)) n.hidden = true;
      }

      const cx = (minX + maxX) / 2 - 80;
      const cy = (minY + maxY) / 2 - 25;

      newNodes.push({
        id: groupNodeId,
        type: "loopGroup",
        position: { x: cx, y: cy },
        data: {
          groupId: group.id,
          label: group.label,
          collapsed: true,
          gateNodeId: group.gateNodeId,
          memberCount: group.memberNodeIds.length,
        },
      });

      for (const edge of edges) {
        const srcIn = memberSet.has(edge.source);
        const tgtIn = memberSet.has(edge.target);

        if (srcIn && !tgtIn) {
          edge.hidden = true;
          edge.data = { ...edge.data, _groupHidden: true };
          newEdges.push({
            ...edge,
            id: `synth-out-${edge.id}`,
            source: groupNodeId,
            sourceHandle: "port:group-out",
            hidden: false,
            data: { ...edge.data, loopGroupEdge: true, synthetic: true, _groupHidden: undefined },
          });
        } else if (!srcIn && tgtIn) {
          edge.hidden = true;
          edge.data = { ...edge.data, _groupHidden: true };
          newEdges.push({
            ...edge,
            id: `synth-in-${edge.id}`,
            target: groupNodeId,
            targetHandle: "port:group-in",
            hidden: false,
            data: { ...edge.data, loopGroupEdge: true, synthetic: true, _groupHidden: undefined },
          });
        } else if (srcIn && tgtIn) {
          edge.hidden = true;
          edge.data = { ...edge.data, _groupHidden: true };
        }
      }
    } else {
      const x = minX - GROUP_PADDING;
      const y = minY - GROUP_PADDING - 24;
      const width = maxX - minX + GROUP_PADDING * 2;
      const height = maxY - minY + GROUP_PADDING * 2 + 24;

      newNodes.push({
        id: groupNodeId,
        type: "loopGroup",
        position: { x, y },
        zIndex: -1,
        selectable: false,
        draggable: false,
        connectable: false,
        data: {
          groupId: group.id,
          label: group.label,
          collapsed: false,
          gateNodeId: group.gateNodeId,
          memberCount: group.memberNodeIds.length,
        },
        style: { width, height },
      });
    }
  }

  return { nodes: [...nodes, ...newNodes], edges: [...edges, ...newEdges] };
}

/**
 * Remove all loop-group visual artifacts (group nodes, synthetic edges,
 * hidden flags) to get back to the clean base graph state.
 */
export function stripLoopGroups(
  nodes: Node[],
  edges: Edge[],
): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: nodes
      .filter((n) => n.type !== "loopGroup")
      .map((n) => (n.hidden ? { ...n, hidden: false } : n)),
    edges: edges
      .filter((e) => !e.data?.loopGroupEdge)
      .map((e) =>
        e.data?._groupHidden
          ? { ...e, hidden: false, data: { ...e.data, _groupHidden: undefined } }
          : e,
      ),
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
    case "gate":
      return { ...base, node_type: "gate", gate_mode: "if_else", condition: "", max_iterations: 10, output_ports: [{ name: "true", schema: {} }, { name: "false", schema: {} }] };
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
