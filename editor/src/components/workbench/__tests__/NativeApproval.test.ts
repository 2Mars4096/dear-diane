// @vitest-environment happy-dom
import { act, createElement as h } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { NativeApproval } from '../NativeApproval';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it('shows the exact proposed action and sends only a one-time decision', async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ accepted: true })));
  vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('div'), root = createRoot(host);
  try {
    await act(async () => root.render(h(NativeApproval, { request: { id: 'p', method: 'claude/canUseTool', params: { tool_name: 'Bash', input: { command: 'npm test' }, reason: 'Run tests' } }, endpoint: '/api/native-lead/run/answer' })));
    expect(host.textContent).toContain('npm test');
    await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === 'Allow once')!.click());
    expect(JSON.parse(String((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body))).toEqual({ request_id: 'p', decision: 'accept', answers: {} });
    expect(host.textContent).toBe('');
  } finally { act(() => root.unmount()); vi.unstubAllGlobals(); }
});

it('renders choices and enables sending only after choosing or typing an answer', async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ accepted: true })));
  vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('div'), root = createRoot(host);
  const question = 'How should we proceed?';
  try {
    await act(async () => root.render(h(NativeApproval, { request: { id: 'q', method: 'claude/canUseTool', params: { tool_name: 'AskUserQuestion', questions: [{ id: question, question, options: [{ label: 'Run serially', description: 'Continue without Git setup' }, { label: 'Wait', description: 'I will prepare the repo' }] }] } }, endpoint: '/answer' })));
    const send = [...host.querySelectorAll('button')].find(button => button.textContent === 'Send answer')!;
    expect(send.disabled).toBe(true);
    expect(host.querySelector('legend')?.textContent).toBe(question);
    expect(host.querySelector('textarea')?.value).toBe('');
    act(() => host.querySelector<HTMLInputElement>('input[type="radio"]')!.click());
    expect(send.disabled).toBe(false);
    await act(async () => send.click());
    expect(JSON.parse(String((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body)).answers).toEqual({ [question]: 'Run serially' });
  } finally { act(() => root.unmount()); vi.unstubAllGlobals(); }
});

it('accepts a custom typed answer and Enter sends it', async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ accepted: true })));
  vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('div'), root = createRoot(host);
  try {
    await act(async () => root.render(h(NativeApproval, { request: { id: 'q', method: 'claude/canUseTool', params: { questions: [{ id: 'q1', question: 'Next step?' }] } }, endpoint: '/answer' })));
    const textarea = host.querySelector('textarea')!;
    act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, 'Review first'); textarea.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect(JSON.parse(String((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body)).answers).toEqual({ q1: 'Review first' });
  } finally { act(() => root.unmount()); vi.unstubAllGlobals(); }
});
