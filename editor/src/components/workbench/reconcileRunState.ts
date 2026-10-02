import type { ChatMessage } from '../../types/chat';
import type { ChatV2TaskSnapshot } from '../../lib/chatV2Api';
const terminal = new Set(['completed', 'failed', 'blocked', 'stopped']);

export function runFailureMessage(text: string) {
  if (/already has an active writer/i.test(text)) return 'This Codex session is already open for writing in another connection. This request did not start. Release that session in the other connection, then retry here.';
  return text;
}

/** Polling must settle replies even when the final SSE event was missed. */
export function reconcileRunState(messages: ChatMessage[], pending: Record<string, boolean>, tasks: ChatV2TaskSnapshot[]) {
  let nextPending = pending;
  let changed = false;
  const nextMessages = messages.map(message => {
    const ref = message.taskRunRef;
    if (message.role !== 'assistant' || !ref?.runId) return message;
    // A task can have several runs; never settle a different run's response.
    const task = tasks.find(task => task.task_id === ref.taskId && task.metadata.active_run_id === ref.runId);
    const status = task?.status ?? ref.status;
    if (!terminal.has(status)) return message;
    if (pending[message.id]) { if (nextPending === pending) nextPending = { ...pending }; delete nextPending[message.id]; }
    const failure = status !== 'completed' ? runFailureMessage(task?.blocker || task?.latest_progress || `Request ${status}.`) : '';
    const content = message.content || failure;
    if (ref.status === status && content === message.content) return message;
    changed = true;
    return { ...message, content, taskRunRef: { ...ref, status } };
  });
  return { messages: changed ? nextMessages : messages, pending: nextPending };
}
