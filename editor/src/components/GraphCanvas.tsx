import { useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type NodeTypes,
} from "@xyflow/react";
import DanNode from "./DanNode";
import { useGraphStore } from "../store/useGraphStore";
import { createDefaultNode, danNodeToReactFlow } from "../lib/graphAdapter";
import type { NodeTypeString } from "../types/graph";

const nodeTypes: NodeTypes = {
  danNode: DanNode,
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
  const deleteSelected = useGraphStore((s) => s.deleteSelected);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const nodeType = e.dataTransfer.getData("application/dan-node-type") as NodeTypeString;
      if (!nodeType) return;

      const bounds = (e.target as HTMLElement).closest(".react-flow")?.getBoundingClientRect();
      if (!bounds) return;

      const position = {
        x: e.clientX - bounds.left,
        y: e.clientY - bounds.top,
      };

      addNode(createDefaultNode(nodeType, position));
    },
    [addNode],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Delete" || e.key === "Backspace") {
        deleteSelected();
      }
    },
    [deleteSelected],
  );

  return (
    <div className="flex-1 h-full" onKeyDown={onKeyDown} tabIndex={0}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={(_, node) => setSelectedNode(node.id)}
        onEdgeClick={(_, edge) => setSelectedEdge(edge.id)}
        onPaneClick={() => { setSelectedNode(null); setSelectedEdge(null); }}
        onDragOver={onDragOver}
        onDrop={onDrop}
        nodeTypes={nodeTypes}
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
