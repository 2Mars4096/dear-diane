import { useGraphStore } from "../store/useGraphStore";

const STATUS_COLORS: Record<string, string> = {
  running: "bg-yellow-400",
  completed: "bg-green-500",
  failed: "bg-red-500",
};

export default function RunPanel() {
  const graphId = useGraphStore((s) => s.graphId);
  const runId = useGraphStore((s) => s.runId);
  const runStatus = useGraphStore((s) => s.runStatus);
  const startRun = useGraphStore((s) => s.startRun);
  const resumeRun = useGraphStore((s) => s.resumeRun);
  const disconnectRun = useGraphStore((s) => s.disconnectRun);
  const dirty = useGraphStore((s) => s.dirty);
  const saveGraph = useGraphStore((s) => s.saveGraph);

  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 border-b border-gray-200">
      {/* Save */}
      <button
        onClick={() => saveGraph()}
        disabled={!dirty}
        className="px-3 py-1 text-xs rounded border border-gray-300 bg-white hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Save{dirty ? " *" : ""}
      </button>

      <div className="w-px h-5 bg-gray-300" />

      {/* Run */}
      <button
        onClick={() => startRun()}
        disabled={!graphId || runStatus === "running"}
        className="px-3 py-1 text-xs rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Run
      </button>

      {/* Resume */}
      {runId && runStatus !== "running" && (
        <button
          onClick={() => resumeRun()}
          className="px-3 py-1 text-xs rounded border border-indigo-400 text-indigo-600 hover:bg-indigo-50"
        >
          Resume
        </button>
      )}

      {/* Disconnect */}
      {runStatus === "running" && (
        <button
          onClick={() => disconnectRun()}
          className="px-3 py-1 text-xs rounded border border-red-300 text-red-600 hover:bg-red-50"
        >
          Disconnect
        </button>
      )}

      {/* Status badge */}
      {runStatus && (
        <div className="flex items-center gap-1.5 ml-2 text-xs text-gray-500">
          <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[runStatus] ?? "bg-gray-400"}`} />
          <span>{runStatus}</span>
          {runId && <span className="text-gray-400">({runId})</span>}
        </div>
      )}
    </div>
  );
}
