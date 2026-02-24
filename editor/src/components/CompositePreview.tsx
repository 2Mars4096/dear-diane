import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  type NodeTypes,
} from "@xyflow/react";
import DanNode from "./DanNode";
import { useGraphStore } from "../store/useGraphStore";
import { danGraphToReactFlow } from "../lib/graphAdapter";
import type { DanGraph, DanNode as DanNodeType } from "../types/graph";

const nodeTypes: NodeTypes = { danNode: DanNode };

interface Props {
  onClose: () => void;
}

export default function CompositePreview({ onClose }: Props) {
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const nodes = useGraphStore((s) => s.nodes);
  const danGraph = useGraphStore((s) => s.danGraph);

  const subGraphData = useMemo(() => {
    if (!selectedNodeId || !danGraph) return null;

    const node = nodes.find((n) => n.id === selectedNodeId);
    if (!node) return null;

    const d = node.data as unknown as DanNodeType;
    const bodyGraphKey =
      d.node_type === "while_loop" ||
      d.node_type === "for_each" ||
      d.node_type === "composite"
        ? d.body_graph
        : undefined;
    if (!bodyGraphKey) return null;

    const subGraph = danGraph.sub_graphs?.[bodyGraphKey] as DanGraph | undefined;
    if (!subGraph) return null;

    return { key: bodyGraphKey, ...danGraphToReactFlow(subGraph) };
  }, [selectedNodeId, nodes, danGraph]);

  if (!subGraphData) {
    return (
      <div className="absolute inset-0 bg-black/40 flex items-center justify-center z-50">
        <div className="bg-white rounded-lg shadow-xl p-6 max-w-md">
          <p className="text-sm text-gray-600">No sub-graph found for this node.</p>
          <button
            onClick={onClose}
            className="mt-4 px-4 py-1.5 text-xs rounded bg-gray-200 hover:bg-gray-300"
          >
            Close
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-[80vw] h-[70vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200">
          <h2 className="text-sm font-semibold text-gray-700">
            Sub-graph: {subGraphData.key}
            <span className="ml-2 text-xs text-gray-400 font-normal">read-only</span>
          </h2>
          <button
            onClick={onClose}
            className="px-3 py-1 text-xs rounded bg-gray-200 hover:bg-gray-300"
          >
            Close
          </button>
        </div>
        <div className="flex-1">
          <ReactFlow
            nodes={subGraphData.nodes}
            edges={subGraphData.edges}
            nodeTypes={nodeTypes}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={16} size={1} />
          </ReactFlow>
        </div>
      </div>
    </div>
  );
}
