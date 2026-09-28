// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { refreshLibrary, usePaperLibrary } from '../library';
const catalogue = { papers: [], sessions: [], warnings: [], settings: { sources: [] } };
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
it('shares one poller across consumers and stops it after the final unsubscription', async () => {
  vi.useFakeTimers(); Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const fetcher = vi.fn(async () => Response.json(catalogue)); vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('div'), root = createRoot(host);
  function Consumer() { usePaperLibrary(); return null; }
  await act(async () => root.render(createElement(Consumer, { key: 'first' })));
  await act(async () => vi.advanceTimersByTimeAsync(5_000));
  await act(async () => root.render([createElement(Consumer, { key: 'first' }), createElement(Consumer, { key: 'second' })]));
  expect(fetcher).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(1);
  await act(async () => vi.advanceTimersByTimeAsync(25_000)); expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => root.render(createElement(Consumer, { key: 'second' })));
  expect(vi.getTimerCount()).toBe(1);
  act(() => root.unmount()); expect(vi.getTimerCount()).toBe(0);
  window.dispatchEvent(new Event('focus')); expect(fetcher).toHaveBeenCalledTimes(2);
});
it('allows a refresh after a stalled request reaches its deadline', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => init.signal?.addEventListener('abort', () => reject(init.signal!.reason)))));
  const first = refreshLibrary(); expect(refreshLibrary()).toBe(first);
  await vi.advanceTimersByTimeAsync(10_000); await first;
  const fetcher = vi.fn(async () => Response.json(catalogue)); vi.stubGlobal('fetch', fetcher);
  await refreshLibrary(); expect(fetcher).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
});
