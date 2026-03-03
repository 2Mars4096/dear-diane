import { useEffect, useState, useCallback, useRef } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { useGraphStore } from "./store/useGraphStore";
import EditorToolbar from "./components/EditorToolbar";
import NodePalette from "./components/NodePalette";
import GraphCanvas from "./components/GraphCanvas";
import ConfigPanel from "./components/ConfigPanel";
import LogPanel from "./components/LogPanel";
import OutputPreview from "./components/OutputPreview";
import RunHistoryPanel from "./components/RunHistoryPanel";
import BreadcrumbBar from "./components/BreadcrumbBar";
import PortMappingOverlay from "./components/PortMappingOverlay";
import ToastContainer from "./components/ToastContainer";
import ExecutionTimeline from "./components/ExecutionTimeline";
import ChatPanel from "./components/ChatPanel";
import CommandPalette from "./components/CommandPalette";
import HumanInputDialog from "./components/HumanInputDialog";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";

type BottomTab = "logs" | "output" | "history";

const MIN_PANEL_HEIGHT = 80;
const MAX_PANEL_HEIGHT = 600;
const DEFAULT_PANEL_HEIGHT = 176;

export default function App() {
  const loadGraphList = useGraphStore((s) => s.loadGraphList);
  const restoreTabs = useGraphStore((s) => s.restoreTabs);
  const logFocusCounter = useGraphStore((s) => s.logFocusCounter);
  const historyFocusCounter = useGraphStore((s) => s.historyFocusCounter);
  const [bottomTab, setBottomTab] = useState<BottomTab>("logs");
  const [panelHeight, setPanelHeight] = useState(DEFAULT_PANEL_HEIGHT);
  const dragging = useRef(false);

  useKeyboardShortcuts();

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    const startY = e.clientY;
    const startH = panelHeight;

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const delta = startY - ev.clientY;
      setPanelHeight(Math.min(MAX_PANEL_HEIGHT, Math.max(MIN_PANEL_HEIGHT, startH + delta)));
    };
    const onUp = () => {
      dragging.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [panelHeight]);

  useEffect(() => {
    loadGraphList().then(() => restoreTabs());
  }, [loadGraphList, restoreTabs]);

  useEffect(() => {
    if (logFocusCounter > 0) setBottomTab("logs");
  }, [logFocusCounter]);

  useEffect(() => {
    if (historyFocusCounter > 0) setBottomTab("history");
  }, [historyFocusCounter]);

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

            {/* Bottom panel — resizable */}
            <div
              onMouseDown={onDragStart}
              className="h-1.5 border-t border-gray-200 cursor-row-resize hover:bg-indigo-100 active:bg-indigo-200 transition-colors flex-shrink-0"
            />
            <div style={{ height: panelHeight }} className="flex flex-col flex-shrink-0">
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
                <button
                  onClick={() => setBottomTab("history")}
                  className={`px-3 py-1.5 text-[11px] font-medium border-b-2 ${
                    bottomTab === "history"
                      ? "border-indigo-500 text-indigo-600"
                      : "border-transparent text-gray-400 hover:text-gray-600"
                  }`}
                >
                  History
                </button>
              </div>
              <div className="flex-1 overflow-hidden">
                {bottomTab === "logs" && <LogPanel />}
                {bottomTab === "output" && <OutputPreview />}
                {bottomTab === "history" && <RunHistoryPanel />}
              </div>
            </div>
          </div>

          {/* Right — config panel */}
          <ConfigPanel />

          {/* Right — chat panel (overlays/stacks alongside config) */}
          <ChatPanel />
        </div>

        <ToastContainer />
        <CommandPalette />
        <HumanInputDialog />
      </div>
    </ReactFlowProvider>
  );
}
