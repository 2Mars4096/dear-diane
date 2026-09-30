import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, it, vi } from 'vitest';

function worker(fetcher = vi.fn().mockResolvedValue(new Response('online'))) {
  const listeners: Record<string, (event: unknown) => void> = {};
  const fallback = new Response('offline');
  const match = vi.fn().mockResolvedValue(fallback);
  const add = vi.fn().mockResolvedValue(undefined);
  const remove = vi.fn().mockResolvedValue(true);
  runInNewContext(readFileSync(new URL('../../../public/sw.js', import.meta.url), 'utf8'), {
    self: { location: { origin: 'https://diane.test' }, addEventListener: (name: string, fn: (event: unknown) => void) => { listeners[name] = fn; } },
    caches: { match, open: async () => ({ add }), keys: async () => ['other-app', 'dear-diane-offline-v0', 'dear-diane-offline-v1'], delete: remove },
    fetch: fetcher, URL, Response,
  });
  return { listeners, fallback, match, add, remove, fetcher };
}

it('leaves API, authentication, files, mutations and other origins to the network', () => {
  const { listeners, fetcher } = worker();
  for (const [url, mode, method] of [
    ['/api/personal/commitments', 'navigate', 'GET'], ['/remote/login', 'navigate', 'GET'],
    ['/api/file.pdf', 'navigate', 'GET'], ['/', 'navigate', 'POST'],
    ['https://other.test/', 'navigate', 'GET'], ['/', 'cors', 'GET'],
  ]) {
    const respondWith = vi.fn();
    listeners.fetch({ request: { url: new URL(url, 'https://diane.test').href, mode, method }, respondWith });
    expect(respondWith).not.toHaveBeenCalled();
  }
  expect(fetcher).not.toHaveBeenCalled();
});

it('shows only the generic offline page on failed workspace navigation', async () => {
  const { listeners, fallback, match } = worker(vi.fn().mockRejectedValue(new TypeError('offline')));
  let response: Promise<Response> | undefined;
  listeners.fetch({ request: { url: 'https://diane.test/', mode: 'navigate', method: 'GET' }, respondWith: (value: Promise<Response>) => { response = value; } });
  expect(await response).toBe(fallback);
  expect(match).toHaveBeenCalledWith('/app/offline.html');
});

it('preserves server authentication failures rather than replacing them with offline data', async () => {
  const denied = new Response('sign in', { status: 401 });
  const { listeners, match } = worker(vi.fn().mockResolvedValue(denied));
  let response: Promise<Response> | undefined;
  listeners.fetch({ request: { url: 'https://diane.test/', mode: 'navigate', method: 'GET' }, respondWith: (value: Promise<Response>) => { response = value; } });
  expect(await response).toBe(denied);
  expect(match).not.toHaveBeenCalled();
});

it('caches only the fallback and removes only this app’s obsolete caches', async () => {
  const { listeners, add, remove } = worker();
  const waits: Promise<unknown>[] = [];
  const event = { waitUntil: (work: Promise<unknown>) => waits.push(work) };
  listeners.install(event); listeners.activate(event);
  await Promise.all(waits);
  expect(add).toHaveBeenCalledExactlyOnceWith('/app/offline.html');
  expect(remove).toHaveBeenCalledExactlyOnceWith('dear-diane-offline-v0');
});
