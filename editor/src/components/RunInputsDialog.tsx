import { useState, useEffect, useRef, useMemo } from "react";
import { useGraphStore } from "../store/useGraphStore";
import { detectRunInputVariables } from "../lib/runInputs";

interface RunInputsDialogProps {
  open: boolean;
  onClose: () => void;
}

export default function RunInputsDialog({ open, onClose }: RunInputsDialogProps) {
  const danGraph = useGraphStore((s) => s.danGraph);
  const startRun = useGraphStore((s) => s.startRun);

  const variables = useMemo(
    () => (danGraph ? detectRunInputVariables(danGraph) : []),
    [danGraph],
  );

  const [values, setValues] = useState<Record<string, string>>({});
  const firstInputRef = useRef<HTMLTextAreaElement>(null);

  // Reset form values when dialog opens or variables change
  useEffect(() => {
    if (open) {
      setValues(Object.fromEntries(variables.map((v) => [v, ""])));
      setTimeout(() => firstInputRef.current?.focus(), 50);
    }
  }, [open, variables]);

  // If no inputs needed, run immediately when dialog opens
  useEffect(() => {
    if (open && variables.length === 0 && danGraph) {
      startRun();
      onClose();
    }
  }, [open, variables, danGraph, startRun, onClose]);

  if (!open || variables.length === 0) return null;

  const handleSubmit = () => {
    const inputs: Record<string, string> = {};
    for (const [k, v] of Object.entries(values)) {
      if (v.trim()) inputs[k] = v.trim();
    }
    startRun(Object.keys(inputs).length > 0 ? inputs : undefined);
    onClose();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") onClose();
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      onKeyDown={handleKeyDown}
    >
      <div className="bg-white rounded-lg shadow-xl w-[440px] max-h-[80vh] flex flex-col border border-gray-200">
        <div className="px-5 py-4 border-b border-gray-100">
          <h3 className="text-sm font-semibold text-gray-800">Run Inputs</h3>
          <p className="text-xs text-gray-400 mt-0.5">
            Provide values for the workflow's input variables
          </p>
        </div>

        <div className="px-5 py-4 space-y-3 overflow-y-auto flex-1">
          {variables.map((v, i) => (
            <div key={v}>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                {v}
              </label>
              <textarea
                ref={i === 0 ? firstInputRef : undefined}
                value={values[v] ?? ""}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, [v]: e.target.value }))
                }
                placeholder={`Enter ${v}…`}
                rows={2}
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-md focus:outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-200 resize-y transition-all"
              />
            </div>
          ))}
        </div>

        <div className="px-5 py-3 border-t border-gray-100 flex items-center justify-between">
          <span className="text-[10px] text-gray-400">
            {navigator.platform.includes("Mac") ? "⌘" : "Ctrl"}+Enter to run
          </span>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              className="px-4 py-1.5 text-xs rounded bg-indigo-600 text-white hover:bg-indigo-700 transition-colors"
            >
              Run
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
