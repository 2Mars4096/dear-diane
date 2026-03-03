/**
 * API client for the DAN backend server.
 */

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

// -- Graph CRUD --------------------------------------------------------------

export interface GraphListItem {
  graph_id: string;
  name: string;
  description: string;
  updated_at: string | null;
}

export const listGraphs = () =>
  request<{ graphs: GraphListItem[]; last_opened: string | null }>("/graphs");

export const getGraph = (id: string, options?: { layout?: boolean }) =>
  request<{ graph_id: string; data: Record<string, unknown> }>(
    `/graphs/${id}${options?.layout ? "?layout=true" : ""}`,
  );

export const createGraph = (graphId: string, data?: Record<string, unknown>) =>
  request<{ graph_id: string; data: Record<string, unknown> }>("/graphs", {
    method: "POST",
    body: JSON.stringify({ graph_id: graphId, data }),
  });

export const updateGraph = (id: string, data: Record<string, unknown>) =>
  request<{ graph_id: string; status: string }>(`/graphs/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });

export interface ApplyMutationResult {
  success: boolean;
  new_graph: Record<string, unknown> | null;
  errors: Array<{ op_index?: number; op_type?: string; message?: string }>;
  warnings?: string[];
  diagnostics?: string[];
  stale_plan?: boolean;
}

export const applyMutation = (
  graphId: string,
  mutationPlan: Record<string, unknown>,
) =>
  request<ApplyMutationResult>(`/graphs/${graphId}/apply-mutation`, {
    method: "POST",
    body: JSON.stringify({ mutation_plan: mutationPlan }),
  });

export const deleteGraph = (id: string) =>
  request<{ graph_id: string; status: string }>(`/graphs/${id}`, {
    method: "DELETE",
  });

// -- Validation --------------------------------------------------------------

export interface ValidationIssue {
  node_id?: string;
  edge_id?: string;
  message: string;
}

export interface ValidationResult {
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export const validateGraph = (graphId: string) =>
  request<ValidationResult>(`/graphs/${graphId}/validate`, { method: "POST" });

// -- Boundary validators -----------------------------------------------------

export const addBoundaryValidators = (graphId: string, nodeId: string) =>
  request<{ graph_id: string; node_id: string; status: string }>(
    `/graphs/${graphId}/nodes/${nodeId}/add-boundary-validators`,
    { method: "POST" },
  );

// -- Runs --------------------------------------------------------------------

export interface RunInfo {
  run_id: string;
  graph_id: string;
  status: string;
  node_statuses: Record<string, string>;
  started_at: number;
  finished_at: number | null;
  success: boolean | null;
  errors: Record<string, string>;
  outputs: Record<string, unknown>;
}

export const startRun = (graphId: string, inputs?: Record<string, unknown>) =>
  request<{ run_id: string; status: string }>("/runs", {
    method: "POST",
    body: JSON.stringify({ graph_id: graphId, inputs }),
  });

export const resumeRun = (runId: string, graphId: string) =>
  request<{ run_id: string; status: string }>(`/runs/${runId}/resume`, {
    method: "POST",
    body: JSON.stringify({ graph_id: graphId }),
  });

export const getRun = (runId: string) =>
  request<RunInfo>(`/runs/${runId}`);

export const submitHumanInput = (
  runId: string,
  requestId: string,
  nodeId: string,
  response: string,
) =>
  request<{ status: string; request_id: string }>(
    `/runs/${runId}/human-input`,
    {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, node_id: nodeId, response }),
    },
  );

// -- Run history & comparison ------------------------------------------------

export interface RunListFilters {
  workflow_id?: string;
  status?: string;
  after?: number;
  before?: number;
  limit?: number;
  offset?: number;
}

export interface RunSummary extends RunInfo {
  total_prompt_tokens?: number;
  total_completion_tokens?: number;
  total_tokens?: number;
  total_cost?: number | null;
  elapsed_seconds?: number | null;
  node_usage?: Record<string, Record<string, number>>;
}

export interface RunListResponse {
  runs: RunSummary[];
  total: number;
}

export const listRuns = (filters: RunListFilters = {}) => {
  const params = new URLSearchParams();
  if (filters.workflow_id) params.set("workflow_id", filters.workflow_id);
  if (filters.status) params.set("status", filters.status);
  if (filters.after != null) params.set("after", String(filters.after));
  if (filters.before != null) params.set("before", String(filters.before));
  if (filters.limit != null) params.set("limit", String(filters.limit));
  if (filters.offset != null) params.set("offset", String(filters.offset));
  const qs = params.toString();
  return request<RunListResponse>(`/runs${qs ? `?${qs}` : ""}`);
};

export interface RunEventsResponse {
  events: Array<Record<string, unknown>>;
  source: "live" | "persisted" | "memory";
}

export const getRunEvents = (
  runId: string,
  filters?: { node_id?: string; event_type?: string },
) => {
  const params = new URLSearchParams();
  if (filters?.node_id) params.set("node_id", filters.node_id);
  if (filters?.event_type) params.set("event_type", filters.event_type);
  const qs = params.toString();
  return request<RunEventsResponse>(`/runs/${runId}/events${qs ? `?${qs}` : ""}`);
};

export interface NodeDiff {
  node_id: string;
  status_a: string | null;
  status_b: string | null;
  status_changed: boolean;
  tokens_a: number;
  tokens_b: number;
  token_delta: number;
  prompt_tokens_a: number;
  prompt_tokens_b: number;
  completion_tokens_a: number;
  completion_tokens_b: number;
}

export interface CompareRunsResponse {
  run_a: RunSummary;
  run_b: RunSummary;
  summary: {
    elapsed_delta: number;
    token_delta: number;
    cost_delta: number;
    status_a: string;
    status_b: string;
  };
  node_diffs: NodeDiff[];
}

export const compareRuns = (runA: string, runB: string) =>
  request<CompareRunsResponse>(
    `/runs/compare?run_a=${encodeURIComponent(runA)}&run_b=${encodeURIComponent(runB)}`,
  );

// -- Mention context ---------------------------------------------------------

export const listWorkspaceFiles = () =>
  request<{ files: string[] }>("/files/list");

export const listDocs = () =>
  request<{ docs: string[] }>("/docs/list");

export const listCodeRefs = (workflowId: string) =>
  request<{ refs: string[] }>(`/code-refs/${workflowId}`);

// -- Chat --------------------------------------------------------------------

export interface ChatMessageResponse {
  message_id: string;
  stream_channel_id: string;
}

export const sendChatMessage = (
  workflowId: string,
  message: string,
  history: Array<{ role: string; content: string }> = [],
  threadId?: string | null,
  clientGraphRevision?: string | null,
  mode: "ask" | "agent" | "plan" | "debug" = "agent",
) =>
  request<ChatMessageResponse>("/chat/message", {
    method: "POST",
    body: JSON.stringify({
      workflow_id: workflowId,
      message,
      history,
      thread_id: threadId,
      client_graph_revision: clientGraphRevision,
      mode,
    }),
  });

export interface ChatThreadSummary {
  id: string;
  title: string;
  workflow_id: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  pinned?: boolean;
}

export const listChatThreads = (workflowId: string) =>
  request<{ threads: ChatThreadSummary[] }>(`/chats/${workflowId}`);

export const getChatThread = (workflowId: string, threadId: string) =>
  request<Record<string, unknown>>(`/chats/${workflowId}/${threadId}`);

export const createChatThread = (workflowId: string, title?: string) =>
  request<Record<string, unknown>>(`/chats/${workflowId}`, {
    method: "POST",
    body: JSON.stringify({ title }),
  });

export const updateChatThread = (
  workflowId: string,
  threadId: string,
  body: { title?: string; messages?: unknown[] },
) =>
  request<{ status: string }>(`/chats/${workflowId}/${threadId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const deleteChatThread = (workflowId: string, threadId: string) =>
  request<{ status: string }>(`/chats/${workflowId}/${threadId}`, {
    method: "DELETE",
  });

export const stopChatStream = (channelId: string, messageId?: string) =>
  request<{ status: string; channel_id: string }>(
    `/chat/${channelId}/stop`,
    {
      method: "POST",
      body: JSON.stringify({ message_id: messageId ?? null }),
    },
  );

export const exportChatThread = (
  workflowId: string,
  threadId: string,
  format: "md" | "json" = "md",
) =>
  request<{ content: string; format: string }>(
    `/chats/${workflowId}/${threadId}/export?format=${format}`,
  );

export const searchChatThreads = (
  query: string,
  workflowId?: string,
) =>
  request<{
    results: Array<{
      thread_id: string;
      thread_title: string;
      workflow_id: string;
      message_id: string;
      message_preview: string;
      timestamp: string;
    }>;
  }>(`/chats/search?q=${encodeURIComponent(query)}${workflowId ? `&workflow_id=${encodeURIComponent(workflowId)}` : ""}`);

export const pinChatThread = (
  workflowId: string,
  threadId: string,
  pinned: boolean,
) =>
  request<{ status: string; pinned: boolean }>(
    `/chats/${workflowId}/${threadId}/pin`,
    { method: "POST", body: JSON.stringify({ pinned }) },
  );

export const saveChatCheckpoint = (
  workflowId: string,
  threadId: string,
  messageId: string,
  graphSnapshot: Record<string, unknown>,
) =>
  request<{ status: string; filename: string }>(
    `/chats/${workflowId}/${threadId}/checkpoint`,
    {
      method: "POST",
      body: JSON.stringify({
        message_id: messageId,
        graph_snapshot: graphSnapshot,
      }),
    },
  );

export function connectChatStream(
  channelId: string,
  onEvent: (event: Record<string, unknown>) => void,
  onClose?: () => void,
): WebSocket {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/api/chat/${channelId}/events`);
  ws.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch { /* ignore parse errors */ }
  };
  ws.onclose = () => onClose?.();
  return ws;
}

// -- WebSocket ---------------------------------------------------------------

export function connectRunEvents(
  runId: string,
  onEvent: (event: Record<string, unknown>) => void,
  onClose?: () => void,
): WebSocket {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/api/runs/${runId}/events`);
  ws.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch { /* ignore parse errors */ }
  };
  ws.onclose = () => onClose?.();
  return ws;
}
