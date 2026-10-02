import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({ handle: vi.fn(), openPath: vi.fn().mockResolvedValue(''), popup: vi.fn(), build: vi.fn(), writeText: vi.fn() }));
vi.mock('electron', () => ({ BrowserWindow: {}, Menu: { buildFromTemplate: mock.build }, clipboard: { writeText: mock.writeText }, dialog: {}, ipcMain: { handle: mock.handle }, shell: { openPath: mock.openPath } }));
import { registerFileLinks } from '../../electron/fileLinks';
const directories: string[] = [];
afterEach(async () => { for (const dir of directories.splice(0)) await fs.rm(dir, { recursive: true, force: true }); vi.clearAllMocks(); });
async function fixture() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'dan-file-menu-')); directories.push(dir);
  await fs.writeFile(path.join(dir, 'notes.md'), 'test');
  const webContents = { mainFrame: {} }; const window = { webContents };
  mock.build.mockReturnValue({ popup: mock.popup });
  registerFileLinks(() => window as never);
  const handler = mock.handle.mock.calls[0][1];
  const event = { sender: webContents, senderFrame: webContents.mainFrame };
  return { dir, event, handler };
}
it('opens the resolved existing file and returns missing-path errors', async () => {
  const { dir, event, handler } = await fixture();
  expect(await handler(event, { href: 'notes.md:2', root: dir })).toEqual({ ok: true });
  expect(mock.openPath).toHaveBeenCalledWith(path.join(dir, 'notes.md'));
  expect(await handler(event, { href: 'missing', root: dir })).toEqual(expect.objectContaining({ ok: false }));
});
it('offers file-specific actions and a folder-specific menu without launching on right click', async () => {
  const { dir, event, handler } = await fixture();
  await handler(event, { href: 'notes.md', root: dir, menu: true });
  expect(mock.build.mock.calls[0][0].map((i: { label: string }) => i.label)).toEqual(expect.arrayContaining(['Open file', 'Copy path', 'Copy file contents', 'Save as…']));
  expect(mock.openPath).not.toHaveBeenCalled();
  await handler(event, { href: dir, root: '', menu: true });
  const labels = mock.build.mock.calls[1][0].map((i: { label: string }) => i.label);
  expect(labels).toContain('Open folder'); expect(labels).not.toContain('Copy file contents');
});
it('rejects requests outside the main app frame', async () => {
  const { dir, handler } = await fixture();
  await expect(handler({ sender: {}, senderFrame: {} }, { href: dir, root: '' })).rejects.toThrow('sender');
  expect(mock.openPath).not.toHaveBeenCalled();
});

it('resolves missing targets for sidecar errors without opening an external app', async () => {
  const { dir, event, handler } = await fixture();
  expect(await handler(event, { href: 'missing.tex:8', root: dir, resolveOnly: true })).toEqual({ ok: true, path: path.join(dir, 'missing.tex') });
  expect(mock.openPath).not.toHaveBeenCalled();
});
