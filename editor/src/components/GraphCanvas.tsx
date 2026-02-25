import { useCallback, useEffect, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  SelectionMode,
  useReactFlow,
  type Node as RFNode,
  type Edge,
  type NodeTypes,
  type EdgeTypes,
  type Connection,
} from "@xyflow/react";
import DanNode from "./DanNode";
import LoopGroupNode from "./LoopGroupNode";
import AnimatedEdge from "./AnimatedEdge";
import ContextMenu from "./ContextMenu";
import { useGraphStore } from "../store/useGraphStore";
import { createDefaultNode, handleToPortName } from "../lib/graphAdapter";
import { isValidConnection } from "../lib/connectionValidation";
import type { DanNode as DanNodeType, NodeTypeString } from "../types/graph";

const nodeTypes: NodeTypes = {
  danNode: DanNode,
  loopGroup: LoopGroupNode,
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
  // -- 6-1: History & multi-select
  const pushSnapshot = useGraphStore((s) => s.pushSnapshot);

  // -- 5-1: Layer navigation
  const drillIn = useGraphStore((s) => s.drillIn);
  const layerStack = useGraphStore((s) => s.layerStack);
  const isDrilledIn = layerStack.length > 0;

  const { screenToFlowPosition, fitView } = useReactFlow();

  // -- 6-2: Context menu state
  const [contextMenu, setContextMenu] = useState<{
    type: "canvas" | "node" | "edge";
    position: { x: number; y: number };
    targetId?: string;
  } | null>(null);

  const validateConnection = useCallback(
    (conn: Connection) => isValidConnection(conn, nodes, edges),
    [nodes, edges],
  );

  // -- 5-1: Reset viewport after layer change
  useEffect(() => {
    requestAnimationFrame(() => fitView({ duration: 250 }));
  }, [layerStack, fitView]);

  // -- 6-1: Snapshot on drag-stop (not every pixel move)
  const onNodeDragStop = useCallback(() => { pushSnapshot(); }, [pushSnapshot]);
  const onSelectionDragStop = useCallback(() => { pushSnapshot(); }, [pushSnapshot]);

  // -- 6-1: Sync multi-select to store
  const onSelectionChange = useCallback(
    ({ nodes: selNodes }: { nodes: RFNode[] }) => {
      const ids = new Set(selNodes.map((n) => n.id));
      useGraphStore.setState({ selectedNodeIds: ids });
    },
    [],
  );

  // -- 6-2: Edge reconnection
  const onReconnect = useCallback(
    (oldEdge: Edge, newConnection: Connection) => {
      if (!isValidConnection(newConnection, nodes, edges)) return;
      pushSnapshot();
      const updated: Edge = {
        ...oldEdge,
        source: newConnection.source!,
        target: newConnection.target!,
        sourceHandle: newConnection.sourceHandle,
        targetHandle: newConnection.targetHandle,
      };
      if (updated.data?.danEdge) {
        updated.data = {
          ...updated.data,
          danEdge: {
            ...(updated.data.danEdge as Record<string, unknown>),
            source_node_id: newConnection.source,
            target_node_id: newConnection.target,
            source_port: handleToPortName(newConnection.sourceHandle),
            target_port: handleToPortName(newConnection.targetHandle),
          },
        };
      }
      useGraphStore.setState((s) => ({
        edges: s.edges.map((e) => (e.id === oldEdge.id ? updated : e)),
        dirty: true,
      }));
    },
    [pushSnapshot, nodes, edges],
  );

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
      } else if (rawType.startsWith("workflow:")) {
        const store = useGraphStore.getState();
        store.addGraphAsNode(rawType.slice("workflow:".length), position);
      } else {
        addNode(createDefaultNode(rawType as NodeTypeString, position));
      }
    },
    [addNode, addTemplateNode, screenToFlowPosition],
  );

  return (
    <div className={`flex-1 h-full relative ${isDrilledIn ? "dan-layer-enter" : ""}`}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={validateConnection}
        edgesReconnectable
        onReconnect={onReconnect}
        selectionOnDrag
        selectionMode={SelectionMode.Partial}
        onSelectionChange={onSelectionChange}
        onNodeDragStop={onNodeDragStop}
        onSelectionDragStop={onSelectionDragStop}
        onNodeClick={(_, node) => setSelectedNode(node.id)}
        onEdgeClick={(_, edge) => setSelectedEdge(edge.id)}
        onPaneClick={() => { setSelectedNode(null); setSelectedEdge(null); setContextMenu(null); }}
        onNodeDoubleClick={(_, node) => {
          const d = node.data as unknown as DanNodeType;
          const hasBody =
            (d.node_type === "while_loop" || d.node_type === "for_each" || d.node_type === "composite") &&
            !!(d as Record<string, unknown>).body_graph &&
            !(d as Record<string, unknown>).is_blackbox;
          if (hasBody) drillIn(node.id);
        }}
        onPaneContextMenu={(e) => {
          e.preventDefault();
          setContextMenu({ type: "canvas", position: { x: e.clientX, y: e.clientY } });
        }}
        onNodeContextMenu={(e, node) => {
          e.preventDefault();
          setContextMenu({ type: "node", position: { x: e.clientX, y: e.clientY }, targetId: node.id });
        }}
        onEdgeContextMenu={(e, edge) => {
          e.preventDefault();
          setContextMenu({ type: "edge", position: { x: e.clientX, y: e.clientY }, targetId: edge.id });
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
      {contextMenu && (
        <ContextMenu
          type={contextMenu.type}
          position={contextMenu.position}
          targetId={contextMenu.targetId}
          onClose={() => setContextMenu(null)}
        />
      )}
    </div>
  );
}
