// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import MarkdownRenderer, { renderMarkdownToHtml } from '../MarkdownRenderer';
const host = document.createElement('div'); document.body.append(host);
let root: ReturnType<typeof createRoot>;
afterEach(() => { if (root) act(() => root.unmount()); host.innerHTML = ''; delete window.electronAPI; document.querySelector('meta[name="dan-remote-machine"]')?.remove(); });
it('retains local targets, makes Claude code paths actionable and avoids nested anchors', () => {
  const html = renderMarkdownToHtml('[`output/imagegen`](output/imagegen) and `src/app.ts` and [web](https://example.com)');
  host.innerHTML = html;
  expect(host.querySelectorAll('a')).toHaveLength(3);
  expect(host.querySelector('a')?.dataset.fileLink).toBe('output/imagegen');
  expect(host.querySelector('a a')).toBeNull();
  expect(host.querySelector('a[href="https://example.com"]')).not.toBeNull();
});
it('dispatches click and native context menu with the project root for either lead', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const fileLink = vi.fn().mockResolvedValue({ ok: true });
  window.electronAPI = { shell: { fileLink } } as unknown as NonNullable<Window['electronAPI']>;
  host.dataset.workspaceRoot = '/project'; root = createRoot(host);
  act(() => root.render(createElement(MarkdownRenderer, { content: '[folder](output/imagegen)' })));
  await act(async () => host.querySelector('a')!.click());
  expect(fileLink).toHaveBeenLastCalledWith({ href: 'output/imagegen', root: '/project', menu: false });
  await act(async () => host.querySelector('a')!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true })));
  expect(fileLink).toHaveBeenLastCalledWith({ href: 'output/imagegen', root: '/project', menu: true });
});
it('shows missing-file errors and prevents local actions for remote links', async () => {
  const fileLink = vi.fn().mockResolvedValue({ ok: false, error: 'File or folder not found' });
  window.electronAPI = { shell: { fileLink } } as unknown as NonNullable<Window['electronAPI']>;
  root = createRoot(host); act(() => root.render(createElement(MarkdownRenderer, { content: '[file](/missing)' })));
  await act(async () => host.querySelector('a')!.click());
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('not found');
  fileLink.mockClear(); const meta = document.createElement('meta'); meta.name = 'dan-remote-machine'; document.head.append(meta);
  await act(async () => host.querySelector('a')!.click()); expect(fileLink).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('remote host');
});
