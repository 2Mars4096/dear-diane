// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest';
import { epubNavigationKey, epubWheelNavigation } from '../lib/epub-input';
afterEach(() => { vi.restoreAllMocks(); document.body.replaceChildren(); });
it('turns from a toolbar button but preserves editing, selection extension and slider keys', () => {
  const turn = vi.fn();
  const button = document.createElement('button'); document.body.append(button);
  const handler = (e: KeyboardEvent) => epubNavigationKey(e, turn);
  document.addEventListener('keydown', handler);
  try {
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    expect(turn).toHaveBeenLastCalledWith(1);
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', shiftKey: true, bubbles: true }));
    const input = document.createElement('input'); input.type = 'range'; document.body.append(input);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    expect(turn).toHaveBeenCalledTimes(1);
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(turn).toHaveBeenLastCalledWith(-1);
  } finally { document.removeEventListener('keydown', handler); }
});
it('coalesces wheel bursts, supports reverse scrolling and leaves pinch zoom alone', () => {
  let now = 1000; vi.spyOn(performance, 'now').mockImplementation(() => now);
  const turn = vi.fn(), wheel = epubWheelNavigation(turn);
  for (let i = 0; i < 10; i++) wheel(new WheelEvent('wheel', { deltaY: 30, cancelable: true }));
  expect(turn).toHaveBeenCalledTimes(1); expect(turn).toHaveBeenLastCalledWith(1);
  now += 300; wheel(new WheelEvent('wheel', { deltaY: -80, cancelable: true }));
  expect(turn).toHaveBeenLastCalledWith(-1);
  now += 300; const pinch = new WheelEvent('wheel', { deltaY: 80 });
  // Happy DOM's WheelEvent omits MouseEvent modifier initialization.
  Object.defineProperty(pinch, 'ctrlKey', { value: true }); wheel(pinch);
  expect(turn).toHaveBeenCalledTimes(2);
});
