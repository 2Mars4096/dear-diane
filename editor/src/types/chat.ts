export interface ToolCallInfo {
  id: string;
  toolName: string;
  argsPreview: string;
  status: "running" | "success" | "error";
  outputPreview?: string;
  durationMs?: number;
}

export interface ChatAttachment {
  path?: string;
  filename: string;
  size?: number;
  mimeType?: string;
  kind?: "file" | "figure";
  caption?: string;
  source?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  tokenUsage?: { prompt: number; completion: number } | null;
  estimatedCost?: number | null;
  mutationPlan?: unknown | null;
  dryRunResult?: Record<string, unknown> | null;
  mutationId?: string | null;
  mutationStatus?: "proposed" | "applied" | "partial" | "rejected" | "reverted" | null;
  runRef?: {
    runId: string;
    scope: string;
    status: string;
    targetNodeId?: string;
    targetSubgraphKey?: string;
  } | null;
  mentions?: Array<{ name: string; type: string; id: string }>;
  toolCalls?: ToolCallInfo[];
  runEvents?: RunEventPayload[];
  attachments?: ChatAttachment[];
  progressStatus?: string;
  progressFilePath?: string;
}

export interface ChatThread {
  id: string;
  workflow_id: string;
  title: string;
  messages: ChatMessage[];
  created_at: string;
  updated_at: string;
}

export interface RunEventPayload {
  type: string;
  event_type: string;
  node_id?: string | null;
  summary: string;
  detail?: Record<string, unknown>;
}

export interface ChatStreamEvent {
  type:
    | "chat_token"
    | "chat_complete"
    | "chat_queued"
    | "chat_error"
    | "chat_mutation"
    | "chat_run_event"
    | "chat_interrupted"
    | "chat_tool_call_start"
    | "chat_tool_call_result"
    | "chat_graph_created"
    | "chat_validation_result"
    | "chat_file_attachment"
    | "chat_injected_message"
    | "ping";
  delta?: string;
  accumulated?: string;
  message_id?: string;
  content?: string;
  token_usage?: { prompt: number; completion: number };
  estimated_cost?: number | null;
  context_window?: number;
  graph_revision?: string;
  revision_mismatch?: boolean;
  error?: string;
  mutation_plan?: Record<string, unknown>;
  dry_run_result?: Record<string, unknown>;
  run_event?: RunEventPayload;
  tool_call_id?: string;
  tool_name?: string;
  args_preview?: string;
  status?: string;
  output_preview?: string;
  duration_ms?: number;
  detected_mode?: string;
  stream_channel_id?: string;
  correlation_id?: string;
  queue_position?: number;
  success?: boolean;
  errors?: string[];
  path?: string;
  filename?: string;
  size?: number;
  inject_id?: string;
}
