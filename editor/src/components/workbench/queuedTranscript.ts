import type { ChatMessage } from "../../types/chat";
import type { ChatV2TaskSnapshot } from "../../lib/chatV2Api";

const ACKNOWLEDGEMENT = /^(Queued (?:after|to continue|for checkpoint)|Steering note queued)/;

export function queueItemIsWaiting(item: { status: string; metadata?: Record<string, unknown> }): boolean {
  return ["queued", "waiting_dependency"].includes(item.status)
    || (item.status === "injected" && !!item.metadata?.admitted_run_id
      && !item.metadata?.continued_run_id && !item.metadata?.delivered_run_id);
}

/**
 * Waiting requests live in Up next, not the transcript. A request appears once it is
 * delivered (injected into the running turn or promoted to a new one) and disappears
 * if it is withdrawn. Older clients saved an acknowledgement reply; those never show.
 */
export function visibleQueuedTranscript(messages: ChatMessage[], tasks: ChatV2TaskSnapshot[], pending: Record<string, boolean> = {}) {
  const hidden = new Set<string>();
  const deliveredRequests = new Map<string, Set<string>>();
  for (const task of tasks) for (const item of task.metadata?.queue_items ?? []) {
    const payload = item.metadata?.command_payload as Record<string, unknown> | undefined;
    const userId = typeof payload?.client_message_id === "string" ? payload.client_message_id : "";
    const assistantId = typeof payload?.client_assistant_id === "string" ? payload.client_assistant_id : "";
    let index = userId ? messages.findIndex(message => message.id === userId) : -1;
    if (!userId) index = messages.findIndex((message, i) => message.role === "user" && message.content.trim() === item.text.trim()
      && ACKNOWLEDGEMENT.test(messages[i + 1]?.content ?? ""));
    const user = messages[index];
    const deliveredRun = item.metadata?.delivered_run_id;
    if (user && item.status === "completed" && typeof deliveredRun === "string" && deliveredRun) {
      const requests = deliveredRequests.get(deliveredRun) ?? new Set<string>();
      requests.add(user.id);
      deliveredRequests.set(deliveredRun, requests);
    }
    const assistant = assistantId ? messages.find(message => message.id === assistantId) : index >= 0 ? messages[index + 1] : undefined;
    if (user && (queueItemIsWaiting(item) || item.status === "cancelled")) hidden.add(user.id);
    if (assistant?.role === "assistant" && !assistant.taskRunRef?.runId && !pending[assistant.id]) hidden.add(assistant.id);
  }
  const visible = messages.filter(message => !hidden.has(message.id)
    && !(message.role === "assistant" && !message.taskRunRef?.runId && !pending[message.id] && ACKNOWLEDGEMENT.test(message.content)));
  // A steer joins the existing run. Its answer must follow the requests it
  // handles, even though the assistant bubble was created before the steer.
  // Project this ordering without rewriting durable history or duplicating text.
  for (const [runId, requests] of deliveredRequests) {
    const answerIndex = visible.findIndex(message => message.role === "assistant" && message.taskRunRef?.runId === runId);
    const lastRequestIndex = visible.reduce((last, message, index) => requests.has(message.id) ? index : last, -1);
    if (answerIndex >= 0 && lastRequestIndex > answerIndex) {
      const [answer] = visible.splice(answerIndex, 1);
      visible.splice(lastRequestIndex, 0, answer);
    }
  }
  return visible;
}

/** Link newly promoted replies immediately, before any stream event arrives. */
export function attachFollowupReply(messages: ChatMessage[], target: ChatMessage, runId: string, taskId?: string): ChatMessage[] {
  const reply: ChatMessage = { ...target, timestamp: Date.now(), content: "", runEvents: [],
    taskRunRef: { ...target.taskRunRef, runId, taskId, status: "running" } };
  return messages.some(message => message.id === target.id)
    ? messages.map(message => message.id === target.id ? reply : message)
    : [...messages, reply];
}
