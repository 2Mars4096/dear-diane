import { buildApiWebSocketUrl } from "./api";
import { fromBackendMessage, toBackendMessage } from "./chatMessagePersistence";
import type { ChatMessage, ChatStreamEvent } from "../types/chat";

export type ChatV2Mode =
  | "auto"
  | "ask"
  | "agent"
  | "plan"
  | "debug"
  | "conversation";

export type ChatV2ProductMode = "chat" | "agent";

export type ChatV2QueueLane = "append" | "continue_after_current";

export interface ChatV2QueueItem {
  id: string;
  task_id: string;
  lane: ChatV2QueueLane;
  status: string;
  text: string;
  position: number;
  created_at?: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
}

export interface ChatV2TaskSnapshot {
  task_id: string;
  thread_id: string;
  status: string;
  phase: string;
  queue_position?: number | null;
  latest_progress: string;
  latest_artifact_refs: Array<Record<string, unknown>>;
  blocker: string;
  trace_refs: string[];
  metadata: {
    active_run_id?: string | null;
    append_queue_length?: number;
    continue_queue_length?: number;
    queue_items?: ChatV2QueueItem[];
    topic_key?: string;
    queue_key?: string;
    workspace_root?: string;
    workspace_id?: string;
    selected_backend?: string;
    [key: string]: unknown;
  };
}

export interface ChatV2AgentRunEvent {
  type: string;
  run_id?: string | null;
  task_id?: string | null;
  summary?: string;
  artifact_refs?: Array<Record<string, unknown>>;
  source_event_id?: string | null;
  source_event_type?: string | null;
  source_event_path?: string | null;
  payload?: Record<string, unknown>;
}

export interface ChatV2AgentRunRecord {
  run_id: string;
  task_id: string;
  thread_id: string;
  workspace_root: string;
  workspace_id: string;
  status: string;
  command?: {
    command?: string;
    payload?: Record<string, unknown>;
    [key: string]: unknown;
  };
  created_at?: string;
  updated_at?: string;
  latest_event_type?: string;
  latest_summary?: string;
  metadata?: Record<string, unknown>;
}

export interface ChatV2TaskRunRef {
  task_id?: string | null;
  run_id?: string | null;
  status?: string;
  workspace_root?: string;
  workspace_id?: string;
}

export interface ChatV2ThreadSummary {
  id: string;
  title: string;
  workflow_id: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  pinned?: boolean;
  archived?: boolean;
  archived_at?: string | null;
  mode?: string;
  parent_thread_id?: string | null;
  branch_point_message_id?: string | null;
  branch_type?: "edit" | "regenerate" | "explore" | null;
}

export interface ChatV2ThreadPayload {
  id: string;
  title: string;
  workflow_id: string;
  messages: ChatMessage[];
  created_at: string;
  updated_at: string;
  mode?: string;
  parent_thread_id?: string | null;
  branch_point_message_id?: string | null;
  branch_type?: "edit" | "regenerate" | "explore" | null;
}

export interface ChatV2PromptLogPayload {
  thread_id: string;
  path: string;
  content: string;
  entry_count: number;
  run_ids: string[];
}

export interface ChatV2MessageResponse {
  message_id: string;
  stream_channel_id?: string;
  status?: string;
  v2_endpoint?: boolean;
  v2_control_plane?: {
    surface_turn_id?: string;
    triage_action?: string;
    task_binding?: string;
    topic_key?: string;
    queue_key?: string;
    workspace_root?: string;
    workspace_id?: string;
    attachment_count?: number;
    legacy_bridge?: boolean;
    delegated_to?: string;
    task_id?: string | null;
    run_id?: string | null;
    queue_item_id?: string | null;
    queue_position?: number | null;
    task_run_ref?: ChatV2TaskRunRef | null;
  };
  task_run_ref?: ChatV2TaskRunRef | null;
  type?: "run_started" | "run_error";
  run_id?: string;
  scope?: string;
  error?: { message?: string } | Record<string, unknown>;
  control_plane_mode?: string;
  control_plane_mode_source?: string;
}

export const CHAT_V2_ENDPOINTS = {
  streamEvents: (channelId: string) => `/api/chat/${channelId}/events`,
  stopStream: (channelId: string) => `/api/chat/${channelId}/stop`,
  createAgentRun: "/api/v2/agent-runs",
  listTasks: "/api/v2/tasks",
  getTask: (taskId: string) => `/api/v2/tasks/${taskId}`,
  listThreadTasks: (threadId: string) => `/api/v2/threads/${threadId}/tasks`,
  threadPromptLog: (threadId: string) => `/api/v2/threads/${threadId}/prompt-log`,
  getAgentRun: (runId: string) => `/api/v2/agent-runs/${runId}`,
  executeAgentRun: (runId: string) => `/api/v2/agent-runs/${runId}/execute`,
  agentRunEvents: (runId: string) => `/api/v2/agent-runs/${runId}/events`,
  agentRunCommands: (runId: string) => `/api/v2/agent-runs/${runId}/commands`,
  listThreads: "/api/chats",
  createThread: (workflowId: string) => `/api/chats/${workflowId}`,
  getThread: (workflowId: string, threadId: string) =>
    `/api/chats/${workflowId}/${threadId}`,
  updateThread: (workflowId: string, threadId: string) =>
    `/api/chats/${workflowId}/${threadId}`,
  archiveThread: (workflowId: string, threadId: string) =>
    `/api/chats/${workflowId}/${threadId}/archive`,
  deleteThread: (workflowId: string, threadId: string) =>
    `/api/chats/${workflowId}/${threadId}`,
} as const;

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`${response.status}: ${body || response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function normalizeChatV2History(
  messages: ChatMessage[],
): Array<{ role: "user" | "assistant"; content: string }> {
  return messages.flatMap((message) => {
    if (message.role !== "user" && message.role !== "assistant") return [];
    const content = String(message.content ?? "").trim();
    if (!content) return [];
    return [{ role: message.role, content }];
  });
}

export function chatV2MessagesToBackend(
  messages: ChatMessage[],
): Record<string, unknown>[] {
  return messages.map(toBackendMessage);
}

export function chatV2MessagesFromBackend(value: unknown): ChatMessage[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => {
      return Boolean(item && typeof item === "object");
    })
    .map(fromBackendMessage);
}

export async function listChatV2Threads(): Promise<ChatV2ThreadSummary[]> {
  const payload = await readJson<{ threads: ChatV2ThreadSummary[] }>(
    await fetch(CHAT_V2_ENDPOINTS.listThreads),
  );
  return Array.isArray(payload.threads) ? payload.threads : [];
}

export async function createChatV2Thread(
  workflowId: string,
  body: {
    title?: string;
    mode?: ChatV2Mode;
    parent_thread_id?: string;
    branch_point_message_id?: string;
    branch_type?: "edit" | "regenerate" | "explore";
  } = {},
): Promise<ChatV2ThreadPayload> {
  const payload = await readJson<Record<string, unknown>>(
    await fetch(CHAT_V2_ENDPOINTS.createThread(workflowId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  return {
    id: String(payload.id || ""),
    title: String(payload.title || ""),
    workflow_id: String(payload.workflow_id || workflowId),
    messages: chatV2MessagesFromBackend(payload.messages),
    created_at: String(payload.created_at || ""),
    updated_at: String(payload.updated_at || ""),
    mode: typeof payload.mode === "string" ? payload.mode : body.mode,
    parent_thread_id:
      typeof payload.parent_thread_id === "string" ? payload.parent_thread_id : null,
    branch_point_message_id:
      typeof payload.branch_point_message_id === "string"
        ? payload.branch_point_message_id
        : null,
    branch_type:
      payload.branch_type === "edit" ||
      payload.branch_type === "regenerate" ||
      payload.branch_type === "explore"
        ? payload.branch_type
        : null,
  };
}

export async function getChatV2Thread(
  workflowId: string,
  threadId: string,
): Promise<ChatV2ThreadPayload> {
  const payload = await readJson<Record<string, unknown>>(
    await fetch(CHAT_V2_ENDPOINTS.getThread(workflowId, threadId)),
  );
  return {
    id: String(payload.id || threadId),
    title: String(payload.title || ""),
    workflow_id: String(payload.workflow_id || workflowId),
    messages: chatV2MessagesFromBackend(payload.messages),
    created_at: String(payload.created_at || ""),
    updated_at: String(payload.updated_at || ""),
    mode: typeof payload.mode === "string" ? payload.mode : undefined,
    parent_thread_id:
      typeof payload.parent_thread_id === "string" ? payload.parent_thread_id : null,
    branch_point_message_id:
      typeof payload.branch_point_message_id === "string"
        ? payload.branch_point_message_id
        : null,
    branch_type:
      payload.branch_type === "edit" ||
      payload.branch_type === "regenerate" ||
      payload.branch_type === "explore"
        ? payload.branch_type
        : null,
  };
}

export async function saveChatV2Thread(
  workflowId: string,
  threadId: string,
  body: { messages?: ChatMessage[]; mode?: ChatV2Mode; title?: string },
): Promise<void> {
  await readJson<{ status: string }>(
    await fetch(CHAT_V2_ENDPOINTS.updateThread(workflowId, threadId), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...("title" in body ? { title: body.title } : {}),
        ...("mode" in body ? { mode: body.mode } : {}),
        ...("messages" in body
          ? { messages: chatV2MessagesToBackend(body.messages ?? []) }
          : {}),
      }),
    }),
  );
}

export async function deleteChatV2Thread(
  workflowId: string,
  threadId: string,
): Promise<void> {
  await readJson<{ status: string }>(
    await fetch(CHAT_V2_ENDPOINTS.deleteThread(workflowId, threadId), {
      method: "DELETE",
    }),
  );
}

export async function archiveChatV2Thread(
  workflowId: string,
  threadId: string,
  archived = true,
): Promise<void> {
  await readJson<{ status: string; archived: boolean }>(
    await fetch(CHAT_V2_ENDPOINTS.archiveThread(workflowId, threadId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived }),
    }),
  );
}

export async function createChatV2AgentRun(body: {
  workflow_id: string;
  message: string;
  history: Array<{ role: "user" | "assistant"; content: string }>;
  thread_id: string;
  session_id: string;
  mode: ChatV2Mode;
  attachment_path?: string | null;
  mentions?: Array<{ type: string; identifier: string }>;
  surface: string;
  surface_type: string;
  surface_id: string;
  surface_context: Record<string, unknown>;
}): Promise<{
  status: string;
  v2_control_plane: NonNullable<ChatV2MessageResponse["v2_control_plane"]>;
  task_run_ref?: ChatV2TaskRunRef | null;
  task?: ChatV2TaskSnapshot | null;
  event?: ChatV2AgentRunEvent | null;
}> {
  return readJson(
    await fetch(CHAT_V2_ENDPOINTS.createAgentRun, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function getChatV2Task(
  taskId: string,
): Promise<ChatV2TaskSnapshot> {
  const payload = await readJson<{ task: ChatV2TaskSnapshot }>(
    await fetch(CHAT_V2_ENDPOINTS.getTask(taskId)),
  );
  return payload.task;
}

export async function listChatV2Tasks(options: {
  limit?: number;
  workspaceRoot?: string;
  threadId?: string;
} = {}): Promise<ChatV2TaskSnapshot[]> {
  const params = new URLSearchParams();
  if (options.limit) params.set("limit", String(options.limit));
  if (options.workspaceRoot) params.set("workspace_root", options.workspaceRoot);
  if (options.threadId) params.set("thread_id", options.threadId);
  const suffix = params.toString();
  const payload = await readJson<{ tasks: ChatV2TaskSnapshot[] }>(
    await fetch(`${CHAT_V2_ENDPOINTS.listTasks}${suffix ? `?${suffix}` : ""}`),
  );
  return Array.isArray(payload.tasks) ? payload.tasks : [];
}

export async function listChatV2ThreadTasks(
  threadId: string,
): Promise<ChatV2TaskSnapshot[]> {
  const payload = await readJson<{ tasks: ChatV2TaskSnapshot[] }>(
    await fetch(CHAT_V2_ENDPOINTS.listThreadTasks(threadId)),
  );
  return Array.isArray(payload.tasks) ? payload.tasks : [];
}

export async function getChatV2ThreadPromptLog(
  threadId: string,
): Promise<ChatV2PromptLogPayload> {
  return readJson<ChatV2PromptLogPayload>(
    await fetch(CHAT_V2_ENDPOINTS.threadPromptLog(threadId)),
  );
}

export async function getChatV2AgentRun(
  runId: string,
): Promise<ChatV2AgentRunRecord> {
  const payload = await readJson<{ run: ChatV2AgentRunRecord }>(
    await fetch(CHAT_V2_ENDPOINTS.getAgentRun(runId)),
  );
  return payload.run;
}

export async function executeChatV2AgentRun(
  runId: string,
  body: {
    backend?: string | null;
    surface_profile?: string | null;
    background?: boolean;
    profile_policy?: Record<string, unknown>;
    mutation_policy?: Record<string, unknown>;
    approval_policy?: Record<string, unknown>;
    tool_policy?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  } = {},
): Promise<{
  status: string;
  backend?: string;
  run?: ChatV2AgentRunRecord | null;
  task?: ChatV2TaskSnapshot | null;
}> {
  return readJson(
    await fetch(CHAT_V2_ENDPOINTS.executeAgentRun(runId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function getChatV2AgentRunEvents(
  runId: string,
): Promise<ChatV2AgentRunEvent[]> {
  const payload = await readJson<{ events: ChatV2AgentRunEvent[] }>(
    await fetch(CHAT_V2_ENDPOINTS.agentRunEvents(runId)),
  );
  return Array.isArray(payload.events) ? payload.events : [];
}

export async function postChatV2AgentRunCommand(
  runId: string,
  body: {
    command:
      | "start"
      | "append_followup"
      | "continue_after_current"
      | "approve"
      | "deny"
      | "stop"
      | "retry"
      | "branch_from"
      | "reprioritize"
      | "resume";
    task_id?: string | null;
    surface_turn_id?: string | null;
    idempotency_key?: string | null;
    payload?: Record<string, unknown>;
  },
): Promise<{
  command: Record<string, unknown>;
  event: ChatV2AgentRunEvent;
  task?: ChatV2TaskSnapshot | null;
}> {
  return readJson(
    await fetch(CHAT_V2_ENDPOINTS.agentRunCommands(runId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export function connectChatV2Stream(
  channelId: string,
  onEvent: (event: ChatStreamEvent) => void,
  onClose?: (event: CloseEvent) => void,
  onError?: (event: Event) => void,
): WebSocket {
  const ws = new WebSocket(
    buildApiWebSocketUrl(CHAT_V2_ENDPOINTS.streamEvents(channelId)),
  );
  ws.onmessage = (event) => {
    try {
      onEvent(JSON.parse(event.data) as ChatStreamEvent);
    } catch {
      // Ignore malformed stream frames; the server sends a terminal error when needed.
    }
  };
  ws.onclose = (event) => onClose?.(event);
  ws.onerror = (event) => onError?.(event);
  return ws;
}

export function connectChatV2AgentRunEvents(
  runId: string,
  onEvent: (event: ChatV2AgentRunEvent) => void,
  onClose?: (event: CloseEvent) => void,
  onError?: (event: Event) => void,
): WebSocket {
  const ws = new WebSocket(
    buildApiWebSocketUrl(CHAT_V2_ENDPOINTS.agentRunEvents(runId)),
  );
  ws.onmessage = (event) => {
    try {
      onEvent(JSON.parse(event.data) as ChatV2AgentRunEvent);
    } catch {
      // Ignore malformed Agent event frames.
    }
  };
  ws.onclose = (event) => onClose?.(event);
  ws.onerror = (event) => onError?.(event);
  return ws;
}

export async function stopChatV2Stream(
  channelId: string,
  messageId?: string,
): Promise<void> {
  await readJson<{ status: string; channel_id: string }>(
    await fetch(CHAT_V2_ENDPOINTS.stopStream(channelId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId ?? null }),
    }),
  );
}
