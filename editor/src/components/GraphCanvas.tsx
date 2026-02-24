import { useCallback, useEffect } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  type NodeTypes,
  type EdgeTypes,
  type Connection,
} from "@xyflow/react";
import DanNode from "./DanNode";
import AnimatedEdge from "./AnimatedEdge";
import { useGraphStore } from "../store/useGraphStore";
import { createDefaultNode } from "../lib/graphAdapter";
import { isValidConnection } from "../lib/connectionValidation";
import type { DanNode as DanNodeType, NodeTypeString } from "../types/graph";

const nodeTypes: NodeTypes = {
  danNode: DanNode,
};

// -- 5-2: Override smoothstep edges with animated variant
const edgeTypes: EdgeTypes = {
  smoothstep: AnimatedEdge,
};

export default function GraphCanvas() {
  const nodes = useGraphStore((s) => s.nodes);
  const edges = useGraphStore((s) => s.edges);
  const onNodesChange = useGraphStore((s) => s.onNodesChange);
  const onEdgesChange = useGraphStore((s) => s.onEdgesChange);
  const onConnect = useGraphStore((s) => s.onConnect);
  const setSelectedNode = useGraphStore((s) => s.setSelectedNode);
  const setSelectedEdge = useGraphStore((s) => s.setSelectedEdge);
  const addNode = useGraphStore((s) => s.addNode);
  // -- 5-4: Build palette
  const addTemplateNode = useGraphStore((s) => s.addTemplateNode);

  // -- 5-1: Layer navigation
  const drillIn = useGraphStore((s) => s.drillIn);
  const layerStack = useGraphStore((s) => s.layerStack);
  const isDrilledIn = layerStack.length > 0;

  const { screenToFlowPosition, fitView } = useReactFlow();

  const validateConnection = useCallback(
    (conn: Connection) => isValidConnection(conn, nodes, edges),
    [nodes, edges],
  );

  // -- 5-1: Reset viewport after layer change
  useEffect(() => {
    requestAnimationFrame(() => fitView({ duration: 250 }));
  }, [layerStack, fitView]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      // -- 5-4: Build palette — handle template: prefix
      const rawType = e.dataTransfer.getData("application/dan-node-type");
      if (!rawType) return;

      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      if (rawType.startsWith("template:")) {
        addTemplateNode(rawType.slice("template:".length), position);
      } else {
        addNode(createDefaultNode(rawType as NodeTypeString, position));
      }
    },
    [addNode, addTemplateNode, screenToFlowPosition],
  );

  return (
    <div className={`flex-1 h-full ${isDrilledIn ? "dan-layer-enter" : ""}`}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={validateConnection}
        onNodeClick={(_, node) => setSelectedNode(node.id)}
        onEdgeClick={(_, edge) => setSelectedEdge(edge.id)}
        onPaneClick={() => { setSelectedNode(null); setSelectedEdge(null); }}
        onNodeDoubleClick={(_, node) => {
          const d = node.data as unknown as DanNodeType;
          const hasBody =
            (d.node_type === "while_loop" || d.node_type === "for_each" || d.node_type === "composite") &&
            !!(d as Record<string, unknown>).body_graph &&
            !(d as Record<string, unknown>).is_blackbox;
          if (hasBody) drillIn(node.id);
        }}
        onDragOver={onDragOver}
        onDrop={onDrop}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        deleteKeyCode={["Delete", "Backspace"]}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} size={1} />
        <Controls />
        <MiniMap
          nodeStrokeWidth={3}
          pannable
          zoomable
          className="!bg-gray-100"
        />
      </ReactFlow>
    </div>
  );
}
