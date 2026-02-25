import { useState, useMemo } from "react";
import { useGraphStore } from "../store/useGraphStore";
import type { DanNode, InputPort, OutputPort } from "../types/graph";

const SKIP_FIELDS = new Set([
  "id", "node_type", "input_ports", "output_ports", "position", "ui", "metadata",
  "read_set", "write_set", "compaction_rule", "failure_policy", "projections",
  "local_state", "control_state_schema", "external_input_schema", "external_output_schema",
  "output_json_schema",
]);

const LARGE_TEXT_FIELDS = new Set(["prompt_template", "code", "system_prompt"]);

const GATE_DEDICATED_FIELDS = new Set(["gate_mode", "condition", "max_iterations"]);

const SCHEMA_TYPES = ["string", "number", "boolean", "array", "object"] as const;

// -- Port Editor Row ---------------------------------------------------------

function PortRow({
  port,
  portType,
  nodeId,
  allPortNames,
  showRequired,
  onRequiredChange,
}: {
  port: InputPort | OutputPort;
  portType: "input" | "output";
  nodeId: string;
  allPortNames: string[];
  showRequired: boolean;
  onRequiredChange?: (checked: boolean) => void;
}) {
  const renamePort = useGraphStore((s) => s.renamePort);
  const deletePort = useGraphStore((s) => s.deletePort);
  const [name, setName] = useState(port.name);
  const [error, setError] = useState("");

  const commitName = () => {
    const trimmed = name.trim();
    if (trimmed === port.name) { setError(""); return; }
    if (!trimmed) { setError("Empty name"); setName(port.name); return; }
    if (allPortNames.some((n) => n !== port.name && n === trimmed)) {
      setError("Duplicate");
      return;
    }
    setError("");
    renamePort(nodeId, portType, port.name, trimmed);
  };

  return (
    <div className="flex items-center gap-1">
      <input
        value={name}
        onChange={(e) => { setName(e.target.value); setError(""); }}
        onBlur={commitName}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") { setName(port.name); setError(""); }
        }}
        className={`flex-1 min-w-0 border rounded px-1.5 py-0.5 text-xs ${error ? "border-red-400 bg-red-50" : ""}`}
        title={error || undefined}
      />
      {showRequired && (
        <input
          type="checkbox"
          checked={(port as InputPort).required !== false}
          onChange={(e) => onRequiredChange?.(e.target.checked)}
          title="Required"
          className="w-3 h-3 shrink-0"
        />
      )}
      <button
        onClick={() => deletePort(nodeId, portType, port.name)}
        className="text-gray-400 hover:text-red-500 text-sm leading-none px-0.5 shrink-0"
        title="Delete port"
      >
        ×
      </button>
    </div>
  );
}

// -- Schema Helpers ----------------------------------------------------------

interface SchemaProperty {
  name: string;
  type: string;
  required: boolean;
}

function parseSchemaProperties(
  schema: Record<string, unknown> | null | undefined,
): SchemaProperty[] | null {
  if (
    !schema ||
    schema.type !== "object" ||
    typeof schema.properties !== "object" ||
    !schema.properties
  )
    return null;
  const props = schema.properties as Record<string, Record<string, unknown>>;
  const req = Array.isArray(schema.required) ? (schema.required as string[]) : [];
  return Object.entries(props).map(([name, def]) => ({
    name,
    type: typeof def.type === "string" ? def.type : "string",
    required: req.includes(name),
  }));
}

function propertiesToSchema(properties: SchemaProperty[]): Record<string, unknown> {
  const props: Record<string, unknown> = {};
  const req: string[] = [];
  for (const p of properties) {
    props[p.name] = { type: p.type };
    if (p.required) req.push(p.name);
  }
  return { type: "object", properties: props, ...(req.length > 0 ? { required: req } : {}) };
}

// -- Schema Editor -----------------------------------------------------------

function SchemaEditor({
  nodeId,
  schema,
}: {
  nodeId: string;
  schema: Record<string, unknown> | null | undefined;
}) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const parsedProps = useMemo(() => parseSchemaProperties(schema), [schema]);

  const [mode, setMode] = useState<"visual" | "raw">(
    parseSchemaProperties(schema) !== null ? "visual" : "raw",
  );
  const [rawJson, setRawJson] = useState(schema ? JSON.stringify(schema, null, 2) : "");
  const [rawError, setRawError] = useState(false);

  const switchToRaw = () => {
    setRawJson(schema ? JSON.stringify(schema, null, 2) : "");
    setRawError(false);
    setMode("raw");
  };

  const switchToVisual = () => {
    if (!rawJson.trim() || rawJson.trim() === "null") {
      const empty = { type: "object", properties: {} };
      updateNodeData(nodeId, { output_json_schema: empty } as unknown as Partial<DanNode>);
      setMode("visual");
      return;
    }
    try {
      const parsed = JSON.parse(rawJson);
      updateNodeData(nodeId, { output_json_schema: parsed } as unknown as Partial<DanNode>);
      setRawError(false);
      setMode("visual");
    } catch {
      setRawError(true);
    }
  };

  const commitRawJson = () => {
    if (!rawJson.trim()) {
      updateNodeData(nodeId, { output_json_schema: null } as unknown as Partial<DanNode>);
      setRawError(false);
      return;
    }
    try {
      updateNodeData(nodeId, { output_json_schema: JSON.parse(rawJson) } as unknown as Partial<DanNode>);
      setRawError(false);
    } catch {
      setRawError(true);
    }
  };

  const updateProperty = (index: number, field: keyof SchemaProperty, value: string | boolean) => {
    if (!parsedProps) return;
    const updated = parsedProps.map((p, i) => (i === index ? { ...p, [field]: value } : p));
    updateNodeData(nodeId, { output_json_schema: propertiesToSchema(updated) } as unknown as Partial<DanNode>);
  };

  const addProperty = () => {
    const existing = parsedProps ?? [];
    let idx = existing.length;
    let name = `field_${idx}`;
    const names = existing.map((p) => p.name);
    while (names.includes(name)) { idx++; name = `field_${idx}`; }
    const updated = [...existing, { name, type: "string", required: false }];
    updateNodeData(nodeId, { output_json_schema: propertiesToSchema(updated) } as unknown as Partial<DanNode>);
  };

  const deleteProperty = (index: number) => {
    if (!parsedProps) return;
    const updated = parsedProps.filter((_, i) => i !== index);
    updateNodeData(nodeId, { output_json_schema: propertiesToSchema(updated) } as unknown as Partial<DanNode>);
  };

  return (
    <div className="mt-3">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-[11px] font-semibold text-gray-400 uppercase">Output Schema</h3>
        <div className="flex gap-0.5">
          <button
            onClick={mode !== "visual" ? switchToVisual : undefined}
            className={`text-[10px] px-1.5 py-0.5 rounded ${mode === "visual" ? "bg-blue-100 text-blue-700 font-medium" : "text-gray-400 hover:text-gray-600"}`}
          >
            Visual
          </button>
          <button
            onClick={mode !== "raw" ? switchToRaw : undefined}
            className={`text-[10px] px-1.5 py-0.5 rounded ${mode === "raw" ? "bg-blue-100 text-blue-700 font-medium" : "text-gray-400 hover:text-gray-600"}`}
          >
            JSON
          </button>
        </div>
      </div>

      {mode === "raw" ? (
        <div>
          <textarea
            value={rawJson}
            onChange={(e) => { setRawJson(e.target.value); setRawError(false); }}
            onBlur={commitRawJson}
            className={`w-full border rounded px-2 py-1 text-xs font-mono h-24 resize-y ${rawError ? "border-red-400 bg-red-50" : ""}`}
            placeholder='{"type": "object", "properties": {...}}'
          />
          {rawError && <p className="text-[10px] text-red-500 mt-0.5">Invalid JSON</p>}
        </div>
      ) : parsedProps ? (
        <div className="flex flex-col gap-1">
          {parsedProps.map((prop, i) => (
            <div key={i} className="flex items-center gap-1">
              <input
                value={prop.name}
                onChange={(e) => updateProperty(i, "name", e.target.value)}
                className="flex-1 min-w-0 border rounded px-1.5 py-0.5 text-xs"
                placeholder="name"
              />
              <select
                value={prop.type}
                onChange={(e) => updateProperty(i, "type", e.target.value)}
                className="border rounded px-1 py-0.5 text-xs shrink-0"
              >
                {SCHEMA_TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <input
                type="checkbox"
                checked={prop.required}
                onChange={(e) => updateProperty(i, "required", e.target.checked)}
                title="Required"
                className="w-3 h-3 shrink-0"
              />
              <button
                onClick={() => deleteProperty(i)}
                className="text-gray-400 hover:text-red-500 text-sm leading-none px-0.5 shrink-0"
                title="Delete property"
              >
                ×
              </button>
            </div>
          ))}
          <button
            onClick={addProperty}
            className="text-xs text-blue-500 hover:text-blue-700 mt-0.5"
          >
            + Add Property
          </button>
        </div>
      ) : (
        <p className="text-[10px] text-gray-400 italic">
          Not an object schema. Use JSON mode to edit.
        </p>
      )}
    </div>
  );
}

// -- Main Component ----------------------------------------------------------

export default function ConfigPanel() {
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const selectedEdgeId = useGraphStore((s) => s.selectedEdgeId);
  const selectedNodeIds = useGraphStore((s) => s.selectedNodeIds);
  const nodes = useGraphStore((s) => s.nodes);
  const edges = useGraphStore((s) => s.edges);
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const updateEdgeData = useGraphStore((s) => s.updateEdgeData);
  const deleteSelected = useGraphStore((s) => s.deleteSelected);

  // -- 6-1: Multi-select summary
  if (selectedNodeIds.size > 1) {
    return (
      <div className="w-72 bg-gray-50 border-l border-gray-200 p-3">
        <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
          Multi-Select
        </h2>
        <p className="text-sm text-gray-600 mb-3">{selectedNodeIds.size} nodes selected</p>
        <button
          onClick={deleteSelected}
          className="px-3 py-1.5 text-xs bg-red-500 text-white rounded hover:bg-red-600 transition-colors"
        >
          Delete Selected
        </button>
      </div>
    );
  }

  if (selectedNodeId) {
    const node = nodes.find((n) => n.id === selectedNodeId);
    if (!node) return null;
    const d = node.data as unknown as DanNode;

    const editableFields = Object.entries(d).filter(
      ([k]) => !SKIP_FIELDS.has(k) && !(d.node_type === "gate" && GATE_DEDICATED_FIELDS.has(k)),
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

        {/* 6-3: Port Editor — Input Ports */}
        <div className="mt-4">
          <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Input Ports</h3>
          <div className="flex flex-col gap-1">
            {d.input_ports.map((port) => (
              <PortRow
                key={port.name}
                port={port}
                portType="input"
                nodeId={d.id}
                allPortNames={d.input_ports.map((p) => p.name)}
                showRequired
                onRequiredChange={(checked) => {
                  const updated = d.input_ports.map((p) =>
                    p.name === port.name ? { ...p, required: checked } : p,
                  );
                  updateNodeData(d.id, { input_ports: updated } as Partial<DanNode>);
                }}
              />
            ))}
          </div>
          <button
            onClick={() => {
              const names = d.input_ports.map((p) => p.name);
              let idx = d.input_ports.length;
              let nm = `input_${idx}`;
              while (names.includes(nm)) { idx++; nm = `input_${idx}`; }
              updateNodeData(d.id, {
                input_ports: [...d.input_ports, { name: nm, schema: {}, required: true }],
              } as Partial<DanNode>);
            }}
            className="text-xs text-blue-500 hover:text-blue-700 mt-1"
          >
            + Add Port
          </button>
        </div>

        {/* 6-3: Port Editor — Output Ports */}
        <div className="mt-3">
          <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Output Ports</h3>
          <div className="flex flex-col gap-1">
            {d.output_ports.map((port) => (
              <PortRow
                key={port.name}
                port={port}
                portType="output"
                nodeId={d.id}
                allPortNames={d.output_ports.map((p) => p.name)}
                showRequired={false}
              />
            ))}
          </div>
          <button
            onClick={() => {
              const names = d.output_ports.map((p) => p.name);
              let idx = d.output_ports.length;
              let nm = `output_${idx}`;
              while (names.includes(nm)) { idx++; nm = `output_${idx}`; }
              updateNodeData(d.id, {
                output_ports: [...d.output_ports, { name: nm, schema: {} }],
              } as Partial<DanNode>);
            }}
            className="text-xs text-blue-500 hover:text-blue-700 mt-1"
          >
            + Add Port
          </button>
        </div>

        {/* 6-3: Output Schema Editor — for LLM and router nodes */}
        {(d.node_type === "llm_operator" || d.node_type === "router") && (
          <SchemaEditor
            nodeId={d.id}
            schema={
              (d as unknown as Record<string, unknown>).output_json_schema as
                | Record<string, unknown>
                | null
                | undefined
            }
          />
        )}

        {/* 6-10: Gate config — mode, condition, max_iterations */}
        {d.node_type === "gate" && (
          <div className="mt-3">
            <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Gate Config</h3>
            <div className="flex flex-col gap-2">
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">gate_mode</span>
                <select
                  value={d.gate_mode}
                  onChange={(e) => {
                    const mode = e.target.value as "if_else" | "while";
                    const ports = mode === "if_else"
                      ? [{ name: "true", schema: {} }, { name: "false", schema: {} }]
                      : [{ name: "continue", schema: {} }, { name: "done", schema: {} }];
                    updateNodeData(d.id, { gate_mode: mode, output_ports: ports } as unknown as Partial<DanNode>);
                  }}
                  className="border rounded px-2 py-1 text-xs"
                >
                  <option value="if_else">if_else</option>
                  <option value="while">while</option>
                </select>
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">condition</span>
                <input
                  type="text"
                  value={d.condition}
                  onChange={(e) =>
                    updateNodeData(d.id, { condition: e.target.value } as unknown as Partial<DanNode>)
                  }
                  placeholder="e.g. output.success == true"
                  className="border rounded px-2 py-1 text-xs font-mono"
                />
              </label>
              {d.gate_mode === "while" && (
                <label className="flex flex-col gap-0.5">
                  <span className="text-[11px] font-medium text-gray-500">max_iterations</span>
                  <input
                    type="number"
                    value={d.max_iterations ?? 10}
                    onChange={(e) =>
                      updateNodeData(d.id, { max_iterations: parseInt(e.target.value) || 10 } as unknown as Partial<DanNode>)
                    }
                    className="border rounded px-2 py-1 text-xs"
                    min={1}
                  />
                </label>
              )}
            </div>
          </div>
        )}
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
