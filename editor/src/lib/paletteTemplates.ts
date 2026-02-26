// -- 5-4: Build palette — pre-defined agent template factories ----------------

import type { DanNode, DanGraph, DanEdge, Position } from "../types/graph";

export interface TemplateResult {
  node: DanNode;
  rootSubGraphKey: string;
  subGraphs: Record<string, DanGraph>;
}

export interface PaletteTemplate {
  id: string;
  label: string;
  description: string;
  factory: (position: Position) => TemplateResult;
}

function uid(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
}

function emptySubGraph(overrides?: Partial<DanGraph>): DanGraph {
  return {
    version: "dan_graph_v1",
    metadata: { name: "" },
    nodes: [],
    edges: [],
    sub_graphs: {},
    entry_points: [],
    exit_points: [],
    shared_context: [],
    artifact_refs: [],
    ...overrides,
  };
}

// -- ReAct: Think → Act loop (Composite with GateNode while-loop body) ---------

function reactTemplateFactory(position: Position): TemplateResult {
  const bodyKey = `react_body_${Date.now()}`;
  const llmId = uid("react_llm");
  const toolId = uid("react_tool");
  const gateId = uid("react_gate");

  const llmNode: DanNode = {
    id: llmId,
    node_type: "llm_operator",
    name: "Think / Reason",
    description: "LLM reasoning step",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [{ name: "text", schema: {} }],
    position: { x: 100, y: 80 },
    ui: {},
    metadata: {},
    model: "",
    prompt_template: "Given the input, reason about the next action to take.",
    system_prompt:
      "You are a ReAct agent. Think step-by-step, then decide on an action.",
    temperature: 0.7,
    max_tokens: null,
    output_json_schema: null,
  };

  const toolNode: DanNode = {
    id: toolId,
    node_type: "tool_operator",
    name: "Act",
    description: "Execute tool action",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [{ name: "result", schema: {} }],
    position: { x: 100, y: 200 },
    ui: {},
    metadata: {},
    tool_id: "",
    tool_config: {},
  };

  const gateNode: DanNode = {
    id: gateId,
    node_type: "gate",
    name: "Loop Gate",
    description: "Continue or done",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [
      { name: "continue", schema: {} },
      { name: "done", schema: {} },
    ],
    position: { x: 100, y: 320 },
    ui: {},
    metadata: {},
    gate_mode: "while",
    condition: "True",
    max_iterations: 10,
  };

  const bodyEdges: DanEdge[] = [
    {
      id: `e-${llmId}-${toolId}`,
      edge_type: "data",
      source_node_id: llmId,
      source_port: "text",
      target_node_id: toolId,
      target_port: "input",
      ui: {},
      metadata: {},
    },
    {
      id: `e-${toolId}-${gateId}`,
      edge_type: "data",
      source_node_id: toolId,
      source_port: "result",
      target_node_id: gateId,
      target_port: "input",
      ui: {},
      metadata: {},
    },
    {
      id: `e-${gateId}-${llmId}`,
      edge_type: "data",
      source_node_id: gateId,
      source_port: "continue",
      target_node_id: llmId,
      target_port: "input",
      ui: {},
      metadata: {},
    },
  ];

  const bodyGraph = emptySubGraph({
    metadata: { name: "ReAct Loop Body" },
    nodes: [llmNode, toolNode, gateNode],
    edges: bodyEdges,
    entry_points: [llmId],
    exit_points: [gateId],
  });

  const compositeId = uid("react");
  const compositeNode: DanNode = {
    id: compositeId,
    node_type: "composite",
    name: "ReAct Agent",
    description: "ReAct: Think → Act loop until done",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [{ name: "output", schema: {} }],
    position,
    ui: {},
    metadata: {},
    body_graph: bodyKey,
    input_mappings: { input: "input" },
    output_mappings: { done: "output" },
  };

  return {
    node: compositeNode,
    rootSubGraphKey: bodyKey,
    subGraphs: { [bodyKey]: bodyGraph },
  };
}

// -- Plan-Execute: planner → executor chain -----------------------------------

function planExecuteTemplateFactory(position: Position): TemplateResult {
  const bodyKey = `plan_execute_body_${Date.now()}`;
  const plannerId = uid("pe_planner");
  const executorId = uid("pe_executor");

  const plannerNode: DanNode = {
    id: plannerId,
    node_type: "llm_operator",
    name: "Planner",
    description: "Generate execution plan",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [{ name: "text", schema: {} }],
    position: { x: 100, y: 80 },
    ui: {},
    metadata: {},
    model: "",
    prompt_template: "Break down the task into a step-by-step plan.",
    system_prompt:
      "You are a planning agent. Create a detailed execution plan.",
    temperature: 0.7,
    max_tokens: null,
    output_json_schema: null,
  };

  const executorNode: DanNode = {
    id: executorId,
    node_type: "tool_operator",
    name: "Executor",
    description: "Execute each plan step",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [{ name: "result", schema: {} }],
    position: { x: 100, y: 260 },
    ui: {},
    metadata: {},
    tool_id: "",
    tool_config: {},
  };

  const bodyEdge: DanEdge = {
    id: `e-${plannerId}-${executorId}`,
    edge_type: "data",
    source_node_id: plannerId,
    source_port: "text",
    target_node_id: executorId,
    target_port: "input",
    ui: {},
    metadata: {},
  };

  const bodyGraph = emptySubGraph({
    metadata: { name: "Plan-Execute Body" },
    nodes: [plannerNode, executorNode],
    edges: [bodyEdge],
    entry_points: [plannerId],
    exit_points: [executorId],
  });

  const compositeId = uid("plan_execute");
  const compositeNode: DanNode = {
    id: compositeId,
    node_type: "composite",
    name: "Plan-Execute",
    description: "Plan then execute step-by-step",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [{ name: "output", schema: {} }],
    position,
    ui: {},
    metadata: {},
    body_graph: bodyKey,
    input_mappings: { input: "input" },
    output_mappings: { result: "output" },
  };

  return {
    node: compositeNode,
    rootSubGraphKey: bodyKey,
    subGraphs: { [bodyKey]: bodyGraph },
  };
}

// -- Template registry --------------------------------------------------------

export const PREDEFINED_AGENT_TEMPLATES: PaletteTemplate[] = [
  {
    id: "react",
    label: "ReAct Agent",
    description: "Think → Act loop with LLM reasoning and tool execution",
    factory: reactTemplateFactory,
  },
  {
    id: "plan_execute",
    label: "Plan-Execute",
    description: "LLM planner generates steps, executor runs each one",
    factory: planExecuteTemplateFactory,
  },
];
