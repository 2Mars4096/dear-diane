import { useState } from "react";
import { useGraphStore } from "../store/useGraphStore";
import * as api from "../lib/api";
import ConfirmDialog from "./shell/ConfirmDialog";

export default function GraphSwitcher() {
  const graphId = useGraphStore((s) => s.graphId);
  const graphList = useGraphStore((s) => s.graphList);
  const loadGraph = useGraphStore((s) => s.loadGraph);
  const createGraph = useGraphStore((s) => s.createGraph);
  const deleteGraph = useGraphStore((s) => s.deleteGraph);
  const saveGraphAs = useGraphStore((s) => s.saveGraphAs);
  const addToast = useGraphStore((s) => s.addToast);
  const loadGraphList = useGraphStore((s) => s.loadGraphList);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [showSaveAs, setShowSaveAs] = useState(false);
  const [saveAsName, setSaveAsName] = useState("");

  const handleCreate = () => {
    const name = newName.trim();
    if (!name) return;
    createGraph(name);
    setNewName("");
    setShowNew(false);
  };

  const handleSaveAs = async () => {
    const name = saveAsName.trim();
    if (!name) return;
    await saveGraphAs(name);
    setSaveAsName("");
    setShowSaveAs(false);
  };

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-white border-b border-gray-200">
      <span className="text-xs font-bold text-gray-700 tracking-wide">DAN</span>
      <div className="w-px h-4 bg-gray-300" />

      <select
        value={graphId ?? ""}
        onChange={(e) => e.target.value && loadGraph(e.target.value)}
        className="text-xs border rounded px-2 py-1 bg-white min-w-[140px]"
      >
        <option value="" disabled>Select graph...</option>
        {graphList.map((g) => (
          <option key={g.graph_id} value={g.graph_id}>
            {g.name || g.graph_id}
          </option>
        ))}
      </select>

      {graphId && (
        <button
          onClick={() => setConfirmDelete(graphId)}
          className="text-[10px] text-red-400 hover:text-red-600"
          title="Delete graph"
        >
          &times;
        </button>
      )}

      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete graph"
        message={`Are you sure you want to delete "${confirmDelete}"?`}
        confirmLabel="Delete"
        confirmVariant="danger"
        onConfirm={async () => {
          const gid = confirmDelete;
          setConfirmDelete(null);
          if (!gid) return;
          let snapshot: Record<string, unknown> | null = null;
          try {
            const resp = await api.getGraph(gid);
            snapshot = resp.data;
          } catch { /* proceed without undo capability */ }
          await deleteGraph(gid);
          if (snapshot) {
            addToast({
              type: "info",
              message: `Deleted "${gid}"`,
              durationMs: 8000,
              action: {
                label: "Undo",
                onClick: async () => {
                  try {
                    await api.createGraph(gid, snapshot!);
                    await loadGraphList();
                    addToast({ type: "success", message: `Restored "${gid}"` });
                  } catch {
                    addToast({ type: "error", message: "Failed to restore graph" });
                  }
                },
              },
            });
          }
        }}
        onCancel={() => setConfirmDelete(null)}
      />

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
        <button
          onClick={() => setShowNew(true)}
          className="text-xs text-indigo-600 hover:underline"
        >
          + New
        </button>
      )}

      {graphId ? (
        showSaveAs ? (
          <div className="flex items-center gap-1">
            <input
              autoFocus
              value={saveAsName}
              onChange={(e) => setSaveAsName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void handleSaveAs()}
              placeholder="workflow name"
              className="text-xs border rounded px-2 py-1 w-36"
            />
            <button onClick={() => void handleSaveAs()} className="text-xs text-indigo-600 hover:underline">
              Save
            </button>
            <button
              onClick={() => {
                setShowSaveAs(false);
                setSaveAsName("");
              }}
              className="text-xs text-gray-400 hover:underline"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => {
              const current = graphList.find((g) => g.graph_id === graphId);
              setSaveAsName(current?.name || graphId);
              setShowSaveAs(true);
            }}
            className="text-xs text-indigo-600 hover:underline"
          >
            Save As
          </button>
        )
      ) : null}
    </div>
  );
}
