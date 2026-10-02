import { createChatV2Thread, getChatV2Thread, saveChatV2Thread, type ChatV2ThreadSummary } from '../../lib/chatV2Api';
import type { ChatMessage } from '../../types/chat';

export async function forkSession(thread: { id: string; workflow_id: string; title?: string }, source: ChatMessage[]) {
  const created = await createChatV2Thread(thread.workflow_id, {
    title: `${thread.title || 'Conversation'} (fork)`, mode: 'agent', parent_thread_id: thread.id,
    branch_point_message_id: [...source].reverse().find(message => message.role === 'user')?.id, branch_type: 'explore',
  });
  const workflowId = created.workflow_id || thread.workflow_id;
  await saveChatV2Thread(workflowId, created.id, { messages: source, mode: 'agent' });
  return { ...created, workflow_id: workflowId, message_count: source.length };
}

export async function copyOrForkSession(thread: ChatV2ThreadSummary, action: 'copy' | 'fork', loadFallback: () => Promise<ChatMessage[]>, restore: (source: ChatMessage[]) => Promise<ChatMessage[]>) {
  const saved = await getChatV2Thread(thread.workflow_id, thread.id);
  const source = await restore(saved.messages.length ? saved.messages : await loadFallback());
  if (action === 'fork') return forkSession(thread, source);
  await navigator.clipboard.writeText(source.map(message => `${message.role}:\n${message.content}`).join('\n\n'));
  return null;
}

export async function performSessionAction(action: string, value: string | undefined, thread: ChatV2ThreadSummary, context: {
  pinned: boolean; unread: boolean; running: boolean;
  preference: (patch: { pinned?: boolean; unread?: boolean }) => void;
  markRead: () => void; renameActive: (title: string) => void;
  refresh: () => Promise<void>; move: (id: string) => void;
  loadFallback: () => Promise<ChatMessage[]>; restore: (messages: ChatMessage[]) => Promise<ChatMessage[]>;
  openFork: (thread: ChatV2ThreadSummary) => Promise<void>;
  archive: () => Promise<void>; remove: () => Promise<void>;
}) {
  if (action === 'pin') context.preference({ pinned: !context.pinned });
  else if (action === 'read') { context.preference({ unread: !context.unread }); if (context.unread) context.markRead(); }
  else if (action === 'rename' && value) { await saveChatV2Thread(thread.workflow_id, thread.id, { title: value }); context.renameActive(value); await context.refresh(); }
  else if (action === 'move' && value) context.move(value);
  else if (action === 'id') await navigator.clipboard.writeText(thread.id);
  else if (action === 'copy' || action === 'fork') {
    if (action === 'fork' && context.running) throw Error('Wait for this session to finish before forking.');
    const created = await copyOrForkSession(thread, action, context.loadFallback, context.restore);
    if (created) await context.openFork(created);
  } else if (action === 'archive') await context.archive();
  else if (action === 'delete') await context.remove();
}
