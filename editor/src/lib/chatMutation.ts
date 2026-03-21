import type { ChatMessage } from "../types/chat";

export const CHAT_MUTATION_CONFIRM_STORAGE_KEY = "dan_mutation_confirm";

function defaultStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function readMutationConfirmPreference(
  storage: Pick<Storage, "getItem"> | null = defaultStorage(),
): boolean {
  try {
    const raw = storage?.getItem(CHAT_MUTATION_CONFIRM_STORAGE_KEY);
    return raw === "1" || raw === "true";
  } catch {
    return false;
  }
}

export function writeMutationConfirmPreference(
  value: boolean,
  storage: Pick<Storage, "setItem"> | null = defaultStorage(),
): void {
  try {
    storage?.setItem(
      CHAT_MUTATION_CONFIRM_STORAGE_KEY,
      value ? "1" : "0",
    );
  } catch {
    /* ignore storage write failures */
  }
}

export function shouldAutoApplyMutation(
  dryRunResult: Record<string, unknown> | null | undefined,
  confirmMode: boolean,
  mutationStatus?: ChatMessage["mutationStatus"] | null,
): boolean {
  return (
    !confirmMode
    && mutationStatus !== "applied"
    && dryRunResult?.success === true
  );
}

export function summarizeMutationPlan(
  mutationPlan: Record<string, unknown> | null | undefined,
): string {
  const ops = Array.isArray(mutationPlan?.operations)
    ? mutationPlan.operations as Array<Record<string, unknown>>
    : [];
  if (ops.length === 0) return "Applied mutation.";
  let addNodes = 0;
  let removeNodes = 0;
  let addEdges = 0;
  let removeEdges = 0;
  let edits = 0;

  for (const op of ops) {
    switch (String(op.op ?? "")) {
      case "add_node":
        addNodes += 1;
        break;
      case "remove_node":
        removeNodes += 1;
        break;
      case "add_edge":
        addEdges += 1;
        break;
      case "remove_edge":
        removeEdges += 1;
        break;
      case "edit_node":
      case "set_position":
      case "expand_pattern":
      case "apply_skill":
        edits += 1;
        break;
      default:
        edits += 1;
        break;
    }
  }

  const parts: string[] = [];
  if (addNodes > 0) parts.push(`+${addNodes} node${addNodes === 1 ? "" : "s"}`);
  if (removeNodes > 0) parts.push(`-${removeNodes} node${removeNodes === 1 ? "" : "s"}`);
  if (addEdges > 0) parts.push(`+${addEdges} edge${addEdges === 1 ? "" : "s"}`);
  if (removeEdges > 0) parts.push(`-${removeEdges} edge${removeEdges === 1 ? "" : "s"}`);
  if (edits > 0) parts.push(`~${edits} edit${edits === 1 ? "" : "s"}`);

  return parts.length > 0
    ? `Applied: ${parts.join(", ")}`
    : `Applied ${ops.length} operation${ops.length === 1 ? "" : "s"}.`;
}

export function parseRunIdFromStreamChannel(
  streamChannelId: string | null | undefined,
): string | null {
  if (!streamChannelId) return null;
  return streamChannelId.startsWith("run-")
    ? streamChannelId.slice(4)
    : null;
}

export function buildAutoApplyPreviewMessage(
  base: ChatMessage,
  mutationPlan: Record<string, unknown> | null,
  dryRunResult: Record<string, unknown> | null,
  mutationId: string | null,
  mutationStatus: ChatMessage["mutationStatus"] | null,
  content?: string,
  tokenUsage?: { prompt: number; completion: number } | null,
): ChatMessage {
  return {
    ...base,
    content: content ?? base.content,
    tokenUsage: tokenUsage ?? base.tokenUsage ?? null,
    mutationPlan,
    dryRunResult,
    mutationStatus: mutationStatus ?? "proposed",
    mutationId,
  };
}
