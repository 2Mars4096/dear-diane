import * as api from "../../lib/api";
import { describeLatestToolProgress } from "../../lib/toolCallPresentation";
import type { ChatMessage, RunEventPayload, ToolCallInfo } from "../../types/chat";

function createAssistantMessage(
  assistantId: string,
  timestamp: number = Date.now(),
): ChatMessage {
  return {
    id: assistantId,
    role: "assistant",
    content: "",
    timestamp,
  };
}

export function upsertAssistantMessage(
  prev: ChatMessage[],
  assistantId: string,
  mutate: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  let found = false;
  const next = prev.map((message) => {
    if (message.id !== assistantId) return message;
    found = true;
    return mutate(message);
  });
  if (found) return next;
  return [...next, mutate(createAssistantMessage(assistantId))];
}

export function applyAssistantToolCallStart(
  prev: ChatMessage[],
  assistantId: string,
  toolCall: Pick<ToolCallInfo, "id" | "toolName" | "argsPreview">,
): ChatMessage[] {
  return upsertAssistantMessage(prev, assistantId, (message) => {
    const nextToolCalls = [
      ...(message.toolCalls ?? []),
      {
        id: toolCall.id,
        toolName: toolCall.toolName,
        argsPreview: toolCall.argsPreview,
        status: "running" as const,
      },
    ];
    const progress = describeLatestToolProgress(nextToolCalls);
    return {
      ...message,
      toolCalls: nextToolCalls,
      progressStatus: progress?.text,
      progressFilePath: progress?.filePath,
    };
  });
}

export function applyAssistantToolCallResult(
  prev: ChatMessage[],
  assistantId: string,
  toolCall: {
    id: string;
    toolName: string;
    argsPreview: string;
    status: string;
    outputPreview?: string;
    durationMs?: number;
  },
): ChatMessage[] {
  return upsertAssistantMessage(prev, assistantId, (message) => {
    const existingToolCalls = message.toolCalls ?? [];
    const seenToolCall = existingToolCalls.some(
      (existing) => existing.id === toolCall.id,
    );
    const nextStatus: ToolCallInfo["status"] =
      toolCall.status === "running"
        ? "running"
        : toolCall.status === "error"
          ? "error"
          : "success";
    const nextToolCalls = seenToolCall
      ? existingToolCalls.map((existing) =>
          existing.id === toolCall.id
            ? {
                ...existing,
                status: nextStatus,
                outputPreview: toolCall.outputPreview,
                durationMs: toolCall.durationMs,
              }
            : existing,
        )
      : [
          ...existingToolCalls,
          {
            id: toolCall.id,
            toolName: toolCall.toolName,
            argsPreview: toolCall.argsPreview,
            status: nextStatus,
            outputPreview: toolCall.outputPreview,
            durationMs: toolCall.durationMs,
          },
        ];
    const progress = describeLatestToolProgress(nextToolCalls);
    return {
      ...message,
      toolCalls: nextToolCalls,
      progressStatus: progress?.text,
      progressFilePath: progress?.filePath,
    };
  });
}

export function applyAssistantRunEvent(
  prev: ChatMessage[],
  assistantId: string,
  runEvent: RunEventPayload,
): ChatMessage[] {
  return upsertAssistantMessage(prev, assistantId, (message) => ({
    ...message,
    runEvents: [...(message.runEvents ?? []), runEvent],
    runRef: (() => {
      const detail = runEvent.detail ?? {};
      const detailRunId =
        typeof detail.run_id === "string" ? detail.run_id : null;
      const detailScope =
        typeof detail.scope === "string" ? detail.scope : "full";
      const terminalStatus =
        runEvent.event_type === "run_completed"
          ? "completed"
          : runEvent.event_type === "run_failed"
            ? "failed"
            : runEvent.event_type === "run_cancelled"
              ? "cancelled"
              : null;
      if (message.runRef) {
        return terminalStatus
          ? { ...message.runRef, status: terminalStatus }
          : message.runRef;
      }
      if (!detailRunId) return message.runRef ?? null;
      return {
        runId: detailRunId,
        scope: detailScope,
        status: terminalStatus ?? "running",
      };
    })(),
  }));
}

export function insertInjectedUserBeforeAssistant(
  prev: ChatMessage[],
  assistantId: string,
  injectedUserMsg: ChatMessage,
  assistantTimestamp: number = Date.now(),
): ChatMessage[] {
  const assistantIndex = prev.findIndex((message) => message.id === assistantId);
  if (assistantIndex === -1) {
    return [
      ...prev,
      injectedUserMsg,
      createAssistantMessage(assistantId, assistantTimestamp),
    ];
  }
  return [
    ...prev.slice(0, assistantIndex),
    injectedUserMsg,
    ...prev.slice(assistantIndex),
  ];
}

export function shouldStopSidebarThreadSnapshotPolling(
  error: unknown,
): boolean {
  return api.isApiStatusError(error, 404);
}
