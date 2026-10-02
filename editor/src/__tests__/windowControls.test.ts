import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const native = vi.hoisted(() => ({ on: vi.fn(), cursor: { x: 400, y: 110 } }));
vi.mock('electron', () => ({ ipcMain: { on: native.on }, screen: { getCursorScreenPoint: () => native.cursor } }));
import { registerWindowControls } from '../../electron/windowControls';
beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal('process', { ...process, platform: 'darwin' }); native.cursor = { x: 400, y: 110 }; });
afterEach(() => vi.unstubAllGlobals());
function fixture() {
  const window = {
    webContents: { mainFrame: {} }, isDestroyed: () => false, isFullScreen: vi.fn(() => false),
    isFocused: vi.fn(() => true), isMaximized: vi.fn(() => false),
    maximize: vi.fn(), unmaximize: vi.fn(), setPosition: vi.fn(),
    getBounds: vi.fn(() => ({ x: 100, y: 80, width: 1000, height: 650 })),
  };
  registerWindowControls(() => window as any);
  const handler = native.on.mock.calls[0][1];
  const event = { sender: window.webContents, senderFrame: window.webContents.mainFrame };
  return { window, send: (action: string, source = event) => handler(source, action) };
}
it('expands and restores, without leaving fullscreen', () => {
  const { window, send } = fixture();
  send('toggle'); expect(window.maximize).toHaveBeenCalledOnce();
  window.isMaximized.mockReturnValue(true); send('toggle'); expect(window.unmaximize).toHaveBeenCalledOnce();
  window.isFullScreen.mockReturnValue(true); send('toggle'); expect(window.unmaximize).toHaveBeenCalledOnce();
});
it('rejects child frames and other windows', () => {
  const { window, send } = fixture();
  send('toggle', { sender: window.webContents, senderFrame: {} });
  send('toggle', { sender: { mainFrame: {} }, senderFrame: window.webContents.mainFrame });
  expect(window.maximize).not.toHaveBeenCalled();
});
it('ignores click jitter, moves by cursor delta, and stops on release or blur', () => {
  const { window, send } = fixture();
  send('begin'); native.cursor = { x: 402, y: 111 }; send('move'); expect(window.setPosition).not.toHaveBeenCalled();
  native.cursor = { x: 420, y: 140 }; send('move'); expect(window.setPosition).toHaveBeenLastCalledWith(120, 110);
  send('end'); send('move'); expect(window.setPosition).toHaveBeenCalledOnce();
  send('begin'); window.isFocused.mockReturnValue(false); send('move');
  window.isFocused.mockReturnValue(true); send('move'); expect(window.setPosition).toHaveBeenCalledOnce();
});
it('restores a maximized window under the pointer when dragging starts', () => {
  const { window, send } = fixture();
  window.isMaximized.mockReturnValue(true);
  window.getBounds.mockReturnValueOnce({ x: 0, y: 0, width: 1600, height: 1000 });
  send('begin'); native.cursor = { x: 440, y: 150 }; send('move');
  expect(window.unmaximize).toHaveBeenCalledOnce();
  expect(window.setPosition).toHaveBeenCalledWith(190, 40);
});
it('uses the sending window for titlebar controls in a multiwindow app', () => {
  const first = fixture().window;
  const second = { ...first, webContents: { mainFrame: {} }, maximize: vi.fn() };
  native.on.mockClear();
  registerWindowControls(sender => (sender === second.webContents ? second : first) as any);
  native.on.mock.calls[0][1]({ sender: second.webContents, senderFrame: second.webContents.mainFrame }, 'toggle');
  expect(second.maximize).toHaveBeenCalledOnce(); expect(first.maximize).not.toHaveBeenCalled();
});
