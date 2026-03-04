import { useState, useRef, useCallback, useEffect } from "react";
import { useGraphStore } from "../store/useGraphStore";
import Spinner from "./Spinner";
import RunInputsDialog from "./RunInputsDialog";
import TabBar from "./TabBar";
import * as api from "../lib/api";

// ---------------------------------------------------------------------------
// ExportPreviewModal — shows markdown file list or Python code with actions
// ---------------------------------------------------------------------------

interface ExportPreviewModalProps {
  mode: "markdown" | "python";
  files?: Array<{ path: string; content: string }>;
  diagnostics?: Array<{ level: string; message: string; hint?: string }>;
  code?: string;
  onClose: () => void;
}

function ExportPreviewModal({ mode, files, diagnostics, code, onClose }: ExportPreviewModalProps) {
  const [selectedFile, setSelectedFile] = useState(0);
  const addToast = useGraphStore((s) => s.addToast);

  const handleCopy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).catch(() => {});
    addToast({ type: "info", message: "Copied to clipboard" });
  }, [addToast]);

  const handleDownloadFile = useCallback((filename: string, content: string) => {
    const mimeType = filename.endsWith(".md") ? "text/markdown" : filename.endsWith(".py") ? "text/x-python" : "text/plain";
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  const handleDownloadAll = useCallback(() => {
    if (!files) return;
    for (const f of files) {
      handleDownloadFile(f.path.split("/").pop() ?? f.path, f.content);
    }
  }, [files, handleDownloadFile]);

  const activeFile = files?.[selectedFile];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="bg-white rounded-xl shadow-2xl max-w-3xl w-full max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between shrink-0">
          <span className="text-sm font-semibold text-gray-800">
            Export as {mode === "markdown" ? "Markdown" : "Python"}
          </span>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        {/* Diagnostics banner */}
        {diagnostics && diagnostics.length > 0 && (
          <div className="px-5 py-2 bg-amber-50 border-b border-amber-100 flex flex-wrap gap-2">
            {diagnostics.map((d, i) => (
              <span key={i} className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 9v4m0 4h.01M12 2L1 21h22L12 2z"/></svg>
                {d.message}
              </span>
            ))}
          </div>
        )}

        {/* Body */}
        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* Markdown: file sidebar */}
          {mode === "markdown" && files && files.length > 1 && (
            <div className="w-44 border-r border-gray-100 overflow-y-auto shrink-0 bg-gray-50/50">
              {files.map((f, i) => (
                <button
                  key={f.path}
                  onClick={() => setSelectedFile(i)}
                  className={`block w-full text-left px-3 py-2 text-[11px] font-mono truncate transition-colors ${
                    i === selectedFile
                      ? "bg-indigo-50 text-indigo-700 font-semibold"
                      : "text-gray-600 hover:bg-gray-100"
                  }`}
                  title={f.path}
                >
                  {f.path.split("/").pop() ?? f.path}
                </button>
              ))}
            </div>
          )}

          {/* Content pane */}
          <div className="flex-1 overflow-auto p-4">
            <pre className="text-[11px] text-gray-700 font-mono whitespace-pre-wrap break-words leading-relaxed">
              {mode === "python" ? code : activeFile?.content ?? ""}
            </pre>
          </div>
        </div>

        {/* Footer actions */}
        <div className="px-5 py-3 border-t border-gray-100 flex items-center gap-2 shrink-0">
          <button
            onClick={() => handleCopy(mode === "python" ? (code ?? "") : (activeFile?.content ?? ""))}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            Copy
          </button>
          {mode === "markdown" && files && (
            <button
              onClick={handleDownloadAll}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg transition-colors"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Download All
            </button>
          )}
          {mode === "python" && (
            <button
              onClick={() => handleDownloadFile("workflow.py", code ?? "")}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg transition-colors"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Download
            </button>
          )}
          <div className="flex-1" />
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-700 transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

const RefreshIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 2v6h-6" /><path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
    <path d="M3 22v-6h6" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
  </svg>
);

const STATUS_COLORS: Record<string, string> = {
  running: "bg-yellow-400",
  completed: "bg-green-500",
  failed: "bg-red-500",
};

export default function EditorToolbar() {
  const graphId = useGraphStore((s) => s.graphId);
  const dirty = useGraphStore((s) => s.dirty);
  const runId = useGraphStore((s) => s.runId);
  const runStatus = useGraphStore((s) => s.runStatus);
  const savingGraph = useGraphStore((s) => s.savingGraph);
  const loadingGraph = useGraphStore((s) => s.loadingGraph);
  const createGraph = useGraphStore((s) => s.createGraph);
  const deleteGraph = useGraphStore((s) => s.deleteGraph);
  const saveGraph = useGraphStore((s) => s.saveGraph);
  const resumeRun = useGraphStore((s) => s.resumeRun);
  const disconnectRun = useGraphStore((s) => s.disconnectRun);
  const applyAutoLayout = useGraphStore((s) => s.applyAutoLayout);
  const danGraph = useGraphStore((s) => s.danGraph);
  const addToast = useGraphStore((s) => s.addToast);
  const loadGraphList = useGraphStore((s) => s.loadGraphList);
  const openTab = useGraphStore((s) => s.openTab);
  const refreshTab = useGraphStore((s) => s.refreshTab);
  // 18-4: Heatmap toggle
  const tokenHeatmapEnabled = useGraphStore((s) => s.tokenHeatmapEnabled);
  const setTokenHeatmapEnabled = useGraphStore((s) => s.setTokenHeatmapEnabled);

  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [showRunInputs, setShowRunInputs] = useState(false);
  const [showExportDropdown, setShowExportDropdown] = useState(false);
  const [exportModal, setExportModal] = useState<{
    mode: "markdown" | "python";
    files?: Array<{ path: string; content: string }>;
    diagnostics?: Array<{ level: string; message: string; hint?: string }>;
    code?: string;
  } | null>(null);
  const importInputRef = useRef<HTMLInputElement>(null);
  const exportDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showExportDropdown) return;
    const handler = (e: MouseEvent) => {
      if (exportDropdownRef.current && !exportDropdownRef.current.contains(e.target as Node)) {
        setShowExportDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showExportDropdown]);

  const handleCreate = () => {
    const name = newName.trim();
    if (!name) return;
    createGraph(name);
    setNewName("");
    setShowNew(false);
  };

  const handleExport = () => {
    if (!danGraph) return;
    const json = JSON.stringify(danGraph, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${danGraph.metadata?.name || graphId || "graph"}.json`;
    a.click();
    URL.revokeObjectURL(url);
    addToast({ type: "success", message: "Graph exported" });
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      if (!parsed.version || !parsed.version.startsWith("dan_graph")) {
        addToast({ type: "error", message: "Invalid graph JSON: missing format version" });
        return;
      }
      const importId = `import_${Date.now()}`;
      await api.createGraph(importId, parsed as Record<string, unknown>);
      await loadGraphList();
      await openTab(importId);
      addToast({ type: "success", message: `Imported "${parsed.metadata?.name || importId}"` });
    } catch (err: unknown) {
      addToast({ type: "error", message: `Import failed: ${(err as Error).message}` });
    } finally {
      if (importInputRef.current) importInputRef.current.value = "";
    }
  };

  return (
    <div className="flex flex-col bg-white border-b border-gray-200 shrink-0">
      {/* Tab bar row */}
      <TabBar />

      {/* Toolbar row */}
      <div className="flex items-center gap-2 px-3 py-1.5">
      {/* Left: branding + graph controls */}
      <span className="text-xs font-extrabold tracking-widest text-indigo-600 select-none">
        DAN
      </span>
      <div className="w-px h-4 bg-gray-300" />

      {loadingGraph && <Spinner size="sm" />}

      {graphId && (
        <button
          onClick={() => {
            if (confirm(`Delete "${graphId}"?`)) deleteGraph(graphId);
          }}
          className="text-[10px] text-red-400 hover:text-red-600"
          title="Delete graph"
        >
          ×
        </button>
      )}

      {showNew ? (
        <div className="flex items-center gap-1">
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            placeholder="graph name"
            className="text-xs border rounded px-2 py-1 w-28"
          />
          <button onClick={handleCreate} className="text-xs text-indigo-600 hover:underline">
            Create
          </button>
          <button onClick={() => setShowNew(false)} className="text-xs text-gray-400 hover:underline">
            Cancel
          </button>
        </div>
      ) : (
        <button onClick={() => setShowNew(true)} className="text-xs text-indigo-600 hover:underline">
          + Create
        </button>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Center-right: Refresh (list + tab), Save, Run, Resume, Disconnect */}
      <button
        onClick={async () => {
          await loadGraphList();
          if (graphId) await refreshTab();
          addToast({ type: "success", message: "Refreshed (list + tab)" });
        }}
        className="px-2 py-1 text-[11px] rounded border border-gray-300 text-gray-500 hover:bg-gray-100"
        title="Reload graph list and current tab (picks up newly built graphs, no restart needed)"
      >
        <RefreshIcon />
      </button>

      <button
        onClick={() => saveGraph()}
        disabled={!dirty && !savingGraph}
        className="flex items-center gap-1 px-3 py-1 text-xs rounded border border-gray-300 bg-white hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
        title="⌘S to save"
      >
        {savingGraph ? <Spinner size="sm" /> : null}
        Save{dirty ? " •" : ""}
      </button>

      <button
        onClick={() => setShowRunInputs(true)}
        disabled={!graphId || runStatus === "running"}
        className="px-3 py-1 text-xs rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Run
      </button>

      <RunInputsDialog
        open={showRunInputs}
        onClose={() => setShowRunInputs(false)}
      />

      {runId && runStatus !== "running" && (
        <button
          onClick={() => resumeRun()}
          className="px-3 py-1 text-xs rounded border border-indigo-400 text-indigo-600 hover:bg-indigo-50"
        >
          Resume
        </button>
      )}

      {runStatus === "running" && (
        <button
          onClick={() => disconnectRun()}
          className="px-3 py-1 text-xs rounded border border-red-300 text-red-600 hover:bg-red-50"
        >
          Disconnect
        </button>
      )}

      <div className="w-px h-4 bg-gray-200" />

      <button
        onClick={applyAutoLayout}
        disabled={!graphId}
        className="px-2 py-1 text-[11px] rounded border border-gray-300 text-gray-500 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
        title="Auto-layout nodes (dagre)"
      >
        Layout
      </button>

      {/* 18-4: Token heatmap toggle */}
      <button
        onClick={() => setTokenHeatmapEnabled(!tokenHeatmapEnabled)}
        className={`px-2 py-1 text-[11px] rounded border ${
          tokenHeatmapEnabled
            ? "border-amber-400 bg-amber-50 text-amber-700"
            : "border-gray-300 text-gray-500 hover:bg-gray-100"
        }`}
        title={tokenHeatmapEnabled ? "Hide token heatmap" : "Show token heatmap on nodes (color by token usage)"}
      >
        Heatmap
      </button>

      <div className="relative" ref={exportDropdownRef}>
        <button
          onClick={() => setShowExportDropdown(!showExportDropdown)}
          disabled={!danGraph}
          className="flex items-center gap-1 px-2 py-1 text-[11px] rounded border border-gray-300 text-gray-500 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
          title="Export graph"
        >
          Export
          <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        {showExportDropdown && (
          <div className="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-50 w-44 py-1">
            <button
              onClick={() => { handleExport(); setShowExportDropdown(false); }}
              className="block w-full text-left px-3 py-1.5 text-[11px] text-gray-700 hover:bg-gray-50 transition-colors"
            >
              Export as JSON
            </button>
            <button
              onClick={async () => {
                setShowExportDropdown(false);
                if (!graphId) return;
                try {
                  const result = await api.exportGraphMarkdown(graphId);
                  setExportModal({ mode: "markdown", files: result.files, diagnostics: result.diagnostics });
                } catch (err: unknown) {
                  addToast({ type: "error", message: `Export failed: ${(err as Error).message}` });
                }
              }}
              className="block w-full text-left px-3 py-1.5 text-[11px] text-gray-700 hover:bg-gray-50 transition-colors"
            >
              Export as Markdown
            </button>
            <button
              onClick={async () => {
                setShowExportDropdown(false);
                if (!graphId) return;
                try {
                  const result = await api.exportGraphPython(graphId);
                  setExportModal({ mode: "python", code: result.code });
                } catch (err: unknown) {
                  addToast({ type: "error", message: `Export failed: ${(err as Error).message}` });
                }
              }}
              className="block w-full text-left px-3 py-1.5 text-[11px] text-gray-700 hover:bg-gray-50 transition-colors"
            >
              Export as Python
            </button>
          </div>
        )}
      </div>

      {exportModal && (
        <ExportPreviewModal
          mode={exportModal.mode}
          files={exportModal.files}
          diagnostics={exportModal.diagnostics}
          code={exportModal.code}
          onClose={() => setExportModal(null)}
        />
      )}

      <button
        onClick={() => importInputRef.current?.click()}
        className="px-2 py-1 text-[11px] rounded border border-gray-300 text-gray-500 hover:bg-gray-100"
        title="Import graph from JSON"
      >
        Import
      </button>
      <input
        ref={importInputRef}
        type="file"
        accept=".json"
        onChange={handleImport}
        className="hidden"
      />

      {/* Far right: status badge */}
      {runStatus && (
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[runStatus] ?? "bg-gray-400"}`} />
          <span>{runStatus}</span>
          {runId && (
            <span className="text-gray-400 text-[10px]">({runId.slice(0, 8)})</span>
          )}
        </div>
      )}
      </div>{/* end toolbar row */}
    </div>
  );
}
