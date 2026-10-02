import { expect, it } from 'vitest';
import { reconcileRunState } from '../reconcileRunState';
import type { ChatMessage } from '../../../types/chat';
import type { ChatV2TaskSnapshot } from '../../../lib/chatV2Api';
const message = { id: 'reply', role: 'assistant', content: '', timestamp: 1, taskRunRef: { taskId: 'task', runId: 'run', status: 'running' } } as ChatMessage;
const failed = { task_id: 'task', status: 'failed', latest_progress: 'thread abc already has an active writer', metadata: { active_run_id: 'run' } } as ChatV2TaskSnapshot;
it('settles a failed run from polling when no terminal stream event arrived', () => {
  const result = reconcileRunState([message], { reply: true, other: true }, [failed]);
  expect(result.pending).toEqual({ other: true });
  expect(result.messages[0].taskRunRef?.status).toBe('failed');
  expect(result.messages[0].content).toContain('This request did not start');
});
it('does not settle another run belonging to the same task', () => {
  const pending = { reply: true };
  expect(reconcileRunState([message], pending, [{ ...failed, metadata: { active_run_id: 'later' } }]).pending).toBe(pending);
});
it('preserves partial answers, settles completion, and is stable on repeated polls', () => {
  const result = reconcileRunState([{ ...message, content: 'Partial answer' }], { reply: true }, [failed]);
  expect(result.messages[0].content).toBe('Partial answer');
  const repeated = reconcileRunState(result.messages, result.pending, [failed]);
  expect(repeated.messages).toBe(result.messages); expect(repeated.pending).toBe(result.pending);
  expect(reconcileRunState([message], { reply: true }, [{ ...failed, status: 'completed' }]).pending).toEqual({});
});
it('clears stale pending flags from already terminal saved replies', () => {
  expect(reconcileRunState([{ ...message, taskRunRef: { ...message.taskRunRef!, status: 'stopped' } }], { reply: true }, []).pending).toEqual({});
});
