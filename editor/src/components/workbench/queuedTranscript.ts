import type { ChatMessage } from "../../types/chat";
import type { ChatV2TaskSnapshot } from "../../lib/chatV2Api";

const ACKNOWLEDGEMENT = /^(Queued (?:after|to continue|for checkpoint)|Steering note queued)/;

/**
 * Waiting requests live in Up next, not the transcript. A request appears once it is
 * delivered (injected into the running turn or promoted to a new one) and disappears
 * if it is withdrawn. Older clients saved an acknowledgement reply; those never show.
 */
export function visibleQueuedTranscript(messages: ChatMessage[], tasks: ChatV2TaskSnapshot[], pending: Record<string, boolean> = {}) {
  const hidden = new Set<string>();
  for (const task of tasks) for (const item of task.metadata?.queue_items ?? []) {
    const payload = item.metadata?.command_payload as Record<string, unknown> | undefined;
    const userId = typeof payload?.client_message_id === "string" ? payload.client_message_id : "";
    const assistantId = typeof payload?.client_assistant_id === "string" ? payload.client_assistant_id : "";
    let index = userId ? messages.findIndex(message => message.id === userId) : -1;
    if (!userId) index = messages.findIndex((message, i) => message.role === "user" && message.content.trim() === item.text.trim()
      && ACKNOWLEDGEMENT.test(messages[i + 1]?.content ?? ""));
    const user = messages[index];
    const assistant = assistantId ? messages.find(message => message.id === assistantId) : index >= 0 ? messages[index + 1] : undefined;
    if (user && ["queued", "waiting_dependency", "cancelled"].includes(item.status)) hidden.add(user.id);
    if (assistant?.role === "assistant" && !assistant.taskRunRef?.runId && !pending[assistant.id]) hidden.add(assistant.id);
  }
  return messages.filter(message => !hidden.has(message.id)
    && !(message.role === "assistant" && !message.taskRunRef?.runId && !pending[message.id] && ACKNOWLEDGEMENT.test(message.content)));
}
