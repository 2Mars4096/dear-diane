/**
 * TypeScript types mirroring the Python dan_graph_v1 models.
 * Source of truth: src/dan/models/ (Python Pydantic models).
 */

// -- Ports ------------------------------------------------------------------

export interface InputPort {
  name: string;
  schema: Record<string, unknown>;
  required?: boolean;
  description?: string;
}

export interface OutputPort {
  name: string;
  schema: Record<string, unknown>;
  description?: string;
}

// -- Position / UI -----------------------------------------------------------

export interface Position {
  x: number;
  y: number;
}

// -- Retry Policy ------------------------------------------------------------

export interface RetryPolicy {
  max_retries?: number;
  backoff?: number;
  backoff_max?: number;
  fallback_model?: string | null;
  on_failure?: "error" | "skip" | "halt";
}

// -- Nodes -------------------------------------------------------------------

export interface NodeBase {
  id: string;
  node_type: string;
  name: string;
  description?: string;
  input_ports: InputPort[];
  output_ports: OutputPort[];
  position: Position;
  ui: Record<string, unknown>;
  metadata: Record<string, unknown>;
  retry_policy?: RetryPolicy | null;
}

export interface LLMOperator extends NodeBase {
  node_type: "llm_operator";
  model: string;
  prompt_template: string;
  system_prompt?: string;
  temperature?: number;
  max_tokens?: number | null;
  output_json_schema?: Record<string, unknown> | null;
}

export interface ToolOperator extends NodeBase {
  node_type: "tool_operator";
  tool_id: string;
  tool_config: Record<string, unknown>;
}

export interface CodeOperator extends NodeBase {
  node_type: "code_operator";
  code: string;
  language?: string;
  sandbox_config: Record<string, unknown>;
}

export interface IfElseNode extends NodeBase {
  node_type: "if_else";
  condition: string;
}

export interface GateNode extends NodeBase {
  node_type: "gate";
  gate_mode: "if_else" | "while";
  condition: string;
  max_iterations?: number;
  state_schema?: Record<string, unknown> | null;
  state_defaults?: Record<string, unknown> | null;
}

export interface WhileLoopNode extends NodeBase {
  node_type: "while_loop";
  condition: string;
  body_graph: string;
  max_iterations?: number;
  external_input_schema?: Record<string, unknown> | null;
  external_output_schema?: Record<string, unknown> | null;
}

export interface ForEachNode extends NodeBase {
  node_type: "for_each";
  body_graph: string;
  parallelism?: number;
  merge_strategy?: string;
  external_input_schema?: Record<string, unknown> | null;
  external_output_schema?: Record<string, unknown> | null;
}

export interface ReduceNode extends NodeBase {
  node_type: "reduce";
  reducer: string;
}

export interface RouterNode extends NodeBase {
  node_type: "router";
  model: string;
  route_descriptions: Record<string, string>;
}

export interface HumanInTheLoopNode extends NodeBase {
  node_type: "human_in_the_loop";
  prompt?: string;
  timeout_seconds?: number | null;
  default_action?: string | null;
}

export interface CompositeNode extends NodeBase {
  node_type: "composite";
  body_graph: string;
  input_mappings: Record<string, string>;
  output_mappings: Record<string, string>;
  is_blackbox?: boolean;
  external_input_schema?: Record<string, unknown> | null;
  external_output_schema?: Record<string, unknown> | null;
}

export interface ParallelSubagentsNode extends NodeBase {
  node_type: "parallel_subagents";
  branch_graphs: string[];
  input_mappings: Record<string, string>;
  branch_inputs?: Record<string, Record<string, string>>;
  merge_strategy?: string;
  reducer?: string | null;
  parallelism?: number;
  failure_policy?: { max_iterations?: number | null; timeout_seconds?: number | null; stagnation_threshold?: number | null };
}

export interface RagOperator extends NodeBase {
  node_type: "rag_operator";
  collection: string;
  top_k: number;
  similarity_threshold?: number | null;
  embedding_model?: string;
  vector_store_config?: Record<string, unknown>;
  query_template: string;
  include_metadata: boolean;
  rerank: boolean;
}

export interface ValidationRule {
  rule_type: "required_keys" | "non_empty" | "schema_conformance" | "type_check" | "custom_expression";
  config: Record<string, unknown>;
}

export interface ValidatorNode extends NodeBase {
  node_type: "validator";
  validation_rules: ValidationRule[];
  on_failure: "route" | "warn" | "halt";
  strict_mode: boolean;
}

export interface InputVariable {
  name: string;
  type: "string" | "number" | "boolean";
  default: unknown;
  description: string;
}

export interface InputNodeType extends NodeBase {
  node_type: "input";
  variables: InputVariable[];
}

export type DanNode =
  | LLMOperator
  | ToolOperator
  | CodeOperator
  | IfElseNode
  | GateNode
  | WhileLoopNode
  | ForEachNode
  | ReduceNode
  | RouterNode
  | HumanInTheLoopNode
  | CompositeNode
  | ParallelSubagentsNode
  | RagOperator
  | ValidatorNode
  | InputNodeType;

// -- Edges -------------------------------------------------------------------

export interface EdgeBase {
  id: string;
  edge_type: string;
  source_node_id: string;
  source_port: string;
  target_node_id: string;
  target_port: string;
  ui: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface DataEdge extends EdgeBase {
  edge_type: "data";
  spread?: boolean;
}

export interface ControlEdge extends EdgeBase {
  edge_type: "control";
  condition?: string | null;
}

export interface ContextEdge extends EdgeBase {
  edge_type: "context";
  context_key: string;
  mode: "read" | "write" | "append";
}

export type DanEdge = DataEdge | ControlEdge | ContextEdge;

// -- Loop Groups (visual-only metadata) --------------------------------------

export interface LoopGroup {
  id: string;
  label: string;
  gateNodeId: string;
  memberNodeIds: string[];
  collapsed: boolean;
}

// -- Graph -------------------------------------------------------------------

export interface GraphMetadata {
  name: string;
  description?: string;
  created_at?: string | null;
  updated_at?: string | null;
  tags?: string[];
  loop_groups?: LoopGroup[];
}

export interface SharedContextDeclaration {
  key: string;
  schema: Record<string, unknown>;
  description?: string;
}

export interface DanGraph {
  version: string;
  metadata: GraphMetadata;
  nodes: DanNode[];
  edges: DanEdge[];
  sub_graphs: Record<string, DanGraph>;
  entry_points: string[];
  exit_points: string[];
  shared_context: SharedContextDeclaration[];
  artifact_refs: unknown[];
}

// -- Node type catalogue (for palette) ---------------------------------------

export const NODE_TYPE_CATALOG = [
  { type: "llm_operator", label: "LLM Operator", category: "operator" },
  { type: "tool_operator", label: "Tool Operator", category: "operator" },
  { type: "code_operator", label: "Code Operator", category: "operator" },
  { type: "input", label: "Input", category: "io" },
  { type: "gate_if_else", label: "If/Else Gate", category: "control" },
  { type: "gate_while", label: "While Gate", category: "control" },
  { type: "for_each", label: "For Each", category: "control" },
  { type: "parallel_subagents", label: "Parallel Subagents", category: "control" },
  { type: "reduce", label: "Reduce", category: "control" },
  { type: "router", label: "Router", category: "control" },
  { type: "human_in_the_loop", label: "Human in the Loop", category: "control" },
  { type: "rag_operator", label: "RAG Operator", category: "operator" },
  { type: "validator", label: "Validator", category: "control" },
  { type: "composite", label: "Composite", category: "composite" },
] as const;

export type NodeTypeString = (typeof NODE_TYPE_CATALOG)[number]["type"];

// -- 5-4: Build palette — node descriptions for hover previews ----------------

export const NODE_DESCRIPTIONS: Record<
  string,
  { description: string; inputs: string[]; outputs: string[] }
> = {
  llm_operator: {
    description: "Call an LLM with a prompt template",
    inputs: ["input"],
    outputs: ["output"],
  },
  tool_operator: {
    description: "Execute a registered tool function",
    inputs: ["input"],
    outputs: ["result"],
  },
  code_operator: {
    description: "Run sandboxed Python code",
    inputs: ["input"],
    outputs: ["result"],
  },
  gate_if_else: {
    description: "Route data to true or false branch based on a condition",
    inputs: ["input"],
    outputs: ["true", "false"],
  },
  gate_while: {
    description: "Loop gate — continue (loop back) or done (exit) based on condition",
    inputs: ["input"],
    outputs: ["continue", "done"],
  },
  for_each: {
    description: "Fan out a sub-graph over list items",
    inputs: ["items"],
    outputs: ["results"],
  },
  parallel_subagents: {
    description: "Run multiple sub-graphs concurrently; merge at fan-in",
    inputs: ["input"],
    outputs: ["results"],
  },
  reduce: {
    description: "Aggregate outputs from parallel branches",
    inputs: ["input"],
    outputs: ["result"],
  },
  router: {
    description: "LLM-powered dynamic routing",
    inputs: ["input"],
    outputs: ["route", "output"],
  },
  human_in_the_loop: {
    description: "Pause for human input",
    inputs: ["input"],
    outputs: ["response"],
  },
  rag_operator: {
    description: "Retrieve relevant chunks from a vector store",
    inputs: ["query"],
    outputs: ["chunks", "scores"],
  },
  validator: {
    description: "Validate data with configurable rules (valid/invalid routing)",
    inputs: ["data"],
    outputs: ["valid", "invalid"],
  },
  composite: {
    description: "Reusable sub-graph block",
    inputs: ["input"],
    outputs: ["output"],
  },
  input: {
    description: "Visual entry point for workflow inputs",
    inputs: [],
    outputs: ["input"],
  },
};
