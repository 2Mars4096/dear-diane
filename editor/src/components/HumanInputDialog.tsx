import { useState } from "react";
import { useGraphStore } from "../store/useGraphStore";
import * as api from "../lib/api";

export default function HumanInputDialog() {
  const pendingHumanInput = useGraphStore((s) => s.pendingHumanInput);
  const runId = useGraphStore((s) => s.runId);
  const [response, setResponse] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!pendingHumanInput || !runId) return null;

  const handleSubmit = async () => {
    if (!response.trim()) return;
    setSubmitting(true);
    try {
      await api.submitHumanInput(
        runId,
        pendingHumanInput.requestId,
        pendingHumanInput.nodeId,
        response,
      );
      useGraphStore.setState({ pendingHumanInput: null });
      setResponse("");
    } catch (err: unknown) {
      useGraphStore.getState().addToast({
        type: "error",
        message: (err as Error).message ?? "Failed to submit input",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = () => {
    useGraphStore.setState({ pendingHumanInput: null });
    setResponse("");
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-white rounded-lg shadow-xl w-[480px] max-w-[90vw] overflow-hidden">
        <div className="px-4 py-3 bg-cyan-50 border-b border-cyan-200">
          <div className="flex items-center gap-2">
            <span className="text-cyan-600 text-lg">&#x270B;</span>
            <h3 className="text-sm font-semibold text-cyan-800">
              Human Input Required
            </h3>
          </div>
          <p className="text-[11px] text-cyan-600 mt-0.5">
            Node: {pendingHumanInput.nodeId}
          </p>
        </div>
        <div className="p-4">
          <p className="text-sm text-gray-700 mb-3 whitespace-pre-wrap">
            {pendingHumanInput.prompt}
          </p>
          <textarea
            value={response}
            onChange={(e) => setResponse(e.target.value)}
            placeholder="Type your response..."
            rows={4}
            className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-transparent resize-y"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                handleSubmit();
              }
            }}
          />
        </div>
        <div className="px-4 py-3 bg-gray-50 border-t flex justify-end gap-2">
          <button
            onClick={handleCancel}
            className="px-3 py-1.5 text-xs text-gray-600 bg-white border border-gray-300 rounded hover:bg-gray-100 transition-colors"
          >
            Dismiss
          </button>
          <button
            onClick={handleSubmit}
            disabled={!response.trim() || submitting}
            className="px-3 py-1.5 text-xs text-white bg-cyan-600 rounded hover:bg-cyan-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? "Submitting..." : "Submit (Cmd+Enter)"}
          </button>
        </div>
      </div>
    </div>
  );
}
