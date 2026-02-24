import { useEffect, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { useGraphStore } from "./store/useGraphStore";
import EditorToolbar from "./components/EditorToolbar";
import NodePalette from "./components/NodePalette";
import GraphCanvas from "./components/GraphCanvas";
import ConfigPanel from "./components/ConfigPanel";
import LogPanel from "./components/LogPanel";
import OutputPreview from "./components/OutputPreview";
import BreadcrumbBar from "./components/BreadcrumbBar";
import PortMappingOverlay from "./components/PortMappingOverlay";
import ToastContainer from "./components/ToastContainer";
import ExecutionTimeline from "./components/ExecutionTimeline";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";

type BottomTab = "logs" | "output";

export default function App() {
  const loadGraphList = useGraphStore((s) => s.loadGraphList);
  const [bottomTab, setBottomTab] = useState<BottomTab>("logs");

  useKeyboardShortcuts();

  useEffect(() => {
    loadGraphList();
  }, [loadGraphList]);

  return (
    <ReactFlowProvider>
      <div className="h-screen w-screen flex flex-col bg-white">
        {/* Top bar — merged toolbar */}
        <EditorToolbar />

        {/* Main area */}
        <div className="flex flex-1 min-h-0">
          {/* Left — palette */}
          <NodePalette />

          {/* Center — canvas + bottom panel */}
          <div className="flex flex-col flex-1 min-w-0">
            {/* 5-1: Breadcrumb bar + port mapping overlay for layer navigation */}
            <BreadcrumbBar />
            <PortMappingOverlay />

            <div className="flex-1 min-h-0 relative">
              <GraphCanvas />
            </div>

            {/* Bottom panel */}
            <div className="h-44 border-t border-gray-200 flex flex-col">
              {/* 5-2: Execution timeline bar */}
              <ExecutionTimeline />
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
              </div>
              <div className="flex-1 overflow-hidden">
                {bottomTab === "logs" ? <LogPanel /> : <OutputPreview />}
              </div>
            </div>
          </div>

          {/* Right — config panel */}
          <ConfigPanel />
        </div>

        <ToastContainer />
      </div>
    </ReactFlowProvider>
  );
}
