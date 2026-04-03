import type { EditorChatMode } from "../../lib/editorChat";
import type { AppMode } from "../../store/useAppStore";
import type { ChatMessage } from "../../types/chat";

export type StoredSidebarChatMode = Exclude<EditorChatMode, "conversation">;

export interface StoredModeChatSession {
  messages: ChatMessage[];
  threadId: string | null;
  chatMode: StoredSidebarChatMode;
}

const MAX_PERSISTED_MESSAGES = 200;

export function buildModeChatStorageKey(
  workspaceId: string,
  mode: AppMode,
  workflowId: string,
) {
  return `dan-chat-${workspaceId}-${mode}-${workflowId}`;
}

export function buildLegacyModeChatStorageKey(
  workspaceId: string,
  mode: AppMode,
) {
  return `dan-chat-${workspaceId}-${mode}`;
}

export function buildModeChatScopeKey(
  workspaceId: string | null,
  mode: AppMode,
  workflowId: string,
) {
  return `${workspaceId ?? "_no-workspace"}::${mode}::${workflowId}`;
}

export function defaultStoredSession(): StoredModeChatSession {
  return {
    messages: [],
    threadId: null,
    chatMode: "auto",
  };
}

function normalizeStoredModeChatSession(parsed: unknown): StoredModeChatSession {
  if (Array.isArray(parsed)) {
    return {
      ...defaultStoredSession(),
      messages: parsed as ChatMessage[],
    };
  }

  if (!parsed || typeof parsed !== "object") {
    return defaultStoredSession();
  }

  return {
    messages: Array.isArray((parsed as { messages?: unknown }).messages)
      ? ((parsed as { messages: ChatMessage[] }).messages ?? [])
      : [],
    threadId:
      typeof (parsed as { threadId?: unknown }).threadId === "string"
        ? ((parsed as { threadId: string }).threadId ?? null)
        : null,
    chatMode:
      typeof (parsed as { chatMode?: unknown }).chatMode === "string"
        ? (((parsed as { chatMode: StoredSidebarChatMode }).chatMode ??
            "auto") as StoredSidebarChatMode)
        : "auto",
  };
}

function readStoredModeChatSession(storageKey: string): StoredModeChatSession | null {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    return normalizeStoredModeChatSession(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function loadModeChatSession(
  workspaceId: string | null,
  mode: AppMode,
  workflowId: string,
) {
  if (!workspaceId) return defaultStoredSession();

  const scoped = readStoredModeChatSession(
    buildModeChatStorageKey(workspaceId, mode, workflowId),
  );
  if (scoped) return scoped;

  // Legacy storage was only keyed by workspace + mode, so only use it for the
  // default scratch workflow to avoid leaking drafts across named workflows.
  if (workflowId !== "_scratch") {
    return defaultStoredSession();
  }

  return (
    readStoredModeChatSession(buildLegacyModeChatStorageKey(workspaceId, mode)) ??
    defaultStoredSession()
  );
}

export function saveModeChatSession(
  workspaceId: string | null,
  mode: AppMode,
  workflowId: string,
  session: StoredModeChatSession,
) {
  if (!workspaceId) return;
  try {
    localStorage.setItem(
      buildModeChatStorageKey(workspaceId, mode, workflowId),
      JSON.stringify({
        ...session,
        messages: session.messages.slice(-MAX_PERSISTED_MESSAGES),
      }),
    );
  } catch {
    /* ignore quota */
  }
}

export function clearModeChatSession(
  workspaceId: string | null,
  mode: AppMode,
  workflowId: string,
) {
  if (!workspaceId) return;
  try {
    localStorage.removeItem(buildModeChatStorageKey(workspaceId, mode, workflowId));
    if (workflowId === "_scratch") {
      localStorage.removeItem(buildLegacyModeChatStorageKey(workspaceId, mode));
    }
  } catch {
    /* ignore */
  }
}

export function resolveModeChatWorkflowId(
  graphWorkflowId: string | null,
  activeChatWorkflowId: string | null,
) {
  const candidate = (graphWorkflowId || activeChatWorkflowId || "").trim();
  return candidate || "_scratch";
}

export function formatModeChatWorkflowLabel(workflowId: string) {
  return workflowId === "_scratch" ? "Scratch" : workflowId;
}
