import type { ToolCallInfo } from "../types/chat";

export function normalizeToolCallStatus(
  status: string | undefined,
): ToolCallInfo["status"] {
  if (status === "running" || status === "error") return status;
  return "success";
}

export function upsertToolCallStart(
  toolCalls: ToolCallInfo[] | undefined,
  toolCall: Pick<ToolCallInfo, "id" | "toolName" | "argsPreview">,
): ToolCallInfo[] {
  const existingToolCalls = toolCalls ?? [];
  const existingIndex = existingToolCalls.findIndex(
    (existing) => existing.id === toolCall.id,
  );
  if (existingIndex === -1) {
    return [
      ...existingToolCalls,
      {
        id: toolCall.id,
        toolName: toolCall.toolName,
        argsPreview: toolCall.argsPreview,
        status: "running",
      },
    ];
  }
  return existingToolCalls.map((existing, index) =>
    index === existingIndex
      ? {
          ...existing,
          toolName: toolCall.toolName || existing.toolName,
          argsPreview: toolCall.argsPreview || existing.argsPreview,
          status:
            existing.status === "success" || existing.status === "error"
              ? existing.status
              : "running",
        }
      : existing,
  );
}

export function upsertToolCallResult(
  toolCalls: ToolCallInfo[] | undefined,
  toolCall: {
    id: string;
    toolName: string;
    argsPreview?: string;
    status?: string;
    outputPreview?: string;
    durationMs?: number;
  },
): ToolCallInfo[] {
  const existingToolCalls = toolCalls ?? [];
  const existingIndex = existingToolCalls.findIndex(
    (existing) => existing.id === toolCall.id,
  );
  const nextStatus = normalizeToolCallStatus(toolCall.status);
  if (existingIndex === -1) {
    return [
      ...existingToolCalls,
      {
        id: toolCall.id,
        toolName: toolCall.toolName,
        argsPreview: toolCall.argsPreview ?? "",
        status: nextStatus,
        outputPreview: toolCall.outputPreview,
        durationMs: toolCall.durationMs,
      },
    ];
  }
  return existingToolCalls.map((existing, index) =>
    index === existingIndex
      ? {
          ...existing,
          toolName: toolCall.toolName || existing.toolName,
          argsPreview: toolCall.argsPreview || existing.argsPreview,
          status: nextStatus,
          outputPreview: toolCall.outputPreview,
          durationMs: toolCall.durationMs,
        }
      : existing,
  );
}
