// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import OpenRouterSetup from '../OpenRouterSetup';
import { requestJson } from '../../../lib/http';
vi.mock('../../../lib/http', () => ({ requestJson: vi.fn() }));
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear(); vi.resetAllMocks();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
async function render() { await act(async () => { root.render(createElement(OpenRouterSetup, { children: createElement('textarea', { defaultValue: 'Current session draft' }) })); }); }
it('keeps an established workspace mounted through checking, failure and retry', async () => {
  localStorage.setItem('dan.setup.ready.v1', 'true');
  let reject!: (error: Error) => void;
  vi.mocked(requestJson).mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
  await render();
  const draft = host.querySelector('textarea');
  expect(draft).not.toBeNull(); expect(host.querySelector('h1')).toBeNull();
  await act(async () => { reject(Error('offline')); });
  expect(host.querySelector('textarea')).toBe(draft);
  expect(host.querySelector('[role=alert]')?.textContent).toContain('Could not reach');
  vi.mocked(requestJson).mockResolvedValueOnce({ ready: true, local: true });
  await act(async () => { host.querySelector('button')!.click(); });
  expect(host.querySelector('textarea')).toBe(draft); expect(host.querySelector('[role=alert]')).toBeNull();
});
it('requires setup for a new installation and remembers verified readiness', async () => {
  vi.mocked(requestJson).mockResolvedValueOnce({ ready: false, local: true });
  await render(); expect(host.querySelector('textarea')).toBeNull();
  expect(host.textContent).toContain('Connect OpenRouter');
  expect(localStorage.getItem('dan.setup.ready.v1')).toBeNull();
  await act(async () => { root.unmount(); }); root = createRoot(host);
  vi.mocked(requestJson).mockResolvedValueOnce({ ready: true, local: true });
  await render(); expect(host.querySelector('textarea')).not.toBeNull();
  expect(localStorage.getItem('dan.setup.ready.v1')).toBe('true');
});
it('keeps the session visible when credentials need reconnecting until Connect is chosen', async () => {
  localStorage.setItem('dan.setup.ready.v1', 'true');
  vi.mocked(requestJson).mockResolvedValueOnce({ ready: false, local: true });
  await render(); expect(host.querySelector('textarea')).not.toBeNull();
  await act(async () => { host.querySelector('button')!.click(); });
  expect(host.textContent).toContain('OpenRouter API key');
});
