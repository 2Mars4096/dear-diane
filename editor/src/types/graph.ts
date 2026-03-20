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

export interface HumanNodeType extends NodeBase {
  node_type: "human";
  prompt?: string;
  timeout_seconds?: number | null;
  default_action?: string | null;
  input_schema?: Record<string, unknown> | null;
  output_schema?: Record<string, unknown> | null;
  render_mode?: "text" | "approval" | "form" | "selection" | "file_upload" | "rich";
  options?: string[] | null;
  instructions?: string;
  render_target?: "dialog" | "chat" | "both";
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

export interface OrchestratorNodeType extends NodeBase {
  node_type: "orchestrator";
  teams: Record<string, string>;
  orchestrator_prompt?: string;
  orchestrator_model?: string | null;
  completion_condition?: "all_done" | "any_done" | "orchestrator_halt";
  max_iterations?: number;
  max_llm_calls?: number;
  timeout_seconds?: number | null;
  input_mappings?: Record<string, string>;
  team_inputs?: Record<string, Record<string, unknown>>;
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

export interface ReflectionNodeType extends NodeBase {
  node_type: "reflection";
  reflection_prompt?: string;
  reflection_model?: string | null;
  source?: "last_run" | "last_n_runs" | "error_index";
  source_config?: Record<string, unknown>;
  output_format?: "principles" | "rules" | "summary";
  max_principles?: number;
  min_confidence?: number;
  dedup_strategy?: "embedding_similarity" | "exact_key" | "none";
}

export interface GoalLoopNodeType extends NodeBase {
  node_type: "goal_loop";
  goal_text: string;
  metric_name?: string;
  target_value?: number;
  comparison?: ">=" | "<=" | "==" | ">" | "<";
  max_iterations?: number;
  evaluator?: string;
  success_criteria?: string | null;
  body_graph: string;
  external_input_schema?: Record<string, unknown> | null;
  external_output_schema?: Record<string, unknown> | null;
}

export interface VoteConfig {
  judge_model?: string | null;
  judge_prompt?: string | null;
  quality_metric?: string | null;
  unanimity_threshold?: number;
  consensus_mode?: "whole" | "field";
}

export interface VoteNodeType extends NodeBase {
  node_type: "vote";
  candidates: string[];
  num_votes: number;
  prompt_template: string;
  system_prompt?: string;
  temperature?: number;
  output_json_schema?: Record<string, unknown> | null;
  vote_strategy?: "majority" | "weighted" | "best_of_n" | "judge" | "unanimous";
  vote_config?: VoteConfig | null;
  parallelism?: number;
  timeout_seconds?: number | null;
}

export interface AgentTeamNodeType extends NodeBase {
  node_type: "agent_team";
  agents: Record<string, string>;
  moderator_prompt?: string;
  moderator_model?: string | null;
  turn_strategy?: "round_robin" | "moderator" | "free_form" | "sequential";
  max_turns?: number;
  completion_condition?: "consensus" | "moderator_halt" | "max_turns" | "all_responded";
  timeout_seconds?: number | null;
  shared_context_keys?: string[];
  handoff_policy?: "explicit" | "any" | "moderator_only";
  input_mappings?: Record<string, string>;
  agent_inputs?: Record<string, Record<string, unknown>>;
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
  | HumanNodeType
  | HumanInTheLoopNode
  | CompositeNode
  | ParallelSubagentsNode
  | OrchestratorNodeType
  | RagOperator
  | ValidatorNode
  | ReflectionNodeType
  | GoalLoopNodeType
  | VoteNodeType
  | AgentTeamNodeType
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
  { type: "goal_loop", label: "Goal Loop", category: "control" },
  { type: "parallel_subagents", label: "Parallel Subagents", category: "control" },
  { type: "orchestrator", label: "Orchestrator", category: "control" },
  { type: "agent_team", label: "Agent Team", category: "control" },
  { type: "reduce", label: "Reduce", category: "control" },
  { type: "router", label: "Router", category: "control" },
  { type: "human", label: "Human", category: "control" },
  { type: "vote", label: "Vote", category: "control" },
  { type: "rag_operator", label: "RAG Operator", category: "operator" },
  { type: "reflection", label: "Reflection", category: "operator" },
  { type: "validator", label: "Validator", category: "control" },
  { type: "composite", label: "Composite", category: "composite" },
] as const;

// Palette node types are authoring-surface labels. Some are runtime `node_type`
// strings directly, while others lower to canonical runtime nodes
// (e.g. `gate_if_else` -> `gate` + `gate_mode="if_else"`).
export type PaletteNodeType = (typeof NODE_TYPE_CATALOG)[number]["type"];

// Backward-compatible alias kept while the editor transitions to the more
// explicit `PaletteNodeType` name.
export type NodeTypeString = PaletteNodeType;

export type RuntimeNodeType = DanNode["node_type"];

export const PALETTE_NODE_TO_RUNTIME_NODE_TYPE: Record<PaletteNodeType, RuntimeNodeType> = {
  llm_operator: "llm_operator",
  tool_operator: "tool_operator",
  code_operator: "code_operator",
  input: "input",
  gate_if_else: "gate",
  gate_while: "gate",
  for_each: "for_each",
  goal_loop: "goal_loop",
  parallel_subagents: "parallel_subagents",
  orchestrator: "orchestrator",
  agent_team: "agent_team",
  reduce: "reduce",
  router: "router",
  human: "human",
  rag_operator: "rag_operator",
  vote: "vote",
  reflection: "reflection",
  validator: "validator",
  composite: "composite",
};

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
  goal_loop: {
    description: "Iterate a body sub-graph until a goal metric reaches a target",
    inputs: ["input"],
    outputs: ["result", "goal_met", "iterations", "best_score"],
  },
  parallel_subagents: {
    description: "Run multiple sub-graphs concurrently; merge at fan-in",
    inputs: ["input"],
    outputs: ["results"],
  },
  orchestrator: {
    description: "Coordinate multiple team sub-graphs concurrently",
    inputs: ["input"],
    outputs: ["results"],
  },
  agent_team: {
    description: "Group-chat style multi-agent collaboration with turn routing",
    inputs: ["input"],
    outputs: ["result", "conversation"],
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
  human: {
    description: "Pause for human input",
    inputs: ["input"],
    outputs: ["response"],
  },
  human_in_the_loop: {
    description: "Legacy alias for human input",
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
  vote: {
    description: "Run multiple candidates and choose the best result",
    inputs: ["input"],
    outputs: ["winner", "winner_model"],
  },
  reflection: {
    description: "Analyze prior runs and extract reusable principles",
    inputs: ["input"],
    outputs: ["principles", "text"],
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

// -- 18-5: Model tier info (from model_selected event) ----------------------

export interface TierInfo {
  tier: "micro" | "routine" | "reasoning" | "critical";
  tier_score: number;
  difficulty: number;
  impact: number;
  recoverability: number;
  model: string;
}

// -- 18-4: Token Analytics types --------------------------------------------

export interface TokenBreakdown {
  total_input_tokens: number;
  total_output_tokens: number;
  system_tokens?: number;
  user_tokens?: number;
  context_edge_tokens?: number;
  hyperedge_tokens?: number;
  memory_tokens?: number;
  rag_tokens?: number;
  assistant_tokens?: number;
  output_tokens?: number;
  cache_hit?: boolean;
  model?: string;
}

export interface TokenBreakdownResponse {
  run_id: string;
  nodes: Record<string, TokenBreakdown>;
  run_totals: {
    total_input_tokens: number;
    total_output_tokens: number;
    total_cost: number;
  };
}

export interface WasteFinding {
  category: string;
  node_id: string;
  description: string;
  estimated_saveable_tokens: number;
  suggestion: string;
  edge_id?: string;
}

export interface OptimizationReport {
  findings: WasteFinding[];
  total_waste_tokens: number;
  total_tokens_analyzed: number;
  waste_percentage: number;
}

export interface OptimizationReportResponse {
  run_id: string;
  report: OptimizationReport;
}

export interface OptimizationMutation {
  graph_id: string;
  mutation: Record<string, unknown>;
  mutation_plan: Record<string, unknown>;
  apply_request: Record<string, unknown>;
  finding: WasteFinding;
}

export interface OptimizationMutationsResponse {
  run_id: string;
  mutations: OptimizationMutation[];
  count: number;
}
