import { useGraphStore } from "../store/useGraphStore";
import type { DanNode } from "../types/graph";

const SKIP_FIELDS = new Set([
  "id", "node_type", "input_ports", "output_ports", "position", "ui", "metadata",
  "read_set", "write_set", "compaction_rule", "failure_policy", "projections",
  "local_state", "control_state_schema", "external_input_schema", "external_output_schema",
]);

const LARGE_TEXT_FIELDS = new Set(["prompt_template", "code", "system_prompt"]);

export default function ConfigPanel() {
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const selectedEdgeId = useGraphStore((s) => s.selectedEdgeId);
  const nodes = useGraphStore((s) => s.nodes);
  const edges = useGraphStore((s) => s.edges);
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const updateEdgeData = useGraphStore((s) => s.updateEdgeData);

  if (selectedNodeId) {
    const node = nodes.find((n) => n.id === selectedNodeId);
    if (!node) return null;
    const d = node.data as unknown as DanNode;

    const editableFields = Object.entries(d).filter(
      ([k]) => !SKIP_FIELDS.has(k),
    );

    return (
      <div className="w-72 bg-gray-50 border-l border-gray-200 p-3 overflow-y-auto">
        <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
          Node Config
        </h2>
        <div className="text-xs text-gray-400 mb-3">
          {d.node_type} &middot; {d.id}
        </div>
        <div className="flex flex-col gap-2">
          {editableFields.map(([key, value]) => (
            <label key={key} className="flex flex-col gap-0.5">
              <span className="text-[11px] font-medium text-gray-500">{key}</span>
              {typeof value === "boolean" ? (
                <input
                  type="checkbox"
                  checked={value}
                  onChange={(e) =>
                    updateNodeData(d.id, { [key]: e.target.checked } as Partial<DanNode>)
                  }
                  className="w-4 h-4"
                />
              ) : typeof value === "number" ? (
                <input
                  type="number"
                  value={value}
                  onChange={(e) =>
                    updateNodeData(d.id, { [key]: parseFloat(e.target.value) || 0 } as Partial<DanNode>)
                  }
                  className="border rounded px-2 py-1 text-xs"
                />
              ) : typeof value === "string" ? (
                value.length > 120 || LARGE_TEXT_FIELDS.has(key) ? (
                  <textarea
                    value={value}
                    onChange={(e) =>
                      updateNodeData(d.id, { [key]: e.target.value } as Partial<DanNode>)
                    }
                    className="border rounded px-2 py-1 text-xs font-mono min-h-32 resize-y"
                  />
                ) : (
                  <input
                    type="text"
                    value={value}
                    onChange={(e) =>
                      updateNodeData(d.id, { [key]: e.target.value } as Partial<DanNode>)
                    }
                    className="border rounded px-2 py-1 text-xs"
                  />
                )
              ) : (
                <textarea
                  value={JSON.stringify(value, null, 2)}
                  onChange={(e) => {
                    try {
                      updateNodeData(d.id, { [key]: JSON.parse(e.target.value) } as Partial<DanNode>);
                    } catch { /* let user keep typing */ }
                  }}
                  className="border rounded px-2 py-1 text-xs font-mono h-16 resize-y"
                />
              )}
            </label>
          ))}
        </div>
        <div className="mt-4">
          <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Ports</h3>
          <div className="text-[11px] text-gray-500">
            <div>In: {d.input_ports.map((p) => p.name).join(", ") || "none"}</div>
            <div>Out: {d.output_ports.map((p) => p.name).join(", ") || "none"}</div>
          </div>
        </div>
      </div>
    );
  }

  if (selectedEdgeId) {
    const edge = edges.find((e) => e.id === selectedEdgeId);
    if (!edge) return null;
    const danEdge = (edge.data?.danEdge ?? {}) as Record<string, unknown>;
    const edgeType = (danEdge.edge_type as string) ?? "data";

    return (
      <div className="w-72 bg-gray-50 border-l border-gray-200 p-3 overflow-y-auto">
        <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
          Edge Config
        </h2>
        <div className="text-[11px] text-gray-400 mb-3">
          {edge.source} &rarr; {edge.target}
        </div>
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">edge_type</span>
            <select
              value={edgeType}
              onChange={(e) => updateEdgeData(edge.id, { edge_type: e.target.value })}
              className="border rounded px-2 py-1 text-xs"
            >
              <option value="data">data</option>
              <option value="control">control</option>
              <option value="context">context</option>
            </select>
          </label>

          {edgeType === "control" && (
            <label className="flex flex-col gap-0.5">
              <span className="text-[11px] font-medium text-gray-500">condition</span>
              <input
                type="text"
                value={(danEdge.condition as string) ?? ""}
                onChange={(e) => updateEdgeData(edge.id, { condition: e.target.value || null })}
                placeholder="e.g. output.success == true"
                className="border rounded px-2 py-1 text-xs font-mono"
              />
            </label>
          )}

          {edgeType === "context" && (
            <>
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">context_key</span>
                <input
                  type="text"
                  value={(danEdge.context_key as string) ?? ""}
                  onChange={(e) => updateEdgeData(edge.id, { context_key: e.target.value })}
                  className="border rounded px-2 py-1 text-xs"
                />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">mode</span>
                <select
                  value={(danEdge.mode as string) ?? "read"}
                  onChange={(e) => updateEdgeData(edge.id, { mode: e.target.value })}
                  className="border rounded px-2 py-1 text-xs"
                >
                  <option value="read">read</option>
                  <option value="write">write</option>
                  <option value="append">append</option>
                </select>
              </label>
            </>
          )}

          <div className="mt-2">
            <span className="text-[11px] font-medium text-gray-500">Raw JSON</span>
            <pre className="text-[10px] bg-white border rounded p-2 overflow-auto max-h-40 mt-0.5">
              {JSON.stringify(danEdge, null, 2)}
            </pre>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-72 bg-gray-50 border-l border-gray-200 p-3">
      <p className="text-xs text-gray-400 mt-8 text-center">
        Select a node or edge to configure
      </p>
    </div>
  );
}
