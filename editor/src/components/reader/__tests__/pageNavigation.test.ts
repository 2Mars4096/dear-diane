// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { ReaderPageNavigation } from '../ReaderPageNavigation';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it('supports full page entry and commits a scrub only on release', async () => {
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host); const onGo = vi.fn();
  try {
    await act(async () => root.render(createElement(ReaderPageNavigation, { page: 205, total: 1205, onGo, previous() {}, next() {}, canPrevious: true, canNext: true })));
    const entry = host.querySelector<HTMLInputElement>('[aria-label="Page number"]')!;
    expect(entry.style.width).toBe('7ch'); expect(entry.value).toBe('205');
    await act(async () => { entry.value = '1200'; entry.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); });
    expect(onGo).toHaveBeenLastCalledWith(1200); onGo.mockClear();
    await act(async () => host.querySelector('.wb-reader-page-navigation')!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })));
    const slider = host.querySelector<HTMLInputElement>('[aria-label="Navigate pages"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(slider, '600'); slider.dispatchEvent(new Event('input', { bubbles: true })); });
    expect(onGo).not.toHaveBeenCalled();
    await act(async () => slider.dispatchEvent(new Event('pointerup', { bubbles: true })));
    expect(onGo).toHaveBeenLastCalledWith(600);
  } finally { await act(async () => root.unmount()); host.remove(); }
});
