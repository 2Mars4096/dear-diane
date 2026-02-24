import { useEffect, useState, useCallback } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { useGraphStore } from "./store/useGraphStore";
import GraphSwitcher from "./components/GraphSwitcher";
import RunPanel from "./components/RunPanel";
import NodePalette from "./components/NodePalette";
import GraphCanvas from "./components/GraphCanvas";
import ConfigPanel from "./components/ConfigPanel";
import LogPanel from "./components/LogPanel";
import OutputPreview from "./components/OutputPreview";
import CompositePreview from "./components/CompositePreview";
import type { DanNode } from "./types/graph";

type BottomTab = "logs" | "output";

export default function App() {
  const loadGraphList = useGraphStore((s) => s.loadGraphList);
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const nodes = useGraphStore((s) => s.nodes);
  const [bottomTab, setBottomTab] = useState<BottomTab>("logs");
  const [showCompositePreview, setShowCompositePreview] = useState(false);

  useEffect(() => {
    loadGraphList();
  }, [loadGraphList]);

  const hasBodyGraph = useCallback(() => {
    if (!selectedNodeId) return false;
    const node = nodes.find((n) => n.id === selectedNodeId);
    if (!node) return false;
    const d = node.data as unknown as DanNode;
    return "body_graph" in d && !!(d as Record<string, unknown>).body_graph;
  }, [selectedNodeId, nodes]);

  return (
    <ReactFlowProvider>
      <div className="h-screen w-screen flex flex-col bg-white">
        {/* Top bar */}
        <GraphSwitcher />
        <RunPanel />

        {/* Main area */}
        <div className="flex flex-1 min-h-0">
          {/* Left — palette */}
          <NodePalette />

          {/* Center — canvas + bottom panel */}
          <div className="flex flex-col flex-1 min-w-0">
            <div className="flex-1 min-h-0 relative">
              <GraphCanvas />
              {showCompositePreview && (
                <CompositePreview onClose={() => setShowCompositePreview(false)} />
              )}
            </div>

            {/* Bottom panel */}
            <div className="h-44 border-t border-gray-200 flex flex-col">
              <div className="flex items-center gap-0 border-b border-gray-100 bg-gray-50 px-2">
                <button
                  onClick={() => setBottomTab("logs")}
                  className={`px-3 py-1.5 text-[11px] font-medium border-b-2 ${
                    bottomTab === "logs"
                      ? "border-indigo-500 text-indigo-600"
                      : "border-transparent text-gray-400 hover:text-gray-600"
                  }`}
                >
                  Logs
                </button>
                <button
                  onClick={() => setBottomTab("output")}
                  className={`px-3 py-1.5 text-[11px] font-medium border-b-2 ${
                    bottomTab === "output"
                      ? "border-indigo-500 text-indigo-600"
                      : "border-transparent text-gray-400 hover:text-gray-600"
                  }`}
                >
                  Output
                </button>
                {hasBodyGraph() && (
                  <>
                    <div className="flex-1" />
                    <button
                      onClick={() => setShowCompositePreview(true)}
                      className="text-[11px] text-indigo-500 hover:underline mr-1"
                    >
                      View Sub-graph
                    </button>
                  </>
                )}
              </div>
              <div className="flex-1 overflow-hidden">
                {bottomTab === "logs" ? <LogPanel /> : <OutputPreview />}
              </div>
            </div>
          </div>

          {/* Right — config panel */}
          <ConfigPanel />
        </div>
      </div>
    </ReactFlowProvider>
  );
}
