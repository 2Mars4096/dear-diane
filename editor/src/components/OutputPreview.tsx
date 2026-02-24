import { useGraphStore } from "../store/useGraphStore";

export default function OutputPreview() {
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const nodeOutputs = useGraphStore((s) => s.nodeOutputs);
  const nodeStatuses = useGraphStore((s) => s.nodeStatuses);
  const streamingOutputs = useGraphStore((s) => s.streamingOutputs);

  if (!selectedNodeId) {
    return (
      <div className="p-3 text-xs text-gray-400 text-center">
        Select a node to see its output
      </div>
    );
  }

  const output = nodeOutputs[selectedNodeId];
  const status = nodeStatuses[selectedNodeId];
  const streamingText = streamingOutputs[selectedNodeId];

  return (
    <div className="p-3 overflow-y-auto">
      <h3 className="text-[11px] font-semibold text-gray-500 uppercase mb-1">
        Output &middot; {selectedNodeId}
      </h3>
      {status && (
        <div className="text-[11px] text-gray-400 mb-2">
          Status: {status.replace("node_", "")}
        </div>
      )}
      {streamingText && status === "node_started" && (
        <div className="mb-2">
          <div className="text-[10px] text-cyan-500 font-semibold uppercase mb-0.5">
            Streaming...
          </div>
          <pre className="text-[11px] bg-cyan-50 border border-cyan-200 rounded p-2 overflow-auto max-h-60 font-mono whitespace-pre-wrap">
            {streamingText}
            <span className="animate-pulse text-cyan-400">|</span>
          </pre>
        </div>
      )}
      {output ? (
        <pre className="text-[11px] bg-white border rounded p-2 overflow-auto max-h-60 font-mono whitespace-pre-wrap">
          {JSON.stringify(output, null, 2)}
        </pre>
      ) : (
        !streamingText && (
          <p className="text-[11px] text-gray-400">No output available</p>
        )
      )}
    </div>
  );
}
