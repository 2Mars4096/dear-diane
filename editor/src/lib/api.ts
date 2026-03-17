/**
 * API client for the DAN backend server.
 */

import type {
  TokenBreakdownResponse,
  OptimizationReportResponse,
  OptimizationMutationsResponse,
} from "../types/graph";

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
  mode: "ask" | "agent" | "plan" | "debug" | "auto" = "agent",
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

// -- 18-4: Token Analytics ---------------------------------------------------

export const fetchTokenBreakdown = (runId: string) =>
  request<TokenBreakdownResponse>(`/runs/${runId}/token-breakdown`);

export const fetchOptimizationReport = (runId: string) =>
  request<OptimizationReportResponse>(`/runs/${runId}/optimization-report`);

export const fetchOptimizationMutations = (runId: string) =>
  request<OptimizationMutationsResponse>(`/runs/${runId}/optimization-mutations`);

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
  bot_username?: string | null;
  allowed_chat_ids?: number[];
  allowed_chat_count?: number;
  allowed_jids?: string[];
  allowed_jid_count?: number;
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
