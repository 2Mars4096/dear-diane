import type { ChatMessage } from "../types/chat";

export function formatInterruptedAssistantContent(
  message: Pick<ChatMessage, "content" | "toolCalls" | "attachments" | "runEvents">,
  eventContent: string | undefined,
): string {
  const nextBaseContent =
    typeof eventContent === "string" && eventContent.trim()
      ? eventContent
      : message.content.trim()
        ? message.content
        : "";
  if (nextBaseContent.trim()) {
    return `${nextBaseContent}\n\n*[generation stopped]*`;
  }
  const hasStructuredArtifacts =
    (message.toolCalls?.length ?? 0) > 0
    || (message.attachments?.length ?? 0) > 0
    || (message.runEvents?.length ?? 0) > 0;
  return hasStructuredArtifacts ? "" : "*[generation stopped]*";
}
