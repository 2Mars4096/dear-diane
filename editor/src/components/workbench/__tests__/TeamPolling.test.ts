// @vitest-environment happy-dom
import { act, createElement as h } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { useTeamWorkers } from '../TeamProgress';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

it('refreshes active work frequently without repeatedly fetching historical runs', async () => {
  vi.useFakeTimers();
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify({ workers: [{ worker_id: url, parent_run_id: url.endsWith('/current') ? 'current' : 'old', status: url.endsWith('/current') ? 'running' : 'completed' }], roster: [] })));
  vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('div'), root = createRoot(host);
  function View() { const team = useTeamWorkers(['old', 'current'], true, 'current'); return h('span', null, team.workers.length); }
  try {
    await act(async () => root.render(h(View)));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/old'))).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/current'))).toHaveLength(4);
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
    expect(fetcher).toHaveBeenCalledTimes(5);
  } finally { act(() => root.unmount()); vi.useRealTimers(); vi.unstubAllGlobals(); Object.defineProperty(document, 'hidden', { configurable: true, value: false }); }
});

it('does not overlap a slow refresh with another polling round', async () => {
  vi.useFakeTimers();
  let resolve!: (response: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>(done => { resolve = done; }));
  vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('div'), root = createRoot(host);
  function View() { useTeamWorkers(['current'], true, 'current'); return null; }
  try {
    await act(async () => root.render(h(View)));
    await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => resolve(new Response(JSON.stringify({ workers: [], roster: [] }))));
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(fetcher).toHaveBeenCalledTimes(2);
  } finally { act(() => root.unmount()); vi.useRealTimers(); vi.unstubAllGlobals(); }
});

it('keeps worker text and action changes out of the workspace render subscription', async () => {
  vi.useFakeTimers();
  let sequence = 0;
  let status = 'running';
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ workers: [{ worker_id: 'w', parent_run_id: 'current', backend: 'claude', status, prompt: 'Task', response: `response ${++sequence}`, error: '', native_session_id: 's', activity: 'Thinking', actions: [{ text: `action ${sequence}`, at: sequence }] }], roster: [] }))));
  const host = document.createElement('div'), root = createRoot(host);
  let store: ReturnType<typeof useTeamWorkers>['store'];
  let overview: ReturnType<typeof useTeamWorkers>['workers'];
  function View() { const team = useTeamWorkers(['current'], true, 'current', true); store = team.store; overview = team.workers; return h('span', null, team.workers[0]?.status); }
  try {
    await act(async () => root.render(h(View)));
    const initial = overview!;
    await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
    expect(overview!).toBe(initial);
    expect(store!.getSnapshot()[0].response).toBe('response 4');
    expect(store!.getSnapshot()[0].actions?.[0].text).toBe('action 4');
    status = 'completed';
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(overview!).not.toBe(initial);
    expect(host.textContent).toBe('completed');
  } finally { act(() => root.unmount()); vi.useRealTimers(); vi.unstubAllGlobals(); }
});
