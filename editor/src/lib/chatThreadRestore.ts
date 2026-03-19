import type { ChatThreadSummary } from "./api";

function legacyActiveThreadStorageKey(workflowId: string | null | undefined): string {
  return `dan_active_thread_${String(workflowId ?? "")}`;
}

export function activeThreadStorageKey(
  workflowId: string | null | undefined,
  workspaceId?: string | null,
): string {
  const workspaceKey = String(workspaceId ?? "").trim();
  if (!workspaceKey) {
    return legacyActiveThreadStorageKey(workflowId);
  }
  return `${legacyActiveThreadStorageKey(workflowId)}_${workspaceKey}`;
}

export function saveActiveThreadSelection(
  workflowId: string | null | undefined,
  threadId: string,
  workspaceId?: string | null,
): void {
  if (!threadId.trim()) return;
  try {
    localStorage.setItem(activeThreadStorageKey(workflowId, workspaceId), threadId);
  } catch {
    // Best-effort persistence only.
  }
}

export function readSavedActiveThreadSelection(
  workflowId: string | null | undefined,
  workspaceId?: string | null,
): string | null {
  try {
    const scoped = localStorage.getItem(activeThreadStorageKey(workflowId, workspaceId));
    if (scoped?.trim()) {
      return scoped;
    }
    if (workspaceId?.trim()) {
      const legacy = localStorage.getItem(legacyActiveThreadStorageKey(workflowId));
      if (legacy?.trim()) {
        return legacy;
      }
    }
  } catch {
    // Ignore storage failures and fall through to null.
  }
  return null;
}

export function chooseInitialChatThreadId(params: {
  threads: ChatThreadSummary[];
  workflowId: string | null | undefined;
  workspaceId?: string | null;
  workspaceActiveThreadId?: string | null;
  appThreadId?: string | null;
  appWorkflowId?: string | null;
}): string | null {
  const availableIds = new Set(params.threads.map((thread) => thread.id));
  const candidates = [
    // An explicit in-session handoff (for example from the compact sidebar)
    // should win immediately.
    params.appWorkflowId === params.workflowId ? params.appThreadId : null,
    // On startup, prefer the workflow-scoped persisted full-chat selection
    // over the broader workspace fallback, which can point at a different
    // chat surface's last thread.
    readSavedActiveThreadSelection(params.workflowId, params.workspaceId),
    params.workspaceActiveThreadId,
  ];
  for (const candidate of candidates) {
    if (candidate && availableIds.has(candidate)) {
      return candidate;
    }
  }
  return params.threads[0]?.id ?? null;
}
