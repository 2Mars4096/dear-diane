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
  mode: "mutate" | "build" = "mutate",
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
