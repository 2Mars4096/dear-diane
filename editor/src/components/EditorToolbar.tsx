import { useState, useRef } from "react";
import { useGraphStore } from "../store/useGraphStore";
import Spinner from "./Spinner";
import RunInputsDialog from "./RunInputsDialog";
import TabBar from "./TabBar";
import * as api from "../lib/api";

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

  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [showRunInputs, setShowRunInputs] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);

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

      {/* Center-right: Refresh, Save, Run, Resume, Disconnect */}
      <button
        onClick={() => refreshTab()}
        disabled={!graphId}
        className="px-2 py-1 text-[11px] rounded border border-gray-300 text-gray-500 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
        title="Refresh current tab (reload graph from server)"
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

      <button
        onClick={handleExport}
        disabled={!danGraph}
        className="px-2 py-1 text-[11px] rounded border border-gray-300 text-gray-500 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
        title="Export graph as JSON"
      >
        Export
      </button>

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
