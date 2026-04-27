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

export interface ChatV2ThreadSummary {
  id: string;
  title: string;
  workflow_id: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  pinned?: boolean;
  mode?: string;
}

export interface ChatV2ThreadPayload {
  id: string;
  title: string;
  workflow_id: string;
  messages: ChatMessage[];
  created_at: string;
  updated_at: string;
  mode?: string;
}

export interface ChatV2MessageResponse {
  message_id: string;
  stream_channel_id?: string;
  status?: string;
  type?: "run_started" | "run_error";
  run_id?: string;
  scope?: string;
  error?: { message?: string } | Record<string, unknown>;
  control_plane_mode?: string;
  control_plane_mode_source?: string;
}

export const CHAT_V2_ENDPOINTS = {
  sendMessage: "/api/chat/message",
  streamEvents: (channelId: string) => `/api/chat/${channelId}/events`,
  stopStream: (channelId: string) => `/api/chat/${channelId}/stop`,
  listThreads: "/api/chats",
  createThread: (workflowId: string) => `/api/chats/${workflowId}`,
  getThread: (workflowId: string, threadId: string) =>
    `/api/chats/${workflowId}/${threadId}`,
  updateThread: (workflowId: string, threadId: string) =>
    `/api/chats/${workflowId}/${threadId}`,
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
  body: { title?: string; mode?: ChatV2Mode } = {},
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

export async function postChatV2Message(body: {
  workflow_id: string;
  message: string;
  history: Array<{ role: "user" | "assistant"; content: string }>;
  thread_id: string;
  session_id: string;
  mode: ChatV2Mode;
  surface: string;
  surface_type: string;
  surface_id: string;
  surface_context: Record<string, unknown>;
}): Promise<ChatV2MessageResponse> {
  return readJson<ChatV2MessageResponse>(
    await fetch(CHAT_V2_ENDPOINTS.sendMessage, {
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
