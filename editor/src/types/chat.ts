export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  tokenUsage?: { prompt: number; completion: number } | null;
  mutationPlan?: unknown | null;
  mutationId?: string | null;
  mutationStatus?: "proposed" | "applied" | "partial" | "rejected" | "reverted" | null;
  runRef?: { runId: string; scope: string; status: string } | null;
  mentions?: Array<{ name: string; type: string; id: string }>;
}

export interface ChatThread {
  id: string;
  workflow_id: string;
  title: string;
  messages: ChatMessage[];
  created_at: string;
  updated_at: string;
}

export interface ChatStreamEvent {
  type: "chat_token" | "chat_complete" | "chat_error" | "chat_mutation";
  delta?: string;
  accumulated?: string;
  message_id?: string;
  content?: string;
  token_usage?: { prompt: number; completion: number };
  graph_revision?: string;
  revision_mismatch?: boolean;
  error?: string;
  mutation_plan?: Record<string, unknown>;
  dry_run_result?: Record<string, unknown>;
}
