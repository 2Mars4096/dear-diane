import { EventEmitter } from 'node:events';
import { expect, it, vi } from 'vitest';
import type { BrowserWindow } from 'electron';
import { prepareWindowAppearance } from '../../electron/windowAppearance';

function fixture() {
  const contents = Object.assign(new EventEmitter(), { executeJavaScript: vi.fn(async () => '#0D0D0D') });
  const window = Object.assign(new EventEmitter(), {
    webContents: contents, isDestroyed: vi.fn(() => false), isMinimized: vi.fn(() => false),
    restore: vi.fn(), show: vi.fn(), focus: vi.fn(), setBackgroundColor: vi.fn(),
  });
  return { window, reveal: prepareWindowAppearance(window as unknown as BrowserWindow) };
}

it('waits for the themed document after first paint, even with early reveal requests', async () => {
  const { window, reveal } = fixture();
  window.emit('ready-to-show'); reveal();
  expect(window.show).not.toHaveBeenCalled();
  window.webContents.emit('did-finish-load');
  await vi.waitFor(() => expect(window.show).toHaveBeenCalledOnce());
  expect(window.setBackgroundColor).toHaveBeenCalledWith('#0D0D0D');
  expect(window.setBackgroundColor.mock.invocationCallOrder[0]).toBeLessThan(window.show.mock.invocationCallOrder[0]);
  window.isMinimized.mockReturnValue(true); reveal();
  expect(window.restore).toHaveBeenCalledOnce();
  expect(window.show).toHaveBeenCalledTimes(2);
});

it('waits for first paint if the document finishes first', async () => {
  const { window } = fixture();
  window.webContents.emit('did-finish-load');
  await vi.waitFor(() => expect(window.setBackgroundColor).toHaveBeenCalled());
  expect(window.show).not.toHaveBeenCalled();
  window.emit('ready-to-show');
  expect(window.show).toHaveBeenCalledOnce();
});

it('does not reveal a window destroyed during loading', async () => {
  const { window } = fixture();
  window.isDestroyed.mockReturnValue(true);
  window.emit('ready-to-show'); window.webContents.emit('did-finish-load');
  await Promise.resolve();
  expect(window.show).not.toHaveBeenCalled();
  expect(window.setBackgroundColor).not.toHaveBeenCalled();
});

it('keeps the native fallback if the renderer color is unavailable', async () => {
  const { window } = fixture();
  window.webContents.executeJavaScript.mockRejectedValue(new Error('document unavailable'));
  window.emit('ready-to-show'); window.webContents.emit('did-finish-load');
  await vi.waitFor(() => expect(window.show).toHaveBeenCalledOnce());
  expect(window.setBackgroundColor).not.toHaveBeenCalled();
});
