// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import FileOpener from '../FileOpener';
import { nativeFs } from '../../../lib/electronBridge';
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const onOpen = vi.fn(), onFolder = vi.fn();
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement('div'); document.body.append(host);
  root = createRoot(host);
  act(() => root.render(createElement(FileOpener, { onOpen, onFolder })));
});
afterEach(() => { act(() => root.unmount()); host.remove(); document.querySelector('meta')?.remove(); vi.restoreAllMocks(); vi.clearAllMocks(); });
async function drag(type: string, directory = true, count = 1, target: EventTarget = window) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: {
    types: ['Files'], files: Array.from({ length: count }, () => new File(['hello'], directory ? '研究 Project' : 'paper.pdf')),
    items: [{ kind: 'file', webkitGetAsEntry: () => ({ isDirectory: directory }) }],
  } });
  await act(async () => { target.dispatchEvent(event); });
}
it('shows a boundary and opens a native folder as a project, without opening a document', async () => {
  vi.spyOn(nativeFs, 'droppedDirectory').mockResolvedValue('/Users/me/研究 Project');
  await drag('dragenter'); expect(host.querySelector('.dan-file-drop')).not.toBeNull();
  await drag('drop');
  expect(onFolder).toHaveBeenCalledWith('/Users/me/研究 Project'); expect(onOpen).not.toHaveBeenCalled();
  expect(host.querySelector('.dan-file-drop')).toBeNull();
});
it('preserves PDF opening', async () => {
  vi.spyOn(nativeFs, 'droppedFile').mockResolvedValue('/Users/me/paper.pdf');
  await drag('drop', false);
  expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ name: 'paper.pdf' })); expect(onFolder).not.toHaveBeenCalled();
});
it('rejects multiple folders and unavailable paths', async () => {
  const resolve = vi.spyOn(nativeFs, 'droppedDirectory').mockResolvedValue(null);
  await drag('drop', true, 2); expect(resolve).not.toHaveBeenCalled();
  expect(host.textContent).toContain('one folder');
  await drag('drop'); expect(host.textContent).toContain('path is unavailable'); expect(onFolder).not.toHaveBeenCalled();
});
it('does not register a local folder on a remote host', async () => {
  const meta = document.createElement('meta'); meta.name = 'dan-remote-machine'; meta.content = 'mini'; document.head.append(meta);
  const resolve = vi.spyOn(nativeFs, 'droppedDirectory');
  await drag('drop'); expect(resolve).not.toHaveBeenCalled(); expect(onFolder).not.toHaveBeenCalled(); expect(host.textContent).toContain('Browse folders');
});
it('leaves project settings drops to their own handler and clears cancelled drags', async () => {
  const target = document.createElement('div'); target.className = 'wb-folder-drop'; host.append(target);
  const resolve = vi.spyOn(nativeFs, 'droppedDirectory');
  await drag('drop', true, 1, target); expect(resolve).not.toHaveBeenCalled();
  await drag('dragenter'); await act(async () => { window.dispatchEvent(new Event('blur')); });
  expect(host.querySelector('.dan-file-drop')).toBeNull();
});
it('leaves literature PDF drops to the import panel', async () => {
  const target = document.createElement('section'); target.dataset.literatureDrop = ''; host.append(target);
  const resolve = vi.spyOn(nativeFs, 'droppedFile');
  await drag('dragenter', false, 1, target);
  expect(host.querySelector('.dan-file-drop')).toBeNull();
  await drag('drop', false, 1, target);
  expect(resolve).not.toHaveBeenCalled(); expect(onOpen).not.toHaveBeenCalled();
});
