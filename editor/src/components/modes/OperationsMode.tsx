/**
 * Operations mode: wraps the existing workflow editor (React Flow canvas,
 * node palette, config panel, log panels, toolbar) as a mode workspace.
 * This is the current App.tsx content extracted into a mode component.
 */
import { useEffect, useState, useCallback, useRef } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { useGraphStore } from "../../store/useGraphStore";
import EditorToolbar from "../EditorToolbar";
import NodePalette from "../NodePalette";
import GraphCanvas from "../GraphCanvas";
import ConfigPanel from "../ConfigPanel";
import LogPanel from "../LogPanel";
import OutputPreview from "../OutputPreview";
import RunHistoryPanel from "../RunHistoryPanel";
import TokenAnalyticsPanel from "../TokenAnalyticsPanel";
import BreadcrumbBar from "../BreadcrumbBar";
import PortMappingOverlay from "../PortMappingOverlay";
import ExecutionTimeline from "../ExecutionTimeline";
import HumanInputDialog from "../HumanInputDialog";
import BlockExportDialog from "../BlockExportDialog";
import CommandPalette from "../CommandPalette";
import ChatPanel from "../ChatPanel";
import { importBlock } from "../../lib/api";

type BottomTab = "logs" | "output" | "history" | "optimizations";

const MIN_PANEL_HEIGHT = 80;
const MAX_PANEL_HEIGHT = 600;
const DEFAULT_PANEL_HEIGHT = 176;

export default function OperationsMode() {
  const loadGraphList = useGraphStore((s) => s.loadGraphList);
  const restoreTabs = useGraphStore((s) => s.restoreTabs);
  const logFocusCounter = useGraphStore((s) => s.logFocusCounter);
  const historyFocusCounter = useGraphStore((s) => s.historyFocusCounter);
  const wasteFindings = useGraphStore((s) => s.wasteFindings);
  const addToast = useGraphStore((s) => s.addToast);
  const [bottomTab, setBottomTab] = useState<BottomTab>("logs");
  const [panelHeight, setPanelHeight] = useState(DEFAULT_PANEL_HEIGHT);
  const dragging = useRef(false);

  const [blockExportNodeId, setBlockExportNodeId] = useState<string | null>(null);
  const blockImportRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleExport = (e: Event) => {
      const detail = (e as CustomEvent).detail as { nodeId: string };
      setBlockExportNodeId(detail.nodeId);
    };
    const handleImport = () => {
      blockImportRef.current?.click();
    };
    window.addEventListener("dan:export-block", handleExport);
    window.addEventListener("dan:import-block", handleImport);
    return () => {
      window.removeEventListener("dan:export-block", handleExport);
      window.removeEventListener("dan:import-block", handleImport);
    };
  }, []);

  const handleBlockImport = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        await importBlock(file);
        addToast({ type: "success", message: `Imported block "${file.name}"` });
      } catch (err: unknown) {
        addToast({ type: "error", message: `Block import failed: ${(err as Error).message}` });
      } finally {
        if (blockImportRef.current) blockImportRef.current.value = "";
      }
    },
    [addToast],
  );

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
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
    },
    [panelHeight],
  );

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
      <div className="flex flex-col h-full">
        <EditorToolbar />

        <div className="flex flex-1 min-h-0">
          <NodePalette />

          <div className="flex flex-col flex-1 min-w-0">
            <BreadcrumbBar />
            <PortMappingOverlay />

            <div className="flex-1 min-h-0 relative">
              <GraphCanvas />
            </div>

            <div
              onMouseDown={onDragStart}
              className="h-1.5 border-t border-gray-200 cursor-row-resize hover:bg-indigo-100 active:bg-indigo-200 transition-colors flex-shrink-0"
            />
            <div style={{ height: panelHeight }} className="flex flex-col flex-shrink-0">
              <ExecutionTimeline />
              <div className="flex items-center gap-0 border-b border-gray-100 bg-gray-50 px-2">
                {(["logs", "output", "history", "optimizations"] as BottomTab[]).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setBottomTab(tab)}
                    className={`px-3 py-1.5 text-[11px] font-medium border-b-2 flex items-center gap-1 ${
                      bottomTab === tab
                        ? "border-indigo-500 text-indigo-600"
                        : "border-transparent text-gray-400 hover:text-gray-600"
                    }`}
                  >
                    {tab.charAt(0).toUpperCase() + tab.slice(1)}
                    {tab === "optimizations" && wasteFindings.length > 0 && (
                      <span className="bg-amber-100 text-amber-700 text-[9px] font-bold px-1 py-0.5 rounded-full leading-none">
                        {wasteFindings.length}
                      </span>
                    )}
                  </button>
                ))}
              </div>
              <div className="flex-1 overflow-hidden">
                {bottomTab === "logs" && <LogPanel />}
                {bottomTab === "output" && <OutputPreview />}
                {bottomTab === "history" && <RunHistoryPanel />}
                {bottomTab === "optimizations" && <TokenAnalyticsPanel />}
              </div>
            </div>
          </div>

          <ConfigPanel />

          <ChatPanel />
        </div>

        <CommandPalette />
        <HumanInputDialog />
        {blockExportNodeId && (
          <BlockExportDialog nodeId={blockExportNodeId} onClose={() => setBlockExportNodeId(null)} />
        )}
        <input
          ref={blockImportRef}
          type="file"
          accept=".danblock,.json,.zip"
          onChange={handleBlockImport}
          className="hidden"
        />
      </div>
    </ReactFlowProvider>
  );
}
