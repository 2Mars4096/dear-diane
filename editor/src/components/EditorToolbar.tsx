import { useState } from "react";
import { useGraphStore } from "../store/useGraphStore";
import Spinner from "./Spinner";
import RunInputsDialog from "./RunInputsDialog";

const STATUS_COLORS: Record<string, string> = {
  running: "bg-yellow-400",
  completed: "bg-green-500",
  failed: "bg-red-500",
};

export default function EditorToolbar() {
  const graphId = useGraphStore((s) => s.graphId);
  const graphList = useGraphStore((s) => s.graphList);
  const dirty = useGraphStore((s) => s.dirty);
  const runId = useGraphStore((s) => s.runId);
  const runStatus = useGraphStore((s) => s.runStatus);
  const savingGraph = useGraphStore((s) => s.savingGraph);
  const loadingGraph = useGraphStore((s) => s.loadingGraph);
  const loadGraph = useGraphStore((s) => s.loadGraph);
  const createGraph = useGraphStore((s) => s.createGraph);
  const deleteGraph = useGraphStore((s) => s.deleteGraph);
  const saveGraph = useGraphStore((s) => s.saveGraph);
  const resumeRun = useGraphStore((s) => s.resumeRun);
  const disconnectRun = useGraphStore((s) => s.disconnectRun);
  const applyAutoLayout = useGraphStore((s) => s.applyAutoLayout);

  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [showRunInputs, setShowRunInputs] = useState(false);

  const handleCreate = () => {
    const name = newName.trim();
    if (!name) return;
    createGraph(name);
    setNewName("");
    setShowNew(false);
  };

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-white border-b border-gray-200 shrink-0">
      {/* Left: branding + graph selector */}
      <span className="text-xs font-extrabold tracking-widest text-indigo-600 select-none">
        DAN
      </span>
      <div className="w-px h-4 bg-gray-300" />

      <select
        value={graphId ?? ""}
        onChange={(e) => e.target.value && loadGraph(e.target.value)}
        disabled={loadingGraph}
        className="text-xs border rounded px-2 py-1 bg-white min-w-[140px]"
      >
        <option value="" disabled>
          {loadingGraph ? "Loading…" : "Select graph…"}
        </option>
        {graphList.map((g) => (
          <option key={g.graph_id} value={g.graph_id}>
            {g.name || g.graph_id}
          </option>
        ))}
      </select>
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
          + New
        </button>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Center-right: Save, Run, Resume, Disconnect */}
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
    </div>
  );
}
