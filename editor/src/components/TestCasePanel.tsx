/**
 * TestCasePanel — displays and manages test cases for a selected node.
 * Renders inline in ConfigPanel as a collapsible section with a modal
 * for creating/editing test cases.
 */

import { useState, useEffect, useCallback } from "react";
import {
  listTestCases,
  createOrUpdateTestCase,
  deleteTestCase,
  runTestCase,
} from "../lib/api";
import type { NodeTestCase, TestCaseRunResult } from "../lib/api";
import type { InputPort } from "../types/graph";

// -- Modal for creating / editing a test case --------------------------------

function TestCaseModal({
  workflowId,
  nodeId,
  inputPorts,
  existing,
  onSave,
  onClose,
}: {
  workflowId: string;
  nodeId: string;
  inputPorts: InputPort[];
  existing: NodeTestCase | null;
  onSave: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [inputs, setInputs] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const port of inputPorts) {
      const val = existing?.inputs?.[port.name];
      init[port.name] = val !== undefined ? JSON.stringify(val, null, 2) : "";
    }
    return init;
  });
  const [expectedOutputs, setExpectedOutputs] = useState(
    existing?.expected_outputs ? JSON.stringify(existing.expected_outputs, null, 2) : "",
  );
  const [tags, setTags] = useState(existing?.tags?.join(", ") ?? "");
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      // Parse each input port value
      const parsedInputs: Record<string, unknown> = {};
      for (const [portName, raw] of Object.entries(inputs)) {
        if (!raw.trim()) continue;
        try {
          parsedInputs[portName] = JSON.parse(raw);
        } catch {
          parsedInputs[portName] = raw; // treat as raw string
        }
      }

      let parsedExpected: Record<string, unknown> | null = null;
      if (expectedOutputs.trim()) {
        try {
          parsedExpected = JSON.parse(expectedOutputs);
        } catch {
          setError("Expected outputs must be valid JSON");
          setSaving(false);
          return;
        }
      }

      const body: Partial<NodeTestCase> = {
        ...(existing?.id ? { id: existing.id } : {}),
        name: name || "Untitled Test",
        node_id: nodeId,
        inputs: parsedInputs,
        expected_outputs: parsedExpected,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        notes,
      };

      await createOrUpdateTestCase(workflowId, nodeId, body);
      onSave();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-white rounded-lg shadow-xl w-[480px] max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <h3 className="text-sm font-semibold text-gray-700">
            {existing ? "Edit Test Case" : "New Test Case"}
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg">
            x
          </button>
        </div>

        <div className="overflow-y-auto px-4 py-3 flex flex-col gap-3">
          {/* Name */}
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">Name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Happy path, Edge case..."
              className="border rounded px-2 py-1 text-xs"
            />
          </label>

          {/* Input port values */}
          <div>
            <span className="text-[11px] font-semibold text-gray-400 uppercase">
              Inputs
            </span>
            <div className="flex flex-col gap-2 mt-1">
              {inputPorts.map((port) => (
                <label key={port.name} className="flex flex-col gap-0.5">
                  <span className="text-[11px] font-medium text-gray-500">
                    {port.name}
                    {port.required && (
                      <span className="text-red-400 ml-0.5">*</span>
                    )}
                  </span>
                  <textarea
                    value={inputs[port.name] ?? ""}
                    onChange={(e) =>
                      setInputs((prev) => ({
                        ...prev,
                        [port.name]: e.target.value,
                      }))
                    }
                    placeholder="JSON value or raw string"
                    className="border rounded px-2 py-1 text-xs font-mono min-h-[56px] resize-y"
                  />
                </label>
              ))}
              {inputPorts.length === 0 && (
                <p className="text-[11px] text-gray-400 italic">
                  No input ports defined
                </p>
              )}
            </div>
          </div>

          {/* Expected outputs */}
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">
              Expected Outputs (optional JSON)
            </span>
            <textarea
              value={expectedOutputs}
              onChange={(e) => setExpectedOutputs(e.target.value)}
              placeholder='{"output": "expected value"}'
              className="border rounded px-2 py-1 text-xs font-mono min-h-[56px] resize-y"
            />
          </label>

          {/* Tags */}
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">
              Tags (comma-separated)
            </span>
            <input
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="e.g. smoke, regression"
              className="border rounded px-2 py-1 text-xs"
            />
          </label>

          {/* Notes */}
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">Notes</span>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Additional context..."
              className="border rounded px-2 py-1 text-xs min-h-[40px] resize-y"
            />
          </label>

          {error && (
            <p className="text-xs text-red-500 mt-1">{error}</p>
          )}
        </div>

        <div className="flex justify-end gap-2 px-4 py-3 border-t">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-gray-600 hover:text-gray-800"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-3 py-1.5 text-xs bg-indigo-500 text-white rounded hover:bg-indigo-600 disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

// -- Individual test case row ------------------------------------------------

function TestCaseRow({
  tc,
  workflowId,
  nodeId,
  onRefresh,
  onEdit,
}: {
  tc: NodeTestCase;
  workflowId: string;
  nodeId: string;
  onRefresh: () => void;
  onEdit: (tc: NodeTestCase) => void;
}) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<TestCaseRunResult | null>(null);
  const [deleting, setDeleting] = useState(false);

  const handleRun = async () => {
    setRunning(true);
    setResult(null);
    try {
      const res = await runTestCase(workflowId, nodeId, tc.id);
      setResult(res);
    } catch (e) {
      setResult({
        passed: false,
        actual_outputs: {},
        expected_outputs: null,
        diff: null,
        execution_metadata: {},
        error: String(e),
      });
    } finally {
      setRunning(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete test case "${tc.name}"?`)) return;
    setDeleting(true);
    try {
      await deleteTestCase(workflowId, nodeId, tc.id);
      onRefresh();
    } catch {
      // Ignore error for now, could show a toast
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="border rounded p-2 bg-white">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {result && (
            <span
              className={`w-2 h-2 rounded-full ${
                result.passed ? "bg-green-500" : "bg-red-500"
              }`}
            />
          )}
          <span className="text-xs font-medium text-gray-700 truncate max-w-[120px]">
            {tc.name || "Untitled"}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleRun}
            disabled={running}
            className="px-1.5 py-0.5 text-[10px] bg-green-50 text-green-700 rounded hover:bg-green-100 disabled:opacity-50"
            title="Run test case"
          >
            {running ? "..." : "Run"}
          </button>
          <button
            onClick={() => onEdit(tc)}
            className="px-1.5 py-0.5 text-[10px] bg-gray-50 text-gray-600 rounded hover:bg-gray-100"
            title="Edit"
          >
            Edit
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="px-1.5 py-0.5 text-[10px] text-red-500 hover:text-red-700"
            title="Delete"
          >
            Del
          </button>
        </div>
      </div>
      {tc.tags.length > 0 && (
        <div className="flex gap-1 mt-1">
          {tc.tags.map((tag) => (
            <span
              key={tag}
              className="text-[9px] px-1 py-0 bg-gray-100 text-gray-500 rounded"
            >
              {tag}
            </span>
          ))}
        </div>
      )}
      {result && (
        <div className="mt-1.5 text-[10px]">
          <div
            className={`font-medium ${
              result.passed ? "text-green-600" : "text-red-600"
            }`}
          >
            {result.passed ? "PASSED" : "FAILED"}
          </div>
          {result.error && (
            <div className="text-red-500 mt-0.5 break-all">{result.error}</div>
          )}
          {result.diff && Object.keys(result.diff).length > 0 && (
            <div className="mt-0.5">
              <span className="text-gray-500">Diff:</span>
              <pre className="bg-gray-50 rounded p-1 mt-0.5 overflow-x-auto max-h-20 text-[9px]">
                {JSON.stringify(result.diff, null, 2)}
              </pre>
            </div>
          )}
          {!result.error && (
            <details className="mt-0.5">
              <summary className="text-gray-400 cursor-pointer">
                Outputs
              </summary>
              <pre className="bg-gray-50 rounded p-1 mt-0.5 overflow-x-auto max-h-24 text-[9px]">
                {JSON.stringify(result.actual_outputs, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

// -- Main section component --------------------------------------------------

export default function TestCaseSection({
  workflowId,
  nodeId,
  inputPorts,
}: {
  workflowId: string;
  nodeId: string;
  inputPorts: InputPort[];
}) {
  const [open, setOpen] = useState(false);
  const [cases, setCases] = useState<NodeTestCase[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingCase, setEditingCase] = useState<NodeTestCase | null>(null);

  const refresh = useCallback(async () => {
    if (!workflowId || !nodeId) return;
    setLoading(true);
    try {
      const res = await listTestCases(workflowId, nodeId);
      setCases(res.cases);
    } catch {
      setCases([]);
    } finally {
      setLoading(false);
    }
  }, [workflowId, nodeId]);

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  // Listen for context menu "Add Test Case" action
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.nodeId === nodeId) {
        setOpen(true);
        setEditingCase(null);
        setModalOpen(true);
        refresh();
      }
    };
    window.addEventListener("dan:open-test-case-modal", handler);
    return () => window.removeEventListener("dan:open-test-case-modal", handler);
  }, [nodeId, refresh]);

  const handleEdit = (tc: NodeTestCase) => {
    setEditingCase(tc);
    setModalOpen(true);
  };

  const handleAdd = () => {
    setEditingCase(null);
    setModalOpen(true);
  };

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen(!open)}
        className="text-[11px] font-semibold text-gray-400 uppercase flex items-center gap-1 hover:text-gray-600"
      >
        {open ? "\u25BE" : "\u25B8"} Test Cases
        {cases.length > 0 && (
          <span className="text-[9px] bg-gray-200 text-gray-600 rounded-full px-1.5">
            {cases.length}
          </span>
        )}
      </button>

      {open && (
        <div className="mt-1.5 pl-2 border-l border-gray-200">
          {loading ? (
            <p className="text-[10px] text-gray-400">Loading...</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {cases.map((tc) => (
                <TestCaseRow
                  key={tc.id}
                  tc={tc}
                  workflowId={workflowId}
                  nodeId={nodeId}
                  onRefresh={refresh}
                  onEdit={handleEdit}
                />
              ))}
              {cases.length === 0 && (
                <p className="text-[10px] text-gray-400 italic">
                  No test cases yet
                </p>
              )}
            </div>
          )}
          <button
            onClick={handleAdd}
            className="text-xs text-blue-500 hover:text-blue-700 mt-1.5"
          >
            + Add Test Case
          </button>
        </div>
      )}

      {modalOpen && (
        <TestCaseModal
          workflowId={workflowId}
          nodeId={nodeId}
          inputPorts={inputPorts}
          existing={editingCase}
          onSave={refresh}
          onClose={() => {
            setModalOpen(false);
            setEditingCase(null);
          }}
        />
      )}
    </div>
  );
}
