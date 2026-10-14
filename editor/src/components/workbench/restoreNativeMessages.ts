import type { ChatMessage } from "../../types/chat";
import { getChatV2AgentRun } from "../../lib/chatV2Api";

export async function restoreFullNativeMessages(sourceMessages: ChatMessage[]): Promise<ChatMessage[]> {
  return Promise.all(sourceMessages.map(async (message) => {
          if (message.role !== "assistant" || !message.taskRunRef?.runId) return message;
          try {
            const run = await getChatV2AgentRun(message.taskRunRef.runId);
            const result = run.metadata?.backend_result as { backend?: string; summary?: string } | undefined;
            if (run.status === "completed" && ["codex", "claude", "antigravity", "cursor"].includes(result?.backend || "") && result?.summary && result.summary.startsWith(message.content) && result.summary.length > message.content.length) {
              return { ...message, content: result.summary };
            }
          } catch { /* Keep saved text when run history is unavailable. */ }
          return message;
        }));
}
