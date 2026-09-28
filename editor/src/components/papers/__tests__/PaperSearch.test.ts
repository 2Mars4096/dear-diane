// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PaperSearch } from '../PaperLibrary';
import type { Paper } from '../library';
const paper = { id: 'a', title: 'Networks', authors: 'Smith', year: '2024', journal: 'Econometrica', tags: [], notes: '', abstract: '', key: 'smith2024', available: true, added: '2024' } as unknown as Paper;
const secondPaper = { ...paper, id: 'b', key: 'smith2023', title: 'Supply chains', added: '2023' };
let host: HTMLDivElement, root: ReturnType<typeof createRoot>;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ papers: [paper, secondPaper], sessions: [], settings: { sources: [] }, warnings: [] }) })));
  Element.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal = function() { this.open = true; };
  HTMLDialogElement.prototype.close = function() { this.open = false; };
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it('focuses search, opens matching paper with Enter, and closes only after a successful open', async () => {
  const onOpen = vi.fn(async () => {}), onClose = vi.fn();
  await act(async () => root.render(createElement(PaperSearch, { onOpen, onClose, onBrowse: () => {} })));
  const input = host.querySelector('input')!;
  expect(document.activeElement).toBe(input);
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'netwroks'); input.dispatchEvent(new Event('input', { bubbles: true })); });
  expect(host.querySelectorAll('[role=option]')).toHaveLength(1);
  await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  expect(onOpen).toHaveBeenCalledWith(paper); expect(onClose).toHaveBeenCalledOnce();
});
it('returns Escape to the workspace and exposes browse even without recents', async () => {
  const onClose = vi.fn(), onBrowse = vi.fn();
  await act(async () => root.render(createElement(PaperSearch, { onOpen: async () => {}, onClose, onBrowse })));
  expect(host.textContent).toContain('Your reading sessions will appear here');
  act(() => host.querySelector('dialog')!.dispatchEvent(new Event('cancel', { bubbles: true, cancelable: true })));
  expect(onClose).toHaveBeenCalledOnce();
  act(() => Array.from(host.querySelectorAll('button')).find(button => button.textContent?.includes('Browse all'))!.click());
  expect(onBrowse).toHaveBeenCalledOnce();
});

it('moves between results with Up/Down while retaining input focus and opens the selected paper', async () => {
  const onOpen = vi.fn(async () => {});
  await act(async () => root.render(createElement(PaperSearch, { onOpen, onClose: vi.fn(), onBrowse: vi.fn() })));
  const input = host.querySelector('input')!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'smith'); input.dispatchEvent(new Event('input', { bubbles: true })); });
  expect(host.querySelectorAll('[role=option]')).toHaveLength(2);
  const press = async (key: string) => { await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))); };
  const first = host.querySelector('[role=option]')!;
  act(() => first.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })));
  await press('ArrowDown');
  act(() => first.dispatchEvent(new MouseEvent('mousemove', { bubbles: true })));
  expect(input.getAttribute('aria-activedescendant')).toBe('paper-result-1');
  expect(host.querySelector('[aria-selected=true]')?.textContent).toContain('Supply chains');
  expect(document.activeElement).toBe(input);
  await press('ArrowUp');
  expect(input.getAttribute('aria-activedescendant')).toBe('paper-result-0');
  await press('ArrowDown');
  await press('Enter');
  expect(onOpen).toHaveBeenCalledWith(secondPaper);
});
