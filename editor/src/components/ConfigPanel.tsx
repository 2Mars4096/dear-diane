import { useState, useMemo, useEffect, useCallback } from "react";
import { useGraphStore } from "../store/useGraphStore";
import { resolveGraphAtStack } from "../lib/graphAdapter";
import { getNodeInputs } from "../lib/api";
import type { UpstreamVariable } from "../lib/api";
import TestCaseSection from "./TestCasePanel";
import type { DanNode, InputPort, OutputPort, RetryPolicy } from "../types/graph";

const SKIP_FIELDS = new Set([
  "id", "node_type", "input_ports", "output_ports", "position", "ui", "metadata",
  "read_set", "write_set", "compaction_rule", "failure_policy", "projections",
  "local_state", "control_state_schema", "external_input_schema", "external_output_schema",
  "output_json_schema", "retry_policy",
  "model", "temperature", "system_prompt", "max_tokens",
  "collection", "top_k", "similarity_threshold", "embedding_model", "vector_store_config",
  "query_template", "include_metadata", "rerank",
  "validation_rules", "on_failure", "strict_mode",
]);

const LARGE_TEXT_FIELDS = new Set(["prompt_template", "code", "system_prompt"]);

const GATE_DEDICATED_FIELDS = new Set(["gate_mode", "condition", "max_iterations"]);

const PARALLEL_SUBAGENTS_DEDICATED_FIELDS = new Set([
  "branch_graphs", "input_mappings", "branch_inputs", "merge_strategy", "reducer", "parallelism", "failure_policy",
]);

const BODY_GRAPH_DEDICATED_TYPES = new Set(["while_loop", "goal_loop", "for_each", "composite"]);

const GOAL_LOOP_DEDICATED_FIELDS = new Set([
  "body_graph",
  "goal_text",
  "metric_name",
  "target_value",
  "comparison",
  "max_iterations",
  "evaluator",
  "success_criteria",
]);

const VOTE_DEDICATED_FIELDS = new Set([
  "candidates",
  "num_votes",
  "prompt_template",
  "system_prompt",
  "temperature",
  "vote_strategy",
  "parallelism",
  "timeout_seconds",
  "vote_config",
]);

const REFLECTION_DEDICATED_FIELDS = new Set([
  "reflection_prompt",
  "reflection_model",
  "source",
  "source_config",
  "output_format",
  "max_principles",
  "min_confidence",
  "dedup_strategy",
]);

const ORCHESTRATOR_DEDICATED_FIELDS = new Set([
  "teams",
  "orchestrator_prompt",
  "orchestrator_model",
  "completion_condition",
  "max_iterations",
  "timeout_seconds",
]);

const AGENT_TEAM_DEDICATED_FIELDS = new Set([
  "agents",
  "moderator_prompt",
  "moderator_model",
  "turn_strategy",
  "max_turns",
  "completion_condition",
  "timeout_seconds",
  "shared_context_keys",
  "handoff_policy",
]);

const HUMAN_DEDICATED_FIELDS = new Set([
  "prompt",
  "timeout_seconds",
  "default_action",
  "render_mode",
  "render_target",
  "instructions",
]);

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

// -- Human Config Section ----------------------------------------------------

function HumanConfigSection({
  nodeId,
  data,
  includeAdvanced,
}: {
  nodeId: string;
  data: Record<string, unknown>;
  includeAdvanced: boolean;
}) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);

  const prompt = (data.prompt as string) ?? "";
  const timeout = data.timeout_seconds as number | null | undefined;
  const defaultAction = (data.default_action as string | null | undefined) ?? "";
  const renderMode = ((data.render_mode as string | undefined) ?? "text") as
    "text" | "approval" | "form" | "selection" | "file_upload" | "rich";
  const renderTarget = ((data.render_target as string | undefined) ?? "dialog") as
    "dialog" | "chat" | "both";
  const instructions = (data.instructions as string) ?? "";

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Human Config</h3>
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">prompt</span>
          <textarea
            value={prompt}
            onChange={(e) =>
              updateNodeData(nodeId, { prompt: e.target.value } as unknown as Partial<DanNode>)
            }
            className="border rounded px-2 py-1 text-xs min-h-20 resize-y"
            placeholder="Describe the human interaction..."
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">timeout_seconds</span>
          <input
            type="number"
            value={timeout ?? ""}
            onChange={(e) =>
              updateNodeData(
                nodeId,
                { timeout_seconds: e.target.value ? parseFloat(e.target.value) : null } as unknown as Partial<DanNode>,
              )
            }
            className="border rounded px-2 py-1 text-xs"
            min={0}
            placeholder="No timeout"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">default_action</span>
          <input
            type="text"
            value={defaultAction}
            onChange={(e) =>
              updateNodeData(
                nodeId,
                { default_action: e.target.value || null } as unknown as Partial<DanNode>,
              )
            }
            className="border rounded px-2 py-1 text-xs"
            placeholder="Optional fallback action"
          />
        </label>

        {includeAdvanced && (
          <>
            <label className="flex flex-col gap-0.5">
              <span className="text-[11px] font-medium text-gray-500">render_mode</span>
              <select
                value={renderMode}
                onChange={(e) =>
                  updateNodeData(
                    nodeId,
                    { render_mode: e.target.value } as unknown as Partial<DanNode>,
                  )
                }
                className="border rounded px-2 py-1 text-xs"
              >
                <option value="text">text</option>
                <option value="approval">approval</option>
                <option value="form">form</option>
                <option value="selection">selection</option>
                <option value="file_upload">file_upload</option>
                <option value="rich">rich</option>
              </select>
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[11px] font-medium text-gray-500">render_target</span>
              <select
                value={renderTarget}
                onChange={(e) =>
                  updateNodeData(
                    nodeId,
                    { render_target: e.target.value } as unknown as Partial<DanNode>,
                  )
                }
                className="border rounded px-2 py-1 text-xs"
              >
                <option value="dialog">dialog</option>
                <option value="chat">chat</option>
                <option value="both">both</option>
              </select>
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[11px] font-medium text-gray-500">instructions</span>
              <textarea
                value={instructions}
                onChange={(e) =>
                  updateNodeData(
                    nodeId,
                    { instructions: e.target.value } as unknown as Partial<DanNode>,
                  )
                }
                className="border rounded px-2 py-1 text-xs min-h-16 resize-y"
                placeholder="Additional guidance shown to the human..."
              />
            </label>
          </>
        )}
      </div>
    </div>
  );
}

// -- Shared Named Sub-graph Editor -------------------------------------------

function NamedSubgraphListEditor({
  nodeId,
  itemLabel,
  mappings,
  availableSubGraphKeys,
  setMappings,
}: {
  nodeId: string;
  itemLabel: string;
  mappings: Record<string, string>;
  availableSubGraphKeys: string[];
  setMappings: (mappings: Record<string, string>) => void;
}) {
  const createEmptySubGraph = useGraphStore((s) => s.createEmptySubGraph);
  const drillIn = useGraphStore((s) => s.drillIn);

  const renameEntry = (oldName: string, newName: string) => {
    const trimmed = newName.trim();
    if (!trimmed || trimmed === oldName) return;
    if (trimmed in mappings) return;
    const next: Record<string, string> = {};
    for (const [key, value] of Object.entries(mappings)) {
      next[key === oldName ? trimmed : key] = value;
    }
    setMappings(next);
  };

  const updateGraphKey = (name: string, graphKey: string) => {
    setMappings({ ...mappings, [name]: graphKey });
  };

  const removeEntry = (name: string) => {
    const next = { ...mappings };
    delete next[name];
    setMappings(next);
  };

  const addEntry = () => {
    const base = itemLabel.toLowerCase();
    let idx = Object.keys(mappings).length + 1;
    let name = `${base}_${idx}`;
    while (name in mappings) {
      idx += 1;
      name = `${base}_${idx}`;
    }
    const graphKey = `${nodeId}_${name}`;
    createEmptySubGraph(graphKey);
    setMappings({ ...mappings, [name]: graphKey });
  };

  return (
    <div>
      <span className="text-[11px] font-medium text-gray-500">{itemLabel.toLowerCase()}s</span>
      <div className="flex flex-col gap-1 mt-0.5">
        {Object.entries(mappings).map(([name, graphKey]) => (
          <div key={name} className="flex items-center gap-1">
            <input
              type="text"
              value={name}
              onChange={(e) => renameEntry(name, e.target.value)}
              className="w-24 border rounded px-1.5 py-0.5 text-xs font-mono"
              placeholder={`${itemLabel.toLowerCase()} name`}
            />
            <input
              type="text"
              list={`${itemLabel.toLowerCase()}-subgraph-list-${nodeId}`}
              value={graphKey}
              onChange={(e) => updateGraphKey(name, e.target.value)}
              className="flex-1 min-w-0 border rounded px-1.5 py-0.5 text-xs font-mono"
              placeholder="sub_graph key"
            />
            <datalist id={`${itemLabel.toLowerCase()}-subgraph-list-${nodeId}`}>
              {availableSubGraphKeys.map((key) => (
                <option key={key} value={key} />
              ))}
            </datalist>
            <button
              onClick={() => drillIn(nodeId, graphKey)}
              disabled={!graphKey || !availableSubGraphKeys.includes(graphKey)}
              className="text-[10px] px-1.5 py-0.5 border rounded text-blue-600 disabled:text-gray-300 disabled:border-gray-200"
              title="Open subgraph"
            >
              Open
            </button>
            <button
              onClick={() => removeEntry(name)}
              className="text-gray-400 hover:text-red-500 text-sm leading-none px-0.5 shrink-0"
              title={`Remove ${itemLabel.toLowerCase()}`}
            >
              ×
            </button>
          </div>
        ))}
        <button onClick={addEntry} className="text-xs text-blue-500 hover:text-blue-700 mt-0.5">
          + Add {itemLabel}
        </button>
      </div>
    </div>
  );
}

// -- Body Graph Config Section -----------------------------------------------

function BodyGraphConfigSection({
  nodeId,
  data,
}: {
  nodeId: string;
  data: Record<string, unknown>;
}) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const createEmptySubGraph = useGraphStore((s) => s.createEmptySubGraph);
  const drillIn = useGraphStore((s) => s.drillIn);
  const danGraph = useGraphStore((s) => s.danGraph);
  const layerStack = useGraphStore((s) => s.layerStack);

  const bodyGraphKey = (data.body_graph as string | undefined) ?? "";
  const currentGraph = useMemo(() => {
    if (!danGraph) return null;
    return resolveGraphAtStack(danGraph, layerStack);
  }, [danGraph, layerStack]);
  const availableSubGraphKeys = Object.keys(currentGraph?.sub_graphs ?? {});

  const createBodyGraph = () => {
    if (bodyGraphKey.trim()) return;
    const key = `${nodeId}_body`;
    createEmptySubGraph(key);
    updateNodeData(nodeId, { body_graph: key } as unknown as Partial<DanNode>);
  };

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Body Graph</h3>
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">body_graph</span>
          <div className="flex items-center gap-1">
            <input
              type="text"
              list={`body-graph-list-${nodeId}`}
              value={bodyGraphKey}
              onChange={(e) =>
                updateNodeData(nodeId, { body_graph: e.target.value } as unknown as Partial<DanNode>)
              }
              className="flex-1 min-w-0 border rounded px-2 py-1 text-xs font-mono"
              placeholder="sub_graph key"
            />
            <datalist id={`body-graph-list-${nodeId}`}>
              {availableSubGraphKeys.map((key) => (
                <option key={key} value={key} />
              ))}
            </datalist>
            <button
              onClick={() => drillIn(nodeId, bodyGraphKey)}
              disabled={!bodyGraphKey || !availableSubGraphKeys.includes(bodyGraphKey)}
              className="text-[10px] px-1.5 py-0.5 border rounded text-blue-600 disabled:text-gray-300 disabled:border-gray-200"
            >
              Open
            </button>
          </div>
        </label>
        {!bodyGraphKey && (
          <button onClick={createBodyGraph} className="text-xs text-blue-500 hover:text-blue-700 self-start">
            + Create Body Graph
          </button>
        )}
      </div>
    </div>
  );
}

// -- Orchestrator Config Section ---------------------------------------------

function OrchestratorConfigSection({ nodeId, data }: { nodeId: string; data: Record<string, unknown> }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const danGraph = useGraphStore((s) => s.danGraph);
  const layerStack = useGraphStore((s) => s.layerStack);

  const teams = (data.teams ?? {}) as Record<string, string>;
  const prompt = (data.orchestrator_prompt as string) ?? "";
  const model = (data.orchestrator_model as string | null | undefined) ?? "";
  const completionCondition = (data.completion_condition as string) ?? "all_done";
  const maxIterations = (data.max_iterations as number) ?? 100;
  const timeout = data.timeout_seconds as number | null | undefined;

  const currentGraph = useMemo(() => {
    if (!danGraph) return null;
    return resolveGraphAtStack(danGraph, layerStack);
  }, [danGraph, layerStack]);
  const availableSubGraphKeys = Object.keys(currentGraph?.sub_graphs ?? {});

  const update = (patch: Record<string, unknown>) => {
    updateNodeData(nodeId, patch as unknown as Partial<DanNode>);
  };

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Orchestrator</h3>
      <div className="flex flex-col gap-2 pl-2 border-l border-fuchsia-200">
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">orchestrator_prompt</span>
          <textarea
            value={prompt}
            onChange={(e) => update({ orchestrator_prompt: e.target.value })}
            className="border rounded px-2 py-1 text-xs min-h-16 resize-y"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">orchestrator_model</span>
          <input
            type="text"
            value={model}
            onChange={(e) => update({ orchestrator_model: e.target.value || null })}
            className="border rounded px-2 py-1 text-xs"
            placeholder="Use engine default"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">completion_condition</span>
          <select
            value={completionCondition}
            onChange={(e) => update({ completion_condition: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          >
            <option value="all_done">all_done</option>
            <option value="any_done">any_done</option>
            <option value="orchestrator_halt">orchestrator_halt</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">max_iterations</span>
          <input
            type="number"
            value={maxIterations}
            onChange={(e) => update({ max_iterations: parseInt(e.target.value) || 1 })}
            className="border rounded px-2 py-1 text-xs"
            min={1}
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">timeout_seconds</span>
          <input
            type="number"
            value={timeout ?? ""}
            onChange={(e) => update({ timeout_seconds: e.target.value ? parseFloat(e.target.value) : null })}
            className="border rounded px-2 py-1 text-xs"
            min={0}
            placeholder="No timeout"
          />
        </label>
        <NamedSubgraphListEditor
          nodeId={nodeId}
          itemLabel="Team"
          mappings={teams}
          availableSubGraphKeys={availableSubGraphKeys}
          setMappings={(mappings) => update({ teams: mappings })}
        />
      </div>
    </div>
  );
}

// -- Agent Team Config Section -----------------------------------------------

function AgentTeamConfigSection({ nodeId, data }: { nodeId: string; data: Record<string, unknown> }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const danGraph = useGraphStore((s) => s.danGraph);
  const layerStack = useGraphStore((s) => s.layerStack);

  const agents = (data.agents ?? {}) as Record<string, string>;
  const prompt = (data.moderator_prompt as string) ?? "";
  const model = (data.moderator_model as string | null | undefined) ?? "";
  const turnStrategy = (data.turn_strategy as string) ?? "round_robin";
  const maxTurns = (data.max_turns as number) ?? 20;
  const completionCondition = (data.completion_condition as string) ?? "max_turns";
  const timeout = data.timeout_seconds as number | null | undefined;
  const handoffPolicy = (data.handoff_policy as string) ?? "explicit";
  const sharedContextKeys = ((data.shared_context_keys as string[] | undefined) ?? []).join(", ");

  const currentGraph = useMemo(() => {
    if (!danGraph) return null;
    return resolveGraphAtStack(danGraph, layerStack);
  }, [danGraph, layerStack]);
  const availableSubGraphKeys = Object.keys(currentGraph?.sub_graphs ?? {});

  const update = (patch: Record<string, unknown>) => {
    updateNodeData(nodeId, patch as unknown as Partial<DanNode>);
  };

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Agent Team</h3>
      <div className="flex flex-col gap-2 pl-2 border-l border-teal-200">
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">moderator_prompt</span>
          <textarea
            value={prompt}
            onChange={(e) => update({ moderator_prompt: e.target.value })}
            className="border rounded px-2 py-1 text-xs min-h-16 resize-y"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">moderator_model</span>
          <input
            type="text"
            value={model}
            onChange={(e) => update({ moderator_model: e.target.value || null })}
            className="border rounded px-2 py-1 text-xs"
            placeholder="Use engine default"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">turn_strategy</span>
          <select
            value={turnStrategy}
            onChange={(e) => update({ turn_strategy: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          >
            <option value="round_robin">round_robin</option>
            <option value="moderator">moderator</option>
            <option value="free_form">free_form</option>
            <option value="sequential">sequential</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">max_turns</span>
          <input
            type="number"
            value={maxTurns}
            onChange={(e) => update({ max_turns: parseInt(e.target.value) || 1 })}
            className="border rounded px-2 py-1 text-xs"
            min={1}
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">completion_condition</span>
          <select
            value={completionCondition}
            onChange={(e) => update({ completion_condition: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          >
            <option value="consensus">consensus</option>
            <option value="moderator_halt">moderator_halt</option>
            <option value="max_turns">max_turns</option>
            <option value="all_responded">all_responded</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">timeout_seconds</span>
          <input
            type="number"
            value={timeout ?? ""}
            onChange={(e) => update({ timeout_seconds: e.target.value ? parseFloat(e.target.value) : null })}
            className="border rounded px-2 py-1 text-xs"
            min={0}
            placeholder="No timeout"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">handoff_policy</span>
          <select
            value={handoffPolicy}
            onChange={(e) => update({ handoff_policy: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          >
            <option value="explicit">explicit</option>
            <option value="any">any</option>
            <option value="moderator_only">moderator_only</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">shared_context_keys</span>
          <input
            type="text"
            value={sharedContextKeys}
            onChange={(e) =>
              update({
                shared_context_keys: e.target.value
                  .split(",")
                  .map((value) => value.trim())
                  .filter(Boolean),
              })
            }
            className="border rounded px-2 py-1 text-xs"
            placeholder="conversation_history, shared_artifacts"
          />
        </label>
        <NamedSubgraphListEditor
          nodeId={nodeId}
          itemLabel="Agent"
          mappings={agents}
          availableSubGraphKeys={availableSubGraphKeys}
          setMappings={(mappings) => update({ agents: mappings })}
        />
      </div>
    </div>
  );
}

// -- Goal Loop Config Section ------------------------------------------------

function GoalLoopConfigSection({ nodeId, data }: { nodeId: string; data: Record<string, unknown> }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const goalText = (data.goal_text as string) ?? "";
  const metricName = (data.metric_name as string) ?? "score";
  const targetValue = (data.target_value as number) ?? 1.0;
  const comparison = (data.comparison as string) ?? ">=";
  const maxIterations = (data.max_iterations as number) ?? 10;
  const evaluator = (data.evaluator as string) ?? "llm_judge";
  const successCriteria = (data.success_criteria as string | null | undefined) ?? "";

  const update = (patch: Record<string, unknown>) => {
    updateNodeData(nodeId, patch as unknown as Partial<DanNode>);
  };

  return (
    <>
      <BodyGraphConfigSection nodeId={nodeId} data={data} />
      <div className="mt-3">
        <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Goal Loop</h3>
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">goal_text</span>
            <textarea
              value={goalText}
              onChange={(e) => update({ goal_text: e.target.value })}
              className="border rounded px-2 py-1 text-xs min-h-16 resize-y"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">metric_name</span>
            <input
              type="text"
              value={metricName}
              onChange={(e) => update({ metric_name: e.target.value })}
              className="border rounded px-2 py-1 text-xs"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">target_value</span>
            <input
              type="number"
              value={targetValue}
              onChange={(e) => update({ target_value: parseFloat(e.target.value) || 0 })}
              className="border rounded px-2 py-1 text-xs"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">comparison</span>
            <select
              value={comparison}
              onChange={(e) => update({ comparison: e.target.value })}
              className="border rounded px-2 py-1 text-xs"
            >
              <option value=">=">{">="}</option>
              <option value="<=">{"<="}</option>
              <option value="==">{"=="}</option>
              <option value=">">{">"}</option>
              <option value="<">{"<"}</option>
            </select>
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">max_iterations</span>
            <input
              type="number"
              value={maxIterations}
              onChange={(e) => update({ max_iterations: parseInt(e.target.value) || 1 })}
              className="border rounded px-2 py-1 text-xs"
              min={1}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">evaluator</span>
            <input
              type="text"
              value={evaluator}
              onChange={(e) => update({ evaluator: e.target.value })}
              className="border rounded px-2 py-1 text-xs"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">success_criteria</span>
            <input
              type="text"
              value={successCriteria}
              onChange={(e) => update({ success_criteria: e.target.value || null })}
              className="border rounded px-2 py-1 text-xs"
              placeholder="Optional expression"
            />
          </label>
        </div>
      </div>
    </>
  );
}

// -- Vote Config Section -----------------------------------------------------

function VoteConfigSection({ nodeId, data }: { nodeId: string; data: Record<string, unknown> }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const candidates = ((data.candidates as string[] | undefined) ?? []).join(", ");
  const numVotes = (data.num_votes as number) ?? 3;
  const promptTemplate = (data.prompt_template as string) ?? "";
  const systemPrompt = (data.system_prompt as string) ?? "";
  const temperature = (data.temperature as number) ?? 0.7;
  const voteStrategy = (data.vote_strategy as string) ?? "majority";
  const parallelism = (data.parallelism as number) ?? 3;
  const timeout = (data.timeout_seconds as number | null | undefined) ?? "";

  const update = (patch: Record<string, unknown>) => {
    updateNodeData(nodeId, patch as unknown as Partial<DanNode>);
  };

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Vote</h3>
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">candidates</span>
          <input
            type="text"
            value={candidates}
            onChange={(e) =>
              update({
                candidates: e.target.value.split(",").map((value) => value.trim()).filter(Boolean),
              })
            }
            className="border rounded px-2 py-1 text-xs"
            placeholder="claude-sonnet-4-6, gpt-4o"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">num_votes</span>
          <input
            type="number"
            value={numVotes}
            onChange={(e) => update({ num_votes: parseInt(e.target.value) || 1 })}
            className="border rounded px-2 py-1 text-xs"
            min={1}
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">vote_strategy</span>
          <select
            value={voteStrategy}
            onChange={(e) => update({ vote_strategy: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          >
            <option value="majority">majority</option>
            <option value="weighted">weighted</option>
            <option value="best_of_n">best_of_n</option>
            <option value="judge">judge</option>
            <option value="unanimous">unanimous</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">parallelism</span>
          <input
            type="number"
            value={parallelism}
            onChange={(e) => update({ parallelism: parseInt(e.target.value) || 1 })}
            className="border rounded px-2 py-1 text-xs"
            min={1}
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">timeout_seconds</span>
          <input
            type="number"
            value={timeout}
            onChange={(e) => update({ timeout_seconds: e.target.value ? parseFloat(e.target.value) : null })}
            className="border rounded px-2 py-1 text-xs"
            min={0}
            placeholder="No timeout"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">prompt_template</span>
          <textarea
            value={promptTemplate}
            onChange={(e) => update({ prompt_template: e.target.value })}
            className="border rounded px-2 py-1 text-xs min-h-16 resize-y"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">system_prompt</span>
          <textarea
            value={systemPrompt}
            onChange={(e) => update({ system_prompt: e.target.value })}
            className="border rounded px-2 py-1 text-xs min-h-16 resize-y"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">temperature</span>
          <input
            type="number"
            step="0.1"
            value={temperature}
            onChange={(e) => update({ temperature: parseFloat(e.target.value) || 0 })}
            className="border rounded px-2 py-1 text-xs"
          />
        </label>
      </div>
    </div>
  );
}

// -- Reflection Config Section -----------------------------------------------

function ReflectionConfigSection({ nodeId, data }: { nodeId: string; data: Record<string, unknown> }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const reflectionPrompt = (data.reflection_prompt as string) ?? "";
  const reflectionModel = (data.reflection_model as string | null | undefined) ?? "";
  const source = (data.source as string) ?? "last_run";
  const outputFormat = (data.output_format as string) ?? "principles";
  const maxPrinciples = (data.max_principles as number) ?? 10;
  const minConfidence = (data.min_confidence as number) ?? 0.3;
  const dedupStrategy = (data.dedup_strategy as string) ?? "embedding_similarity";

  const update = (patch: Record<string, unknown>) => {
    updateNodeData(nodeId, patch as unknown as Partial<DanNode>);
  };

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Reflection</h3>
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">reflection_prompt</span>
          <textarea
            value={reflectionPrompt}
            onChange={(e) => update({ reflection_prompt: e.target.value })}
            className="border rounded px-2 py-1 text-xs min-h-16 resize-y"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">reflection_model</span>
          <input
            type="text"
            value={reflectionModel}
            onChange={(e) => update({ reflection_model: e.target.value || null })}
            className="border rounded px-2 py-1 text-xs"
            placeholder="Use engine default"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">source</span>
          <select
            value={source}
            onChange={(e) => update({ source: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          >
            <option value="last_run">last_run</option>
            <option value="last_n_runs">last_n_runs</option>
            <option value="error_index">error_index</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">output_format</span>
          <select
            value={outputFormat}
            onChange={(e) => update({ output_format: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          >
            <option value="principles">principles</option>
            <option value="rules">rules</option>
            <option value="summary">summary</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">max_principles</span>
          <input
            type="number"
            value={maxPrinciples}
            onChange={(e) => update({ max_principles: parseInt(e.target.value) || 1 })}
            className="border rounded px-2 py-1 text-xs"
            min={1}
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">min_confidence</span>
          <input
            type="number"
            step="0.05"
            value={minConfidence}
            onChange={(e) => update({ min_confidence: parseFloat(e.target.value) || 0 })}
            className="border rounded px-2 py-1 text-xs"
            min={0}
            max={1}
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">dedup_strategy</span>
          <select
            value={dedupStrategy}
            onChange={(e) => update({ dedup_strategy: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          >
            <option value="embedding_similarity">embedding_similarity</option>
            <option value="exact_key">exact_key</option>
            <option value="none">none</option>
          </select>
        </label>
      </div>
    </div>
  );
}

// -- Retry Policy Editor -----------------------------------------------------

const ON_FAILURE_OPTIONS = ["error", "skip", "halt"] as const;

function RetryPolicyEditor({ nodeId, policy }: { nodeId: string; policy: RetryPolicy | null | undefined }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const [open, setOpen] = useState(!!policy);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const update = (patch: Partial<RetryPolicy>) => {
    updateNodeData(nodeId, { retry_policy: { ...(policy ?? {}), ...patch } } as unknown as Partial<DanNode>);
  };

  if (!policy) {
    return (
      <div className="mt-3">
        <button
          onClick={() => {
            updateNodeData(nodeId, { retry_policy: { max_retries: 0, backoff: 1, backoff_max: 60, on_failure: "error" } } as unknown as Partial<DanNode>);
            setOpen(true);
          }}
          className="text-xs text-blue-500 hover:text-blue-700"
        >
          + Add retry policy
        </button>
      </div>
    );
  }

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 w-full"
      >
        <span className={`text-[10px] transition-transform ${open ? "rotate-90" : ""}`}>&#9654;</span>
        <h3 className="text-[11px] font-semibold text-gray-400 uppercase">Retry Policy</h3>
        <button
          onClick={(e) => {
            e.stopPropagation();
            updateNodeData(nodeId, { retry_policy: null } as unknown as Partial<DanNode>);
            setOpen(false);
          }}
          className="ml-auto text-gray-400 hover:text-red-500 text-[10px]"
          title="Remove retry policy"
        >
          ✕
        </button>
      </button>
      {open && (
        <div className="flex flex-col gap-2 mt-1 pl-2 border-l border-gray-200">
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">max_retries</span>
            <input
              type="number"
              min={0}
              value={policy.max_retries ?? 0}
              onChange={(e) => update({ max_retries: parseInt(e.target.value) || 0 })}
              className="border rounded px-2 py-1 text-xs"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">backoff (seconds)</span>
            <input
              type="number"
              min={0}
              step={0.1}
              value={policy.backoff ?? 1}
              onChange={(e) => update({ backoff: parseFloat(e.target.value) || 1 })}
              className="border rounded px-2 py-1 text-xs"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">fallback_model</span>
            <input
              type="text"
              value={policy.fallback_model ?? ""}
              onChange={(e) => update({ fallback_model: e.target.value || null })}
              placeholder="e.g. gpt-4o-mini"
              className="border rounded px-2 py-1 text-xs"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">on_failure</span>
            <select
              value={policy.on_failure ?? "error"}
              onChange={(e) => update({ on_failure: e.target.value as RetryPolicy["on_failure"] })}
              className="border rounded px-2 py-1 text-xs"
            >
              {ON_FAILURE_OPTIONS.map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </label>
          <div>
            <button
              onClick={() => setAdvancedOpen(!advancedOpen)}
              className="text-[10px] text-gray-400 hover:text-gray-600"
            >
              {advancedOpen ? "▾ Advanced" : "▸ Advanced"}
            </button>
            {advancedOpen && (
              <label className="flex flex-col gap-0.5 mt-1">
                <span className="text-[11px] font-medium text-gray-500">backoff_max (seconds)</span>
                <input
                  type="number"
                  min={0}
                  step={1}
                  value={policy.backoff_max ?? 60}
                  onChange={(e) => update({ backoff_max: parseFloat(e.target.value) || 60 })}
                  className="border rounded px-2 py-1 text-xs"
                />
              </label>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// -- LLM Config Section (model, temperature, system_prompt + advanced) ------

const COMMON_MODELS = [
  { value: "claude-sonnet-4-6", label: "Claude Sonnet 4", provider: "anthropic" },
  { value: "claude-opus-4", label: "Claude Opus 4", provider: "anthropic" },
  { value: "claude-haiku-3.5", label: "Claude Haiku 3.5", provider: "anthropic" },
  { value: "gpt-4o", label: "GPT-4o", provider: "openai" },
  { value: "gpt-4o-mini", label: "GPT-4o Mini", provider: "openai" },
  { value: "gpt-4.1", label: "GPT-4.1", provider: "openai" },
  { value: "gpt-4.1-mini", label: "GPT-4.1 Mini", provider: "openai" },
  { value: "o3-mini", label: "o3-mini", provider: "openai" },
  { value: "gemini-2.5-pro", label: "Gemini 2.5 Pro", provider: "google" },
  { value: "gemini-2.5-flash", label: "Gemini 2.5 Flash", provider: "google" },
  { value: "gemini-2.0-flash", label: "Gemini 2.0 Flash", provider: "google" },
];

const PROVIDER_BADGES: Record<string, { color: string; label: string }> = {
  openai: { color: "bg-green-100 text-green-700", label: "OpenAI" },
  anthropic: { color: "bg-orange-100 text-orange-700", label: "Anthropic" },
  google: { color: "bg-blue-100 text-blue-700", label: "Google" },
};

function LLMConfigSection({ nodeId, data }: { nodeId: string; data: Record<string, unknown> }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const model = (data.model as string) ?? "";
  const temperature = (data.temperature as number) ?? 0.7;
  const systemPrompt = (data.system_prompt as string) ?? "";
  const maxTokens = data.max_tokens as number | null | undefined;

  const matchedModel = COMMON_MODELS.find((m) => m.value === model);
  const badge = matchedModel ? PROVIDER_BADGES[matchedModel.provider] : null;

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">LLM Config</h3>
      <div className="flex flex-col gap-2 pl-2 border-l border-gray-200">
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500 flex items-center gap-1">
            model
            {badge && (
              <span className={`text-[9px] px-1 py-0 rounded ${badge.color}`}>{badge.label}</span>
            )}
          </span>
          <input
            type="text"
            value={model}
            list="dan-model-list"
            onChange={(e) =>
              updateNodeData(nodeId, { model: e.target.value } as unknown as Partial<DanNode>)
            }
            placeholder="e.g. claude-sonnet-4-6"
            className="border rounded px-2 py-1 text-xs"
          />
          <datalist id="dan-model-list">
            {COMMON_MODELS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label} ({m.provider})
              </option>
            ))}
          </datalist>
        </label>

        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">temperature</span>
          <input
            type="number"
            min={0}
            max={2}
            step={0.05}
            value={temperature}
            onChange={(e) =>
              updateNodeData(nodeId, { temperature: parseFloat(e.target.value) || 0 } as unknown as Partial<DanNode>)
            }
            className="border rounded px-2 py-1 text-xs"
          />
        </label>

        {data.system_prompt !== undefined && (
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">system_prompt</span>
            <textarea
              value={systemPrompt}
              onChange={(e) =>
                updateNodeData(nodeId, { system_prompt: e.target.value } as unknown as Partial<DanNode>)
              }
              className="border rounded px-2 py-1 text-xs font-mono min-h-20 resize-y"
              placeholder="System instructions..."
            />
          </label>
        )}

        <div>
          <button
            onClick={() => setAdvancedOpen(!advancedOpen)}
            className="text-[10px] text-gray-400 hover:text-gray-600"
          >
            {advancedOpen ? "▾ Advanced" : "▸ Advanced"}
          </button>
          {advancedOpen && (
            <div className="flex flex-col gap-2 mt-1">
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">max_tokens</span>
                <input
                  type="number"
                  min={1}
                  value={maxTokens ?? ""}
                  onChange={(e) => {
                    const v = e.target.value ? parseInt(e.target.value) : null;
                    updateNodeData(nodeId, { max_tokens: v } as unknown as Partial<DanNode>);
                  }}
                  placeholder="Auto"
                  className="border rounded px-2 py-1 text-xs"
                />
              </label>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// -- RAG Config Section -------------------------------------------------------

const RAG_STORE_BACKENDS = ["memory", "faiss", "chroma"] as const;

function RAGConfigSection({ nodeId, data }: { nodeId: string; data: Record<string, unknown> }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const collection = (data.collection as string) ?? "";
  const topK = (data.top_k as number) ?? 5;
  const queryTemplate = (data.query_template as string) ?? "{query}";
  const includeMetadata = (data.include_metadata as boolean) ?? true;
  const rerank = (data.rerank as boolean) ?? false;
  const embeddingModel = (data.embedding_model as string) ?? "";
  const threshold = data.similarity_threshold as number | null | undefined;
  const storeConfig = (data.vector_store_config ?? {}) as Record<string, unknown>;

  const update = (patch: Record<string, unknown>) => {
    updateNodeData(nodeId, patch as unknown as Partial<DanNode>);
  };

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">RAG Config</h3>
      <div className="flex flex-col gap-2 pl-2 border-l border-purple-200">
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">collection</span>
          <input type="text" value={collection} onChange={(e) => update({ collection: e.target.value })} placeholder="my_knowledge_base" className="border rounded px-2 py-1 text-xs" />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">top_k</span>
          <input type="number" min={1} max={100} value={topK} onChange={(e) => update({ top_k: parseInt(e.target.value) || 5 })} className="border rounded px-2 py-1 text-xs" />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">query_template</span>
          <textarea value={queryTemplate} onChange={(e) => update({ query_template: e.target.value })} className="border rounded px-2 py-1 text-xs font-mono min-h-12 resize-y" placeholder="{query}" />
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={includeMetadata} onChange={(e) => update({ include_metadata: e.target.checked })} className="w-3.5 h-3.5" />
          <span className="text-[11px] font-medium text-gray-500">include_metadata</span>
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={rerank} onChange={(e) => update({ rerank: e.target.checked })} className="w-3.5 h-3.5" />
          <span className="text-[11px] font-medium text-gray-500">rerank</span>
        </label>
        <div>
          <button onClick={() => setAdvancedOpen(!advancedOpen)} className="text-[10px] text-gray-400 hover:text-gray-600">
            {advancedOpen ? "▾ Advanced" : "▸ Advanced"}
          </button>
          {advancedOpen && (
            <div className="flex flex-col gap-2 mt-1">
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">embedding_model</span>
                <input type="text" value={embeddingModel} onChange={(e) => update({ embedding_model: e.target.value })} placeholder="text-embedding-3-small" className="border rounded px-2 py-1 text-xs" />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">similarity_threshold</span>
                <input type="number" min={0} max={1} step={0.05} value={threshold ?? ""} onChange={(e) => update({ similarity_threshold: e.target.value ? parseFloat(e.target.value) : null })} placeholder="None (return all)" className="border rounded px-2 py-1 text-xs" />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">store backend</span>
                <select value={(storeConfig.backend as string) ?? "memory"} onChange={(e) => update({ vector_store_config: { ...storeConfig, backend: e.target.value } })} className="border rounded px-2 py-1 text-xs">
                  {RAG_STORE_BACKENDS.map((b) => <option key={b} value={b}>{b}</option>)}
                </select>
              </label>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// -- Validator Config Section -------------------------------------------------

const RULE_TYPES = ["required_keys", "non_empty", "schema_conformance", "type_check", "custom_expression"] as const;
const ON_FAILURE_VALIDATOR = ["route", "warn", "halt"] as const;

interface VRule {
  rule_type: string;
  config: Record<string, unknown>;
}

function RuleConfigEditor({ value, onChange }: { value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const canonical = JSON.stringify(value, null, 2);

  useEffect(() => {
    try {
      if (JSON.stringify(JSON.parse(text), null, 2) !== canonical) {
        setText(canonical);
        setParseError(null);
      }
    } catch {
      setText(canonical);
      setParseError(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canonical]);

  const commit = () => {
    try {
      const parsed = JSON.parse(text);
      setParseError(null);
      onChange(parsed);
    } catch (err) {
      setParseError((err as Error).message);
    }
  };

  return (
    <>
      <textarea
        value={text}
        onChange={(e) => { setText(e.target.value); setParseError(null); }}
        onBlur={commit}
        className={`mt-1 w-full border rounded px-1.5 py-0.5 text-[10px] font-mono h-12 resize-y ${parseError ? "border-red-400" : ""}`}
        placeholder='{"keys": ["name", "email"]}'
      />
      {parseError && <p className="text-[9px] text-red-500 mt-0.5">{parseError}</p>}
    </>
  );
}

function ValidatorConfigSection({ nodeId, data }: { nodeId: string; data: Record<string, unknown> }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);

  const rules = (data.validation_rules ?? []) as VRule[];
  const onFailure = (data.on_failure as string) ?? "route";
  const strictMode = (data.strict_mode as boolean) ?? false;

  const update = (patch: Record<string, unknown>) => {
    updateNodeData(nodeId, patch as unknown as Partial<DanNode>);
  };

  const updateRule = (idx: number, patch: Partial<VRule>) => {
    const updated = rules.map((r, i) => (i === idx ? { ...r, ...patch } : r));
    update({ validation_rules: updated });
  };

  const addRule = () => {
    update({ validation_rules: [...rules, { rule_type: "required_keys", config: { keys: [] } }] });
  };

  const deleteRule = (idx: number) => {
    update({ validation_rules: rules.filter((_, i) => i !== idx) });
  };

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Validator Config</h3>
      <div className="flex flex-col gap-2 pl-2 border-l border-emerald-200">
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">on_failure</span>
          <select value={onFailure} onChange={(e) => update({ on_failure: e.target.value })} className="border rounded px-2 py-1 text-xs">
            {ON_FAILURE_VALIDATOR.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={strictMode} onChange={(e) => update({ strict_mode: e.target.checked })} className="w-3.5 h-3.5" />
          <span className="text-[11px] font-medium text-gray-500">strict_mode (stop on first failure)</span>
        </label>
        <div>
          <h4 className="text-[11px] font-medium text-gray-500 mb-1">Rules</h4>
          <div className="flex flex-col gap-1.5">
            {rules.map((rule, i) => (
              <div key={i} className="border rounded p-1.5 bg-white">
                <div className="flex items-center gap-1">
                  <select value={rule.rule_type} onChange={(e) => updateRule(i, { rule_type: e.target.value, config: {} })} className="flex-1 border rounded px-1 py-0.5 text-[10px]">
                    {RULE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <button onClick={() => deleteRule(i)} className="text-gray-400 hover:text-red-500 text-sm leading-none px-0.5" title="Delete rule">×</button>
                </div>
                <RuleConfigEditor value={rule.config} onChange={(v) => updateRule(i, { config: v })} />
              </div>
            ))}
          </div>
          <button onClick={addRule} className="text-xs text-blue-500 hover:text-blue-700 mt-1">+ Add Rule</button>
        </div>
      </div>
    </div>
  );
}

// -- Parallel Subagents Config Section ---------------------------------------

const MERGE_STRATEGIES = ["append", "last_write_wins", "reducer"] as const;

function ParallelSubagentsConfigSection({ nodeId, data }: { nodeId: string; data: Record<string, unknown> }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const createEmptySubGraph = useGraphStore((s) => s.createEmptySubGraph);
  const danGraph = useGraphStore((s) => s.danGraph);
  const layerStack = useGraphStore((s) => s.layerStack);
  const [branchInputsOpen, setBranchInputsOpen] = useState(false);
  const [failurePolicyOpen, setFailurePolicyOpen] = useState(false);

  const branchGraphs = (data.branch_graphs ?? []) as string[];
  const inputMappings = (data.input_mappings ?? {}) as Record<string, string>;
  const branchInputs = (data.branch_inputs ?? {}) as Record<string, Record<string, string>>;
  const mergeStrategy = (data.merge_strategy as string) ?? "append";
  const reducer = (data.reducer as string) ?? "";
  const parallelism = (data.parallelism as number) ?? 1;
  const failurePolicy = (data.failure_policy ?? {}) as Record<string, number | null | undefined>;

  const currentGraph = useMemo(() => {
    if (!danGraph) return null;
    return resolveGraphAtStack(danGraph, layerStack);
  }, [danGraph, layerStack]);
  const availableSubGraphKeys = Object.keys(currentGraph?.sub_graphs ?? {});

  const update = (patch: Record<string, unknown>) => {
    updateNodeData(nodeId, patch as unknown as Partial<DanNode>);
  };

  const setBranchGraphs = (list: string[]) => {
    update({ branch_graphs: list });
  };

  const addBranch = () => {
    const key = `branch_${Date.now()}`;
    createEmptySubGraph(key);
    setBranchGraphs([...branchGraphs, key]);
  };

  const removeBranch = (idx: number) => {
    setBranchGraphs(branchGraphs.filter((_, i) => i !== idx));
  };

  const updateBranchKey = (idx: number, newKey: string) => {
    const updated = [...branchGraphs];
    updated[idx] = newKey;
    setBranchGraphs(updated);
  };

  const setInputMappings = (mappings: Record<string, string>) => {
    update({ input_mappings: mappings });
  };

  const addInputMapping = () => {
    const existing = Object.keys(inputMappings);
    let outer = "input";
    let idx = 0;
    while (existing.includes(outer)) {
      idx++;
      outer = `input_${idx}`;
    }
    setInputMappings({ ...inputMappings, [outer]: "input" });
  };

  const updateInputMapping = (outer: string, inner: string) => {
    setInputMappings({ ...inputMappings, [outer]: inner });
  };

  const removeInputMapping = (outer: string) => {
    const next = { ...inputMappings };
    delete next[outer];
    setInputMappings(next);
  };

  return (
    <div className="mt-3">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase mb-1">Parallel Subagents</h3>
      <div className="flex flex-col gap-2 pl-2 border-l border-purple-200">
        {/* Branch list (sub_graph keys) */}
        <div>
          <span className="text-[11px] font-medium text-gray-500">branch_graphs</span>
          <div className="flex flex-col gap-1 mt-0.5">
            {branchGraphs.map((key, i) => (
              <div key={i} className="flex items-center gap-1">
                <input
                  type="text"
                  list={`parallel-branch-list-${nodeId}`}
                  value={key}
                  onChange={(e) => updateBranchKey(i, e.target.value)}
                  placeholder="sub_graph key"
                  className="flex-1 min-w-0 border rounded px-1.5 py-0.5 text-xs font-mono"
                  title="Key into Graph.sub_graphs — pick existing or type new"
                />
                <datalist id={`parallel-branch-list-${nodeId}`}>
                  {availableSubGraphKeys.map((k) => (
                    <option key={k} value={k} />
                  ))}
                </datalist>
                <button
                  onClick={() => removeBranch(i)}
                  className="text-gray-400 hover:text-red-500 text-sm leading-none px-0.5 shrink-0"
                  title="Remove branch"
                >
                  ×
                </button>
              </div>
            ))}
            <button onClick={addBranch} className="text-xs text-blue-500 hover:text-blue-700 mt-0.5">
              + Add Branch
            </button>
          </div>
        </div>

        {/* Input mappings */}
        <div>
          <span className="text-[11px] font-medium text-gray-500">input_mappings</span>
          <p className="text-[10px] text-gray-400 mt-0.5">outer_port → inner_entry_port (shared to all branches)</p>
          <div className="flex flex-col gap-1 mt-0.5">
            {Object.entries(inputMappings).map(([outer, inner]) => (
              <div key={outer} className="flex items-center gap-1">
                <span className="text-[10px] font-mono w-16 truncate shrink-0">{outer}</span>
                <span className="text-gray-400">→</span>
                <input
                  type="text"
                  value={inner}
                  onChange={(e) => updateInputMapping(outer, e.target.value)}
                  className="flex-1 min-w-0 border rounded px-1.5 py-0.5 text-xs font-mono"
                  placeholder="inner port"
                />
                <button
                  onClick={() => removeInputMapping(outer)}
                  className="text-gray-400 hover:text-red-500 text-sm leading-none px-0.5 shrink-0"
                >
                  ×
                </button>
              </div>
            ))}
            <button onClick={addInputMapping} className="text-xs text-blue-500 hover:text-blue-700 mt-0.5">
              + Add Mapping
            </button>
          </div>
        </div>

        {/* Merge strategy */}
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">merge_strategy</span>
          <select
            value={mergeStrategy}
            onChange={(e) => update({ merge_strategy: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          >
            {MERGE_STRATEGIES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>

        {mergeStrategy === "reducer" && (
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-gray-500">reducer</span>
            <input
              type="text"
              value={reducer}
              onChange={(e) => update({ reducer: e.target.value || null })}
              placeholder='e.g. inputs[0]'
              className="border rounded px-2 py-1 text-xs font-mono"
            />
          </label>
        )}

        {/* Parallelism */}
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-gray-500">parallelism</span>
          <input
            type="number"
            min={1}
            value={parallelism}
            onChange={(e) => update({ parallelism: parseInt(e.target.value) || 1 })}
            className="border rounded px-2 py-1 text-xs"
          />
        </label>

        {/* Branch inputs (per-branch overrides) — collapsible */}
        <div>
          <button
            onClick={() => setBranchInputsOpen(!branchInputsOpen)}
            className="text-[10px] text-gray-400 hover:text-gray-600"
          >
            {branchInputsOpen ? "▾ branch_inputs" : "▸ branch_inputs"}
          </button>
          {branchInputsOpen && (
            <textarea
              value={JSON.stringify(branchInputs, null, 2)}
              onChange={(e) => {
                try {
                  update({ branch_inputs: JSON.parse(e.target.value || "{}") });
                } catch { /* keep typing */ }
              }}
              className="mt-1 w-full border rounded px-1.5 py-0.5 text-[10px] font-mono h-16 resize-y"
              placeholder='{"branch_a": {"port": "value"}}'
            />
          )}
        </div>

        {/* Failure policy — collapsible */}
        <div>
          <button
            onClick={() => setFailurePolicyOpen(!failurePolicyOpen)}
            className="text-[10px] text-gray-400 hover:text-gray-600"
          >
            {failurePolicyOpen ? "▾ failure_policy" : "▸ failure_policy"}
          </button>
          {failurePolicyOpen && (
            <div className="flex flex-col gap-1.5 mt-1 pl-2 border-l border-gray-200">
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">max_iterations</span>
                <input
                  type="number"
                  min={0}
                  value={failurePolicy.max_iterations ?? ""}
                  onChange={(e) =>
                    update({
                      failure_policy: {
                        ...failurePolicy,
                        max_iterations: e.target.value ? parseInt(e.target.value) : null,
                      },
                    })
                  }
                  placeholder="None"
                  className="border rounded px-2 py-1 text-xs"
                />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">timeout_seconds</span>
                <input
                  type="number"
                  min={0}
                  step={0.1}
                  value={failurePolicy.timeout_seconds ?? ""}
                  onChange={(e) =>
                    update({
                      failure_policy: {
                        ...failurePolicy,
                        timeout_seconds: e.target.value ? parseFloat(e.target.value) : null,
                      },
                    })
                  }
                  placeholder="None"
                  className="border rounded px-2 py-1 text-xs"
                />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[11px] font-medium text-gray-500">stagnation_threshold</span>
                <input
                  type="number"
                  min={0}
                  value={failurePolicy.stagnation_threshold ?? ""}
                  onChange={(e) =>
                    update({
                      failure_policy: {
                        ...failurePolicy,
                        stagnation_threshold: e.target.value ? parseInt(e.target.value) : null,
                      },
                    })
                  }
                  placeholder="None"
                  className="border rounded px-2 py-1 text-xs"
                />
              </label>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// -- 13-2: Upstream Inputs Inspector -----------------------------------------

function UpstreamInputsSection({ nodeId }: { nodeId: string }) {
  const graphId = useGraphStore((s) => s.graphId);
  const runId = useGraphStore((s) => s.runId);
  const edges = useGraphStore((s) => s.edges);
  const nodeOutputs = useGraphStore((s) => s.nodeOutputs);
  const [open, setOpen] = useState(false);
  const [variables, setVariables] = useState<UpstreamVariable[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Count incoming edges for this node to decide if section is relevant
  const incomingCount = useMemo(
    () => edges.filter((e) => e.target === nodeId).length,
    [edges, nodeId],
  );

  const fetchVariables = useCallback(async () => {
    if (!graphId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await getNodeInputs(graphId, nodeId, runId);
      // Enrich with live nodeOutputs where available
      const enriched = resp.variables.map((v) => {
        if (v.connected && v.source_node_id && !v.runtime_value) {
          const liveOutput = nodeOutputs[v.source_node_id];
          if (liveOutput) {
            const portVal =
              v.source_port && typeof liveOutput === "object" && v.source_port in liveOutput
                ? (liveOutput as Record<string, unknown>)[v.source_port!]
                : liveOutput;
            return { ...v, runtime_value: portVal };
          }
        }
        return v;
      });
      setVariables(enriched);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load inputs");
    } finally {
      setLoading(false);
    }
  }, [graphId, nodeId, runId, nodeOutputs]);

  useEffect(() => {
    if (open) fetchVariables();
  }, [open, fetchVariables]);

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 w-full"
      >
        <span className={`text-[10px] transition-transform ${open ? "rotate-90" : ""}`}>&#9654;</span>
        <h3 className="text-[11px] font-semibold text-gray-400 uppercase">
          Upstream Inputs
        </h3>
        {incomingCount > 0 && (
          <span className="ml-auto text-[10px] text-gray-400">{incomingCount} edge{incomingCount !== 1 ? "s" : ""}</span>
        )}
      </button>
      {open && (
        <div className="mt-1 pl-2 border-l border-gray-200">
          {loading && <p className="text-[10px] text-gray-400">Loading...</p>}
          {error && <p className="text-[10px] text-red-500">{error}</p>}
          {!loading && !error && variables.length === 0 && (
            <p className="text-[10px] text-gray-400">No input ports or incoming edges</p>
          )}
          {!loading && !error && variables.length > 0 && (
            <div className="flex flex-col gap-1.5">
              {variables.map((v, i) => (
                <div
                  key={`${v.variable_name}-${i}`}
                  className={`text-[11px] rounded border px-2 py-1.5 ${
                    !v.connected
                      ? "border-amber-300 bg-amber-50"
                      : "border-gray-200 bg-white"
                  }`}
                >
                  <div className="flex items-center gap-1">
                    <span className="font-semibold text-gray-700">{v.variable_name}</span>
                    {v.required && <span className="text-red-400 text-[9px]">*</span>}
                    <span className="ml-auto text-[10px] text-gray-400 font-mono">{v.type_hint}</span>
                  </div>
                  {v.connected ? (
                    <div className="text-[10px] text-gray-500 mt-0.5">
                      {v.edge_type === "context" ? (
                        <>ctx: {v.context_key} &larr; {v.source_node}</>
                      ) : (
                        <>{v.source_node}.{v.source_port}</>
                      )}
                    </div>
                  ) : (
                    <div className="text-[10px] text-amber-600 mt-0.5">
                      {v.required ? "Missing: no incoming edge (required)" : "No incoming edge (optional)"}
                    </div>
                  )}
                  {v.runtime_value != null && (
                    <pre className="text-[10px] bg-gray-50 border border-gray-100 rounded p-1 mt-1 overflow-auto max-h-24 font-mono whitespace-pre-wrap">
                      {typeof v.runtime_value === "string"
                        ? v.runtime_value.length > 500
                          ? v.runtime_value.slice(0, 500) + "..."
                          : v.runtime_value
                        : JSON.stringify(v.runtime_value, null, 2)?.slice(0, 500)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
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
  const graphId = useGraphStore((s) => s.graphId);
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
      ([k]) =>
        !SKIP_FIELDS.has(k) &&
        !(BODY_GRAPH_DEDICATED_TYPES.has(d.node_type) && k === "body_graph") &&
        !(d.node_type === "goal_loop" && GOAL_LOOP_DEDICATED_FIELDS.has(k)) &&
        !(d.node_type === "vote" && VOTE_DEDICATED_FIELDS.has(k)) &&
        !(d.node_type === "reflection" && REFLECTION_DEDICATED_FIELDS.has(k)) &&
        !(d.node_type === "gate" && GATE_DEDICATED_FIELDS.has(k)) &&
        !(d.node_type === "parallel_subagents" && PARALLEL_SUBAGENTS_DEDICATED_FIELDS.has(k)) &&
        !(d.node_type === "orchestrator" && ORCHESTRATOR_DEDICATED_FIELDS.has(k)) &&
        !(d.node_type === "agent_team" && AGENT_TEAM_DEDICATED_FIELDS.has(k)) &&
        ((d.node_type !== "human" && d.node_type !== "human_in_the_loop") || !HUMAN_DEDICATED_FIELDS.has(k)),
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

        {/* 13-2: Upstream Inputs Inspector */}
        <UpstreamInputsSection nodeId={d.id} />

        {/* 7-2: Dedicated LLM config section — model, temperature, system_prompt, advanced */}
        {(d.node_type === "llm_operator" || d.node_type === "router") && (
          <LLMConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

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

        {/* 9-1: RAG config — collection, top_k, query_template, etc. */}
        {d.node_type === "rag_operator" && (
          <RAGConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

        {/* 9-3: Validator config — rules, on_failure, strict_mode */}
        {d.node_type === "validator" && (
          <ValidatorConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

        {/* 7-9: Parallel Subagents config — branch_graphs, input_mappings, merge_strategy, parallelism, failure_policy */}
        {d.node_type === "parallel_subagents" && (
          <ParallelSubagentsConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

        {d.node_type === "orchestrator" && (
          <OrchestratorConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

        {d.node_type === "agent_team" && (
          <AgentTeamConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

        {(d.node_type === "while_loop" || d.node_type === "for_each" || d.node_type === "composite") && (
          <BodyGraphConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

        {d.node_type === "goal_loop" && (
          <GoalLoopConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

        {(d.node_type === "human" || d.node_type === "human_in_the_loop") && (
          <HumanConfigSection
            nodeId={d.id}
            data={d as unknown as Record<string, unknown>}
            includeAdvanced={d.node_type === "human"}
          />
        )}

        {d.node_type === "vote" && (
          <VoteConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

        {d.node_type === "reflection" && (
          <ReflectionConfigSection nodeId={d.id} data={d as unknown as Record<string, unknown>} />
        )}

        {/* 7-1: Retry policy — configurable for all node types */}
        <RetryPolicyEditor nodeId={d.id} policy={d.retry_policy} />

        {/* 13-2: Node Test Cases */}
        {graphId && (
          <TestCaseSection
            workflowId={graphId}
            nodeId={d.id}
            inputPorts={d.input_ports}
          />
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
