import { useState } from "react";
import { useGraphStore } from "../store/useGraphStore";

export default function GraphSwitcher() {
  const graphId = useGraphStore((s) => s.graphId);
  const graphList = useGraphStore((s) => s.graphList);
  const loadGraph = useGraphStore((s) => s.loadGraph);
  const createGraph = useGraphStore((s) => s.createGraph);
  const deleteGraph = useGraphStore((s) => s.deleteGraph);
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");

  const handleCreate = () => {
    const name = newName.trim();
    if (!name) return;
    createGraph(name);
    setNewName("");
    setShowNew(false);
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
          onClick={() => { if (confirm(`Delete "${graphId}"?`)) deleteGraph(graphId); }}
          className="text-[10px] text-red-400 hover:text-red-600"
          title="Delete graph"
        >
          &times;
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
        <button
          onClick={() => setShowNew(true)}
          className="text-xs text-indigo-600 hover:underline"
        >
          + New
        </button>
      )}
    </div>
  );
}
