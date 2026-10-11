// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest';
import { waitForReaderSize } from '../lib/reader-size';
afterEach(() => vi.unstubAllGlobals());
it('waits for a hidden reader to gain dimensions before allowing engine initialization', async () => {
  let notify = () => {};
  const disconnect = vi.fn();
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { notify = callback; } observe() {} disconnect = disconnect; });
  const host = document.createElement('div');
  let size = 0;
  host.getBoundingClientRect = () => ({ width: size, height: size } as DOMRect);
  let resolved = false;
  const pending = waitForReaderSize(host, new AbortController().signal).then(() => { resolved = true; });
  await Promise.resolve(); expect(resolved).toBe(false);
  size = 600; notify(); await pending;
  expect(resolved).toBe(true); expect(disconnect).toHaveBeenCalledOnce();
});
it('cancels waiting when the file closes and releases its observer', async () => {
  const disconnect = vi.fn();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect = disconnect; });
  const controller = new AbortController();
  const pending = waitForReaderSize(document.createElement('div'), controller.signal);
  controller.abort();
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  expect(disconnect).toHaveBeenCalledOnce();
});
