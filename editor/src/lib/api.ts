/**
 * API client for the DAN backend server.
 */

import type {
  TokenBreakdownResponse,
  OptimizationReportResponse,
  OptimizationMutationsResponse,
} from "../types/graph";

const BASE = "/api";
const DEFAULT_REQUEST_TIMEOUT_MS = 10000;

function resolveApiWebSocketBase(): string {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  if (import.meta.env.DEV) {
    // In Vite dev, the HMR page can hold a separate websocket open already.
    // Route chat/run streams straight to the backend instead of the proxy.
    return `${protocol}//${location.hostname}:8000`;
  }
  return `${protocol}//${location.host}`;
}

export function buildApiWebSocketUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${resolveApiWebSocketBase()}${normalizedPath}`;
}

type RequestOptions = RequestInit & { timeoutMs?: number };

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const timeoutMs = init?.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  const upstreamSignal = init?.signal;
  const controller = new AbortController();
  const relayAbort = () => {
    controller.abort();
  };

  if (upstreamSignal) {
    if (upstreamSignal.aborted) {
      relayAbort();
    } else {
      upstreamSignal.addEventListener("abort", relayAbort, { once: true });
    }
  }

  const timer =
    timeoutMs > 0
      ? setTimeout(() => {
          controller.abort();
        }, timeoutMs)
      : null;

  try {
    const { timeoutMs: _timeoutMs, ...requestInit } = init ?? {};
    const res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...requestInit.headers },
      ...requestInit,
      signal: controller.signal,
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`${res.status}: ${body}`);
    }
    return res.json();
  } catch (error) {
    if (isAbortError(error) && !upstreamSignal?.aborted && timeoutMs > 0) {
      throw new Error(`Request timed out after ${timeoutMs}ms: ${path}`);
    }
    throw error instanceof Error ? error : new Error(String(error));
  } finally {
    if (timer) clearTimeout(timer);
    upstreamSignal?.removeEventListener("abort", relayAbort);
  }
}

export function isApiStatusError(error: unknown, status: number): boolean {
  return error instanceof Error && error.message.startsWith(`${status}:`);
}

export interface ServerHealth {
  status: string;
  pid: number;
  timestamp: number;
}

export const getServerHealth = () => request<ServerHealth>("/health");

// -- Graph CRUD --------------------------------------------------------------

export interface GraphListItem {
  graph_id: string;
  name: string;
  description: string;
  updated_at: string | null;
}

export interface GraphResponse {
  graph_id: string;
  data: Record<string, unknown>;
  graph_revision?: string | null;
  source_graph_id?: string;
}

export const listGraphs = () =>
  request<{ graphs: GraphListItem[]; last_opened: string | null }>("/graphs");

export const getGraph = (id: string, options?: { layout?: boolean }) =>
  request<GraphResponse>(
    `/graphs/${id}${options?.layout ? "?layout=true" : ""}`,
  );

export const createGraph = (graphId: string, data?: Record<string, unknown>) =>
  request<GraphResponse>("/graphs", {
    method: "POST",
    body: JSON.stringify({ graph_id: graphId, data }),
  });

export const updateGraph = (id: string, data: Record<string, unknown>) =>
  request<{ graph_id: string; status: string; graph_revision?: string | null }>(
    `/graphs/${id}`,
    {
    method: "PUT",
    body: JSON.stringify(data),
    },
  );

export const saveGraphAs = (
  id: string,
  body: {
    new_name: string;
    new_graph_id?: string;
    data?: Record<string, unknown>;
    set_last_opened?: boolean;
  },
) =>
  request<GraphResponse>(`/graphs/${id}/save-as`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export interface ApplyMutationResult {
  success: boolean;
  new_graph: Record<string, unknown> | null;
  graph_revision?: string | null;
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

// -- Variable inspector (13-2) -----------------------------------------------

export interface UpstreamVariable {
  variable_name: string;
  source_node: string | null;
  source_node_id: string | null;
  source_port: string | null;
  type_hint: string;
  required: boolean;
  edge_type: string | null;
  connected: boolean;
  context_key?: string;
  runtime_value?: unknown;
}

export interface NodeInputsResponse {
  node_id: string;
  graph_id: string;
  variables: UpstreamVariable[];
}

export const getNodeInputs = (
  graphId: string,
  nodeId: string,
  runId?: string | null,
) => {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", runId);
  const qs = params.toString();
  return request<NodeInputsResponse>(
    `/graphs/${graphId}/nodes/${nodeId}/inputs${qs ? `?${qs}` : ""}`,
  );
};

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

export interface WorkspaceNoteSummary {
  path: string;
  relative_path: string;
  title: string;
  layout?: string;
  section?: string;
  tags?: string[];
  categories?: string[];
  citations?: string[];
  page_id?: string;
  date?: string;
  lastmod?: string;
  draft?: boolean;
  size: number;
  mtime: number;
}

export interface WorkspaceLearnSession {
  id: string;
  title: string;
  summary: string;
  duration_minutes: number;
  source_heading: string;
  body: string;
  objectives: string[];
  practice: string[];
}

export interface WorkspaceLearnCourse {
  version: number;
  course_id: string;
  note_path: string;
  note_relative_path: string;
  note_title: string;
  note_mtime: number;
  content_signature: string;
  source: string;
  generated_at: string;
  updated_at: string;
  stale?: boolean;
  sessions: WorkspaceLearnSession[];
  progress: {
    active_session_id: string | null;
    completed_session_ids: string[];
  };
}

export interface WorkspaceFileEntry {
  path: string;
  relative_path: string;
  name: string;
  parent: string;
  is_directory: boolean;
  size: number;
  mtime: number;
  depth: number;
}

export interface WorkspaceSkillSuggestion {
  token: string;
  name: string;
  description: string;
  source_scope: string;
}

export type WorkspaceRootSuggestionKind =
  | "current"
  | "match"
  | "nearby"
  | "workspace"
  | "recent";

export interface WorkspaceRootSuggestion {
  path: string;
  name: string;
  label: string;
  kind: WorkspaceRootSuggestionKind;
}

export interface WorkspaceWireGuardStatus {
  service: "wireguard";
  mode: string;
  interface: string;
  launchd_label: string;
  config_path: string;
  config_present: boolean;
  active: boolean;
  status: string;
  wg_present: boolean;
  wg_exit_code: number | null;
  stdout: string;
  stderr: string;
  mutating_actions_enabled: boolean;
  safe_actions: string[];
  conflict_policy: string;
}

export const listWorkspaceNotes = () =>
  request<{ root: string; notes: WorkspaceNoteSummary[] }>("/workspace-notes");

export const readWorkspaceNote = (path: string, init?: RequestOptions) =>
  request<{ note: WorkspaceNoteSummary; content: string }>(
    `/workspace-notes/read?path=${encodeURIComponent(path)}`,
    init,
  );

export const writeWorkspaceNote = (path: string, content: string) =>
  request<{ status: string; note: WorkspaceNoteSummary | null }>(
    "/workspace-notes/write",
    {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    },
  );

export const getWorkspaceNoteLearnCourse = (path: string) =>
  request<{
    status: string;
    root: string;
    course_path: string;
    note: WorkspaceNoteSummary;
    course: WorkspaceLearnCourse | null;
  }>(`/workspace-notes/learn/course?path=${encodeURIComponent(path)}`);

export const generateWorkspaceNoteLearnCourse = (path: string) =>
  request<{
    status: string;
    root: string;
    course_path: string;
    note: WorkspaceNoteSummary;
    course: WorkspaceLearnCourse;
  }>("/workspace-notes/learn/course", {
    method: "POST",
    body: JSON.stringify({ path }),
    timeoutMs: 30000,
  });

export const updateWorkspaceNoteLearnProgress = (
  path: string,
  body: {
    active_session_id?: string | null;
    completed_session_ids: string[];
  },
) =>
  request<{
    status: string;
    root: string;
    course_path: string;
    course: WorkspaceLearnCourse;
  }>("/workspace-notes/learn/course/progress", {
    method: "PUT",
    body: JSON.stringify({ path, ...body }),
  });

export const moveWorkspaceNotePath = (source: string, destination: string) =>
  request<{ status: string; root: string; note: WorkspaceNoteSummary | null }>(
    "/workspace-notes/move",
    {
      method: "POST",
      body: JSON.stringify({ source, destination }),
    },
  );

export const listWorkspaceFileTree = (rootPath?: string) =>
  request<{ root: string; entries: WorkspaceFileEntry[] }>(
    rootPath ? `/workspace-files?root_path=${encodeURIComponent(rootPath)}` : "/workspace-files",
  );

export const listWorkspaceSkillSuggestions = (rootPath?: string, limit = 80) => {
  const params = new URLSearchParams();
  if (rootPath?.trim()) params.set("root_path", rootPath.trim());
  params.set("limit", String(limit));
  return request<{ root: string; skills: WorkspaceSkillSuggestion[] }>(
    `/workspace-skills?${params.toString()}`,
  );
};

export const listWorkspaceRootSuggestions = (query?: string) => {
  const params = new URLSearchParams();
  if (query?.trim()) params.set("query", query.trim());
  const suffix = params.toString();
  return request<{ root: string; suggestions: WorkspaceRootSuggestion[] }>(
    `/workspace-roots${suffix ? `?${suffix}` : ""}`,
  );
};

export const getWorkspaceWireGuardStatus = () =>
  request<WorkspaceWireGuardStatus>("/workspace-wireguard");

export const readWorkspaceFile = (path: string, rootPath?: string) => {
  const params = new URLSearchParams({ path });
  if (rootPath) params.set("root_path", rootPath);
  return request<{
    root: string;
    file: WorkspaceFileEntry | null;
    content: string;
    truncated: boolean;
  }>(`/workspace-files/read?${params.toString()}`);
};

const encodeBase64Url = (value: string) => {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
};

export const workspaceFilePreviewUrl = (
  path: string,
  rootPath?: string,
  relativePath?: string,
) => {
  const token = rootPath?.trim() ? encodeBase64Url(rootPath.trim()) : "-";
  const normalizedPath = (relativePath || path).replace(/\\/g, "/").replace(/^\/+/, "");
  const encodedPath = normalizedPath
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `${BASE}/workspace-files/preview/${token}/${encodedPath || encodeURIComponent(path)}`;
};

export const createWorkspaceFolder = (path: string, rootPath?: string) =>
  request<{ status: string; root: string; file: WorkspaceFileEntry | null }>(
    "/workspace-files/mkdir",
    {
      method: "POST",
      body: JSON.stringify({ path, root_path: rootPath }),
    },
  );

export const moveWorkspacePath = (
  source: string,
  destination: string,
  rootPath?: string,
) =>
  request<{ status: string; root: string; file: WorkspaceFileEntry | null }>(
    "/workspace-files/move",
    {
      method: "POST",
      body: JSON.stringify({ source, destination, root_path: rootPath }),
    },
  );

export interface OrganismLogSummary {
  path: string;
  root_path: string;
  relative_path: string;
  display_name: string;
  product: string;
  stream_kind: string;
  session_id: string;
  turn_id: string;
  task_id: string;
  trace_id: string;
  organism_id: string;
  organ_id: string;
  schema_version: string;
  event_count: number;
  span_count: number;
  size_bytes: number;
  updated_at: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface OrganismLogLaneAnalysis {
  lane_id: string;
  label: string;
  started_at: string;
  ended_at: string;
  span_ids: string[];
}

export interface OrganismLogSpanAnalysis {
  span_id: string;
  label: string;
  lane_id: string;
  lane_label: string;
  parent_span_id: string;
  span_kind: string;
  event: string;
  event_family: string;
  status: string;
  summary: string;
  worker_id: string;
  tool_id: string;
  tool_call_id: string;
  model_call_id: string;
  contract_id: string;
  start_timestamp: string;
  end_timestamp: string;
  start_ms: number | null;
  end_ms: number | null;
  duration_ms: number | null;
  exclusive_duration_ms: number | null;
  waiting_duration_ms: number | null;
  direct_blocker_span_ids: string[];
  dependent_span_ids: string[];
  critical_path_rank: number | null;
}

export interface OrganismLogDependencyEdge {
  edge_id: string;
  source_span_id: string;
  target_span_id: string;
  relationship: "blocked_by" | "lane_sequence" | "parent";
  lag_ms: number | null;
}

export interface OrganismLogBlockingChain {
  target_span_id: string;
  direct_blocker_span_ids: string[];
  blocker_chain_span_ids: string[];
  waiting_duration_ms: number | null;
  status: string;
  wait_reason: string;
}

export interface OrganismLogAnalysis {
  schema_version: string;
  event_count: number;
  span_count: number;
  timeline: {
    started_at: string;
    ended_at: string;
    duration_ms: number | null;
    max_parallel_spans: number;
    lanes: OrganismLogLaneAnalysis[];
    spans: OrganismLogSpanAnalysis[];
  };
  graph: {
    nodes: OrganismLogSpanAnalysis[];
    edges: OrganismLogDependencyEdge[];
    blocker_chains: OrganismLogBlockingChain[];
    critical_path_span_ids: string[];
    critical_path_duration_ms: number | null;
  };
}

export interface OrganismLogListResponse {
  root_path: string;
  logs: OrganismLogSummary[];
}

export interface OrganismLogAnalysisResponse {
  path: string;
  log: OrganismLogSummary;
  analysis: OrganismLogAnalysis;
}

export const fetchOrganismLogs = (
  rootPath: string,
  options: { limit?: number } = {},
) => {
  const params = new URLSearchParams();
  params.set("root_path", rootPath);
  if (options.limit != null) params.set("limit", String(options.limit));
  return request<OrganismLogListResponse>(
    `/organism-logs?${params.toString()}`,
  );
};

export const fetchOrganismLogAnalysis = (
  path: string,
  options: { rootPath?: string } = {},
) => {
  const params = new URLSearchParams();
  params.set("path", path);
  if (options.rootPath) params.set("root_path", options.rootPath);
  return request<OrganismLogAnalysisResponse>(
    `/organism-logs/analyze?${params.toString()}`,
    { timeoutMs: 20000 },
  );
};

// -- Chat --------------------------------------------------------------------

export interface ChatMessageResponse {
  message_id: string;
  stream_channel_id?: string;
  type?: "run_started" | "run_error";
  run_id?: string;
  scope?: string;
  error?: { message?: string } | Record<string, unknown>;
}

export const sendChatMessage = (
  workflowId: string,
  message: string,
  history: Array<{ role: string; content: string }> = [],
  threadId?: string | null,
  clientGraphRevision?: string | null,
  mode: "ask" | "agent" | "plan" | "debug" | "auto" = "auto",
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
  mode?: string;
  parent_thread_id?: string | null;
  branch_point_message_id?: string | null;
  branch_type?: "edit" | "regenerate" | "explore" | null;
}

export const listAllChatThreads = () =>
  request<{ threads: ChatThreadSummary[] }>("/chats");

export const listChatThreads = (workflowId: string) =>
  request<{ threads: ChatThreadSummary[] }>(`/chats/${workflowId}`);

export const getChatThread = (workflowId: string, threadId: string) =>
  request<Record<string, unknown>>(`/chats/${workflowId}/${threadId}`);

export interface CreateChatThreadOptions {
  title?: string;
  mode?: string;
  parent_thread_id?: string;
  branch_point_message_id?: string;
  branch_type?: "edit" | "regenerate" | "explore";
}

export const createChatThread = (
  workflowId: string,
  titleOrOpts?: string | CreateChatThreadOptions,
  mode?: string,
) => {
  const opts: CreateChatThreadOptions =
    typeof titleOrOpts === "object" && titleOrOpts !== null
      ? titleOrOpts
      : { title: titleOrOpts, mode };
  return request<Record<string, unknown>>(`/chats/${workflowId}`, {
    method: "POST",
    body: JSON.stringify(opts),
  });
};

export const updateChatThread = (
  workflowId: string,
  threadId: string,
  body: { title?: string; messages?: unknown[]; mode?: string },
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

export const injectChatMessage = (channelId: string, content: string, injectId: string) =>
  request<{ status: string; channel_id: string; inject_id: string }>(
    `/chat/${channelId}/inject`,
    {
      method: "POST",
      body: JSON.stringify({ content, inject_id: injectId }),
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
  onClose?: (event: CloseEvent) => void,
  onError?: (event: Event) => void,
): WebSocket {
  const ws = new WebSocket(buildApiWebSocketUrl(`/api/chat/${channelId}/events`));
  ws.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data) as Record<string, unknown>;
      onEvent(event);
    } catch { /* ignore parse errors */ }
  };
  ws.onclose = (event) => {
    onClose?.(event);
  };
  ws.onerror = (event) => {
    onError?.(event);
  };
  return ws;
}

// -- 18-4: Token Analytics ---------------------------------------------------

export const fetchTokenBreakdown = (runId: string) =>
  request<TokenBreakdownResponse>(`/runs/${runId}/token-breakdown`);

export const fetchOptimizationReport = (runId: string) =>
  request<OptimizationReportResponse>(`/runs/${runId}/optimization-report`);

export const fetchOptimizationMutations = (runId: string) =>
  request<OptimizationMutationsResponse>(`/runs/${runId}/optimization-mutations`);

// -- Telemetry Analytics ----------------------------------------------------

export interface TelemetryAggregateRow {
  group_key: Record<string, string>;
  count: number;
  total_tokens: number;
  total_cost: number;
  total_duration_ms: number;
  avg_duration_ms: number;
  min_duration_ms: number;
  max_duration_ms: number;
  success_rate: number;
}

export interface TelemetryAnalyticsResponse {
  filters: {
    surface?: string | null;
    session_id?: string | null;
    since?: string | null;
    until?: string | null;
  };
  totals: {
    events: number;
    chat_turns: number;
    fast_commands: number;
    gateway_calls: number;
    total_tokens: number;
    total_cost: number;
  };
  event_types: TelemetryAggregateRow[];
  activity_by_hour: TelemetryAggregateRow[];
  models: TelemetryAggregateRow[];
  modes: TelemetryAggregateRow[];
  lint: {
    workflow_runs: number;
    with_activity: number;
    blocked_runs: number;
    autofixed_runs: number;
    warning_runs: number;
    passed_runs: number;
    states: TelemetryAggregateRow[];
  };
  window_hours: number;
}

export const fetchTelemetryAnalytics = (
  filters: { hours?: number; surface?: string; session_id?: string } = {},
) => {
  const params = new URLSearchParams();
  if (filters.hours != null) params.set("hours", String(filters.hours));
  if (filters.surface) params.set("surface", filters.surface);
  if (filters.session_id) params.set("session_id", filters.session_id);
  const qs = params.toString();
  return request<TelemetryAnalyticsResponse>(
    `/gateway/analytics/telemetry${qs ? `?${qs}` : ""}`,
  );
};

// -- Graph Export ------------------------------------------------------------

export const exportGraphMarkdown = (graphId: string) =>
  request<{
    files: Array<{ path: string; content: string }>;
    diagnostics: Array<{ level: string; message: string; hint?: string }>;
  }>(`/graphs/${graphId}/export/markdown`);

export const exportGraphPython = (graphId: string) =>
  request<{ code: string }>(`/graphs/${graphId}/export/python`);

// -- WebSocket ---------------------------------------------------------------

export function connectRunEvents(
  runId: string,
  onEvent: (event: Record<string, unknown>) => void,
  onClose?: () => void,
): WebSocket {
  const ws = new WebSocket(buildApiWebSocketUrl(`/api/runs/${runId}/events`));
  ws.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch { /* ignore parse errors */ }
  };
  ws.onclose = () => onClose?.();
  return ws;
}

// -- Checkpoint Portal (13-2) ------------------------------------------------

export interface CheckpointEntry {
  checkpoint_id: string;
  timestamp: number | null;
  graph_id: string;
  completed_node_count: number;
  graph_revision: string | null;
  compatible?: boolean;
  stale?: boolean;
  missing_nodes?: string[];
  message?: string;
}

export interface CheckpointListResponse {
  run_id: string;
  checkpoints: CheckpointEntry[];
}

export interface RerunResponse {
  run_id: string;
  source_run_id: string;
  scope: { scope_type: string; target_node_id?: string; sub_graph_key?: string };
  status: string;
}

export const listCheckpoints = (runId: string) =>
  request<CheckpointListResponse>(`/runs/${runId}/checkpoints`);

export const rerunFromCheckpoint = (
  runId: string,
  body: { scope_type: string; target_node_id?: string; sub_graph_key?: string; graph_id: string },
) =>
  request<RerunResponse>(`/runs/${runId}/rerun`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// -- Node Test Cases (13-2) --------------------------------------------------

export interface NodeTestCase {
  id: string;
  name: string;
  node_id: string;
  inputs: Record<string, unknown>;
  expected_outputs: Record<string, unknown> | null;
  assertions: string[] | null;
  tags: string[];
  notes: string;
  created_at: number;
  updated_at: number;
}

export interface TestCaseRunResult {
  passed: boolean;
  actual_outputs: Record<string, unknown>;
  expected_outputs: Record<string, unknown> | null;
  diff: Record<string, { expected: unknown; actual: unknown }> | null;
  execution_metadata: Record<string, unknown>;
  error: string | null;
}

export const listTestCases = (workflowId: string, nodeId: string) =>
  request<{ cases: NodeTestCase[] }>(`/test-cases/${workflowId}/${nodeId}`);

export const createOrUpdateTestCase = (
  workflowId: string,
  nodeId: string,
  body: Partial<NodeTestCase>,
) =>
  request<{ case: NodeTestCase }>(`/test-cases/${workflowId}/${nodeId}`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const deleteTestCase = (
  workflowId: string,
  nodeId: string,
  caseId: string,
) =>
  request<{ status: string; case_id: string }>(
    `/test-cases/${workflowId}/${nodeId}/${caseId}`,
    { method: "DELETE" },
  );

export const runTestCase = (
  workflowId: string,
  nodeId: string,
  caseId: string,
) =>
  request<TestCaseRunResult>(
    `/test-cases/${workflowId}/${nodeId}/${caseId}/run`,
    { method: "POST" },
  );

// -- Blocks (21-5) -----------------------------------------------------------

export interface Block {
  name: string;
  version: string;
  block_type: string;
  description: string;
  author?: string;
  graph_id?: string;
  entry_node_id?: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
}

export const fetchBlocks = () =>
  request<Block[]>("/blocks");

export const importBlock = async (source: File | string): Promise<void> => {
  const path = typeof source === "string" ? source : source.name;
  await request<unknown>("/blocks/import", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
};

export const exportBlock = async (
  graphId: string,
  nodeId?: string,
  meta?: { name?: string; version?: string; description?: string; author?: string },
): Promise<Blob> => {
  const params = new URLSearchParams();
  if (meta?.name) params.set("name", meta.name);
  if (meta?.version) params.set("version", meta.version);
  const path = nodeId
    ? `/blocks/export/${graphId}/${nodeId}`
    : `/blocks/export/${graphId}`;
  const qs = params.toString();
  const url = `${BASE}${path}${qs ? `?${qs}` : ""}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.blob();
};

export const deleteBlock = (name: string, version: string) =>
  request<{ status: string }>(
    `/blocks/${encodeURIComponent(name)}/${encodeURIComponent(version)}`,
    { method: "DELETE" },
  );

// -- Publish (21-3) ----------------------------------------------------------

export interface PublishStatus {
  graph_id: string;
  published: boolean;
  workflow_id: string | null;
  config?: Record<string, unknown>;
}

export const publishGraph = (graphId: string, type: "mcp" | "http") =>
  request<{ status: string; workflow_id: string; graph_id: string }>(`/graphs/${graphId}/publish`, {
    method: "POST",
    body: JSON.stringify({ type }),
  });

export const unpublishGraph = (graphId: string) =>
  request<{ status: string }>(`/graphs/${graphId}/unpublish`, {
    method: "POST",
  });

export const getPublishStatus = (graphId: string) =>
  request<PublishStatus>(`/graphs/${graphId}/publish-status`);

export const getMcpConfig = (graphId: string) =>
  request<{ config: Record<string, unknown> }>(`/graphs/${graphId}/mcp-config`);

export type RuntimeConfigKey =
  | "DAN_CHAT_MODEL"
  | "DAN_LLM_MODEL"
  | "DAN_LLM_BASE_URL"
  | "DAN_BOT_NAME"
  | "DAN_ENABLE_TIER_POLICY"
  | "DAN_FULL_TOOLS"
  | "DAN_TELEMETRY"
  | "DAN_LEARNING_MODE";

export type RuntimeSettingsMap = Record<RuntimeConfigKey, string>;

export interface RuntimeSettingsResponse {
  values: RuntimeSettingsMap;
  restart_required_keys: RuntimeConfigKey[];
}

export interface RuntimeSettingUpdateResponse {
  status: string;
  key: RuntimeConfigKey;
  value: string;
  restart_required: boolean;
}

export const getRuntimeSettings = () =>
  request<RuntimeSettingsResponse>("/config");

export const setRuntimeSetting = (
  key: RuntimeConfigKey,
  value: string,
) =>
  request<RuntimeSettingUpdateResponse>("/config", {
    method: "POST",
    body: JSON.stringify({ key, value }),
  });

// -- Adapters (21-4) ---------------------------------------------------------

export type AdapterConnectionState =
  | "disconnected"
  | "starting"
  | "pairing"
  | "connected"
  | "reconnecting"
  | "error";

export interface AdapterInfo {
  type: string;
  running: boolean;
  session_count: number;
  adapter_id: string;
  uptime_seconds?: number;
  connection_state?: AdapterConnectionState | string | null;
  last_error?: string | null;
  paired?: boolean | null;
  configured?: boolean | null;
  [key: string]: unknown;
}

export interface AdapterStartResponse {
  status: string;
  adapter_id: string;
  type: string;
}

export interface AdapterConfigSummary {
  type?: string;
  configured?: boolean;
  masked_token?: string | null;
  masked_app_secret?: string | null;
  masked_encoding_aes_key?: string | null;
  bot_username?: string | null;
  allowed_chat_ids?: number[];
  allowed_chat_count?: number;
  allowed_jids?: string[];
  allowed_jid_count?: number;
  app_id?: string | null;
  webhook_url?: string | null;
  callback_path?: string | null;
  account_name?: string | null;
  app_name?: string | null;
  welcome_message?: string | null;
  support_encrypted_callbacks?: boolean | null;
  passive_reply_budget_seconds?: number | null;
  passive_reply_fallback_text?: string | null;
  api_base_url?: string | null;
  access_token_refresh_margin_seconds?: number | null;
  server_url?: string | null;
  auto_start?: boolean | null;
  paired?: boolean | null;
  dependency_module?: string | null;
  dependency_package?: string | null;
  dependency_installed?: boolean | null;
  install_hint?: string | null;
  session_db_exists?: boolean;
  db_path?: string | null;
  connection_state?: AdapterConnectionState | string | null;
  last_error?: string | null;
  [key: string]: unknown;
}

export const startAdapter = (
  type: string,
  config: Record<string, unknown> = {},
  workflowPath = "",
) =>
  request<AdapterStartResponse>("/adapters/start", {
    method: "POST",
    body: JSON.stringify({
      type,
      workflow_path: workflowPath,
      config,
    }),
  });

export const getAdapterStatus = () =>
  request<AdapterInfo[]>("/adapters/status");

export const stopAdapter = (adapterId: string) =>
  request<{ status: string }>("/adapters/stop", {
    method: "POST",
    body: JSON.stringify({ adapter_id: adapterId }),
  });

export const getAdapterConfig = (type: string) =>
  request<AdapterConfigSummary>(`/adapters/config/${encodeURIComponent(type)}`);

export const saveAdapterConfig = (
  type: string,
  config: Record<string, unknown>,
) =>
  request<AdapterConfigSummary>(`/adapters/config/${encodeURIComponent(type)}`, {
    method: "POST",
    body: JSON.stringify(config),
  });

export const resetAdapterConfig = (type: string) =>
  request<AdapterConfigSummary>(
    `/adapters/config/${encodeURIComponent(type)}/reset`,
    {
      method: "POST",
    },
  );

export function connectAdapterEvents(
  adapterId: string,
  onEvent: (event: Record<string, unknown>) => void,
  onClose?: () => void,
): EventSource {
  const url = `${BASE}/adapters/${encodeURIComponent(adapterId)}/events`;
  const es = new EventSource(url);
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      /* ignore malformed event payloads */
    }
  };
  es.onerror = () => {
    es.close();
    onClose?.();
  };
  return es;
}

// -- Furnace ----------------------------------------------------------------

export interface FurnaceCreateSessionBody {
  name?: string;
  topic?: string;
  description?: string;
  corpus_id?: string;
  recipe_id?: string;
  parent_session_id?: string;
  inherit_sources?: boolean;
  variant_label?: string;
  target_count?: number;
}

export interface FurnaceSessionSummary {
  session_id: string;
  recipe_id: string;
  name: string;
  topic: string;
  status: string;
  current_phase: string;
  source_count: number;
  processed_count: number;
  total_cost_usd: number;
  variant_label: string;
  parent_session_id: string;
  family_session_id: string;
  tags: string[];
  created_at: number;
  updated_at: number;
}

export interface FurnaceAddSourcesBody {
  source_ids?: string[];
  pdf_paths?: string[];
  urls?: string[];
}

export interface FurnaceListSessionsParams {
  corpus_id?: string;
  recipe_id?: string;
  status?: string;
}

export interface FurnaceUpdateSessionTagsBody {
  tags?: string[];
  add?: string[];
  remove?: string[];
}

export const furnaceCreateSession = (body: FurnaceCreateSessionBody) =>
  request<{ session: Record<string, unknown>; artifact_dir: string }>(
    "/furnace/sessions",
    { method: "POST", body: JSON.stringify(body) },
  );

export const furnaceAddSources = (sessionId: string, body: FurnaceAddSourcesBody) =>
  request<{ session_id: string; added: number; total_sources: number }>(
    `/furnace/sessions/${encodeURIComponent(sessionId)}/sources`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const furnaceStartSession = (sessionId: string) =>
  request<{ session_id: string; status: string }>(
    `/furnace/sessions/${encodeURIComponent(sessionId)}/start`,
    { method: "POST" },
  );

export const furnacePauseSession = (sessionId: string) =>
  request<{ session_id: string; status: string }>(
    `/furnace/sessions/${encodeURIComponent(sessionId)}/pause`,
    { method: "POST" },
  );

export const furnaceResumeSession = (sessionId: string) =>
  request<{ session_id: string; status: string }>(
    `/furnace/sessions/${encodeURIComponent(sessionId)}/resume`,
    { method: "POST" },
  );

export const furnaceCancelSession = (sessionId: string) =>
  request<{ session_id: string; status: string }>(
    `/furnace/sessions/${encodeURIComponent(sessionId)}/cancel`,
    { method: "POST" },
  );

export const furnaceDeleteSession = (sessionId: string, options?: { delete_artifacts?: boolean }) => {
  const search = new URLSearchParams();
  if (options?.delete_artifacts != null) {
    search.set("delete_artifacts", String(options.delete_artifacts));
  }
  const qs = search.toString();
  return request<{ session_id: string; deleted: boolean; artifacts_deleted: boolean }>(
    `/furnace/sessions/${encodeURIComponent(sessionId)}${qs ? `?${qs}` : ""}`,
    { method: "DELETE" },
  );
};

export const furnaceListSessions = (params?: FurnaceListSessionsParams) => {
  const search = new URLSearchParams();
  if (params?.corpus_id) search.set("corpus_id", params.corpus_id);
  if (params?.recipe_id) search.set("recipe_id", params.recipe_id);
  if (params?.status) search.set("status", params.status);
  const qs = search.toString();
  return request<{ sessions: FurnaceSessionSummary[] }>(
    `/furnace/sessions${qs ? `?${qs}` : ""}`,
  );
};

export const furnaceGetSession = (sessionId: string) =>
  request<{ session: Record<string, unknown> }>(
    `/furnace/sessions/${encodeURIComponent(sessionId)}`,
  );

export const furnaceUpdateSessionTags = (
  sessionId: string,
  body: FurnaceUpdateSessionTagsBody,
) =>
  request<{ session_id: string; tags: string[]; session: FurnaceSessionSummary }>(
    `/furnace/sessions/${encodeURIComponent(sessionId)}/tags`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const furnaceGetRecipe = (sessionId: string) =>
  request<{
    session_id: string;
    recipe_md: string | null;
    recipe_full_md: string | null;
    skill_md: string | null;
    artifact_dir: string;
  }>(`/furnace/sessions/${encodeURIComponent(sessionId)}/recipe`);

export function furnaceConnectSSE(
  sessionId: string,
  onEvent: (event: Record<string, unknown>) => void,
  onClose?: () => void,
): EventSource {
  const url = `${BASE}/furnace/sessions/${encodeURIComponent(sessionId)}/events`;
  const es = new EventSource(url);
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch { /* ignore */ }
  };
  es.onerror = () => {
    es.close();
    onClose?.();
  };
  return es;
}
