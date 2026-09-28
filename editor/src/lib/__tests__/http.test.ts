import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, requestJson } from '../http';
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

function stalledFetch() {
  vi.stubGlobal('fetch', vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => {
    if (init.signal?.aborted) reject(init.signal.reason);
    else init.signal?.addEventListener('abort', () => reject(init.signal!.reason), { once: true });
  })));
}
it('times out and releases timers so later requests can succeed', async () => {
  vi.useFakeTimers(); stalledFetch();
  const failed = expect(requestJson('/test', { timeoutMs: 50 })).rejects.toThrow('timed out');
  await vi.advanceTimersByTimeAsync(50); await failed;
  expect(vi.getTimerCount()).toBe(0);
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ready: true })));
  expect(await requestJson('/test')).toEqual({ ready: true });
  expect(vi.getTimerCount()).toBe(0);
});
it('preserves caller cancellation, including a signal already aborted', async () => {
  stalledFetch(); const controller = new AbortController();
  const result = requestJson('/test', { signal: controller.signal }); controller.abort();
  await expect(result).rejects.toMatchObject({ name: 'AbortError' });
  await expect(requestJson('/test', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' });
});
it('keeps the deadline active while consuming the body', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn(async (_url, init: RequestInit) => ({ ok: true, json: () => new Promise((_resolve, reject) => init.signal?.addEventListener('abort', () => reject(init.signal!.reason))) })));
  const failed = expect(requestJson('/test', { timeoutMs: 50 })).rejects.toThrow('timed out');
  await vi.advanceTimersByTimeAsync(50); await failed;
});
it('normalizes validation errors, conflicts and non-JSON proxy failures', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: [{ msg: 'Invalid host' }] }, { status: 422 })));
  await expect(requestJson('/test')).rejects.toMatchObject({ status: 422, message: '422: Invalid host' });
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'changed elsewhere' }, { status: 409 })));
  await expect(requestJson('/test')).rejects.toBeInstanceOf(ApiError);
  vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>proxy error</html>', { status: 502, statusText: 'Bad Gateway' })));
  await expect(requestJson('/test')).rejects.toThrow('502: Bad Gateway');
});
it('merges Headers inputs and allows empty success responses', async () => {
  const fetcher = vi.fn(async () => new Response(null, { status: 204 })); vi.stubGlobal('fetch', fetcher);
  expect(await requestJson('/test', { method: 'PUT', body: '{}', headers: new Headers({ 'If-None-Match': '*' }) })).toBeUndefined();
  const init = (fetcher.mock.calls as unknown as [string, RequestInit][])[0][1];
  expect(new Headers(init.headers).get('If-None-Match')).toBe('*');
  expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
});
