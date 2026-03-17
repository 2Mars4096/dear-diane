import type { ChatMessage } from "../types/chat";
import type { ChatThreadSummary } from "./api";
import {
  chatAttachmentToComposerDraft,
  type ComposerAttachmentDraft,
} from "./editorChat";
import {
  deriveDraftThreadTitleFromMessage,
  normalizeThreadTitleInput,
} from "./chatThreadTitle";

export interface ChatBranchTarget {
  historyBefore: ChatMessage[];
  content: string;
  attachments: ComposerAttachmentDraft[];
}

export interface ChatThreadSiblingInfo {
  key: string;
  position: number;
  count: number;
  threadIds: string[];
}

export interface ChatThreadBranchTree {
  byId: Record<string, ChatThreadSummary>;
  rootIds: string[];
  childrenByParentId: Record<string, string[]>;
  ancestryByThreadId: Record<string, string[]>;
  siblingInfoByThreadId: Record<string, ChatThreadSiblingInfo>;
}

function parseThreadTime(
  thread: ChatThreadSummary,
  field: "created_at" | "updated_at",
): number {
  const value = Date.parse(thread[field]);
  return Number.isFinite(value) ? value : 0;
}

function compareByCreatedAt(
  a: ChatThreadSummary,
  b: ChatThreadSummary,
): number {
  const createdDelta = parseThreadTime(a, "created_at") - parseThreadTime(b, "created_at");
  if (createdDelta !== 0) return createdDelta;
  const updatedDelta = parseThreadTime(a, "updated_at") - parseThreadTime(b, "updated_at");
  if (updatedDelta !== 0) return updatedDelta;
  return a.id.localeCompare(b.id);
}

function compareByUpdatedDesc(
  a: ChatThreadSummary,
  b: ChatThreadSummary,
): number {
  const updatedDelta = parseThreadTime(b, "updated_at") - parseThreadTime(a, "updated_at");
  if (updatedDelta !== 0) return updatedDelta;
  const createdDelta = parseThreadTime(b, "created_at") - parseThreadTime(a, "created_at");
  if (createdDelta !== 0) return createdDelta;
  return a.id.localeCompare(b.id);
}

function normalizeBranchPointMessageId(value?: string | null): string | null {
  const normalized = value?.trim();
  return normalized ? normalized : null;
}

function getSiblingGroupKey(thread: ChatThreadSummary): string | null {
  if (!thread.parent_thread_id) return null;
  return `${thread.parent_thread_id}::${normalizeBranchPointMessageId(
    thread.branch_point_message_id,
  ) ?? ""}::${thread.branch_type ?? ""}`;
}

export function buildThreadBranchTree(
  threads: ChatThreadSummary[],
): ChatThreadBranchTree {
  const byId: Record<string, ChatThreadSummary> = {};
  for (const thread of threads) {
    byId[thread.id] = thread;
  }
  const rootThreads: ChatThreadSummary[] = [];
  const childrenByParent: Record<string, ChatThreadSummary[]> = {};

  for (const thread of threads) {
    const parentId = thread.parent_thread_id ?? null;
    if (parentId && parentId !== thread.id && byId[parentId]) {
      (childrenByParent[parentId] ??= []).push(thread);
      continue;
    }
    rootThreads.push(thread);
  }

  for (const childThreads of Object.values(childrenByParent)) {
    childThreads.sort(compareByCreatedAt);
  }
  rootThreads.sort(compareByUpdatedDesc);

  const childrenByParentId = Object.fromEntries(
    Object.entries(childrenByParent).map(([parentId, childThreads]) => [
      parentId,
      childThreads.map((thread) => thread.id),
    ]),
  );

  const ancestryByThreadId: Record<string, string[]> = {};
  for (const thread of threads) {
    const ancestry: string[] = [];
    const seen = new Set<string>([thread.id]);
    let parentId = thread.parent_thread_id ?? null;
    while (parentId && byId[parentId] && !seen.has(parentId)) {
      ancestry.unshift(parentId);
      seen.add(parentId);
      parentId = byId[parentId].parent_thread_id ?? null;
    }
    ancestryByThreadId[thread.id] = ancestry;
  }

  const siblingBuckets: Record<string, ChatThreadSummary[]> = {};
  for (const thread of threads) {
    const siblingGroupKey = getSiblingGroupKey(thread);
    if (!siblingGroupKey) continue;
    (siblingBuckets[siblingGroupKey] ??= []).push(thread);
  }

  const siblingInfoByThreadId: Record<string, ChatThreadSiblingInfo> = {};
  for (const [key, siblingThreads] of Object.entries(siblingBuckets)) {
    siblingThreads.sort(compareByCreatedAt);
    const threadIds = siblingThreads.map((thread) => thread.id);
    siblingThreads.forEach((thread, index) => {
      siblingInfoByThreadId[thread.id] = {
        key,
        position: index + 1,
        count: siblingThreads.length,
        threadIds,
      };
    });
  }

  return {
    byId,
    rootIds: rootThreads.map((thread) => thread.id),
    childrenByParentId,
    ancestryByThreadId,
    siblingInfoByThreadId,
  };
}

export function isSyntheticAttachmentSummary(
  content: string,
  attachments?: ChatMessage["attachments"],
): boolean {
  const normalized = content.trim();
  if (!normalized || !attachments?.length) return false;
  if (attachments.length === 1) {
    return normalized === `Attached ${attachments[0].filename}`;
  }
  return normalized === `Attached ${attachments.length} items`;
}

function toBranchTarget(
  historyBefore: ChatMessage[],
  userMessage: ChatMessage,
): ChatBranchTarget {
  return {
    historyBefore,
    content: isSyntheticAttachmentSummary(
      userMessage.content,
      userMessage.attachments,
    )
      ? ""
      : userMessage.content,
    attachments: (userMessage.attachments ?? []).map(chatAttachmentToComposerDraft),
  };
}

export function getEditBranchTarget(
  messages: ChatMessage[],
  userMessageId: string,
): ChatBranchTarget | null {
  const index = messages.findIndex(
    (message) => message.id === userMessageId && message.role === "user",
  );
  if (index < 0) return null;
  return toBranchTarget(messages.slice(0, index), messages[index]);
}

export function getRegenerateBranchTarget(
  messages: ChatMessage[],
  assistantMessageId: string,
): ChatBranchTarget | null {
  const assistantIndex = messages.findIndex(
    (message) =>
      message.id === assistantMessageId && message.role === "assistant",
  );
  if (assistantIndex < 0) return null;

  for (let index = assistantIndex - 1; index >= 0; index -= 1) {
    const candidate = messages[index];
    if (candidate.role === "user") {
      return toBranchTarget(messages.slice(0, index), candidate);
    }
  }

  return null;
}

/**
 * Returns history up to and including the target assistant message,
 * with an empty content/attachments so the user can type a fresh follow-up.
 */
export function getExploreBranchTarget(
  messages: ChatMessage[],
  assistantMessageId: string,
): { historyUpToHere: ChatMessage[] } | null {
  const index = messages.findIndex(
    (message) =>
      message.id === assistantMessageId && message.role === "assistant",
  );
  if (index < 0) return null;
  return { historyUpToHere: messages.slice(0, index + 1) };
}

export function buildBranchedThreadTitle(
  currentTitle: string,
  nextUserContent: string,
): string {
  const normalizedCurrent = normalizeThreadTitleInput(currentTitle);
  const trimmedNext = nextUserContent.trim();
  const derived = trimmedNext
    ? normalizeThreadTitleInput(deriveDraftThreadTitleFromMessage(trimmedNext))
    : "";
  const base = derived || normalizedCurrent || "Conversation";
  return /\(branch\)$/i.test(base) ? base : `${base} (branch)`;
}
