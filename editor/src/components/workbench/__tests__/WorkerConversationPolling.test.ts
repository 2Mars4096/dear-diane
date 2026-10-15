// @vitest-environment happy-dom
import { act, createElement as h } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import WorkerConversation from '../WorkerConversation';
import type { TeamWorker } from '../teamPresentation';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it('does not overlap slow requests and pauses a hidden Team pane', async () => {
  vi.useFakeTimers();
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  let resolve!: (value: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>(done => { resolve = done; }));
  vi.stubGlobal('fetch', fetcher);
  const root = createRoot(document.createElement('div'));
  const worker: TeamWorker = { worker_id: 'w', parent_run_id: 'r', backend: 'claude', status: 'running', prompt: '', response: '', error: '', native_session_id: '' };
  try {
    await act(async () => root.render(h(WorkerConversation, { worker, active: true })));
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => resolve(new Response(JSON.stringify({ entries: [], before: null }))));
    await act(async () => root.render(h(WorkerConversation, { worker, active: false })));
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => root.render(h(WorkerConversation, { worker, active: true })));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => resolve(new Response(JSON.stringify({ entries: [], before: null }))));
  } finally { act(() => root.unmount()); vi.useRealTimers(); vi.unstubAllGlobals(); }
});

it('ignores a late cancelled page after hide/reopen and preserves the new cursor', async () => {
  vi.useFakeTimers();
  const requests: { url: string; signal: AbortSignal; resolve: (response: Response) => void }[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => new Promise<Response>(resolve => requests.push({ url, signal: options.signal as AbortSignal, resolve }))));
  const root = createRoot(document.createElement('div'));
  const worker: TeamWorker = { worker_id: 'w', parent_run_id: 'r', backend: 'claude', status: 'running', prompt: '', response: '', error: '', native_session_id: '' };
  const page = (cursor: number) => new Response(JSON.stringify({ entries: [{ cursor, event: { type: 'system', subtype: 'init' } }], before: null }));
  try {
    await act(async () => root.render(h(WorkerConversation, { worker, active: true })));
    await act(async () => root.render(h(WorkerConversation, { worker, active: false })));
    expect(requests[0].signal.aborted).toBe(true);
    await act(async () => root.render(h(WorkerConversation, { worker, active: true })));
    await act(async () => requests[1].resolve(page(10)));
    await act(async () => requests[0].resolve(page(100)));
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(requests[2].url).toContain('?after=10');
    await act(async () => requests[2].resolve(new Response(JSON.stringify({ entries: [], before: null }))));
  } finally { act(() => root.unmount()); vi.useRealTimers(); vi.unstubAllGlobals(); }
});
