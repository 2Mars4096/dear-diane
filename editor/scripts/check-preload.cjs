// Run with Electron after electron:compile. Uses an isolated, hidden renderer.
const assert = require('node:assert/strict');
const { mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { app, BrowserWindow, ipcMain } = require('electron');
const data = mkdtempSync(path.join(tmpdir(), 'dan-preload-check-'));
app.setPath('userData', data);
let win;
const deadline = setTimeout(() => { console.error('Preload check timed out'); app.exit(1); }, 15000);
app.whenReady().then(async () => {
  ipcMain.handle('updates:status', () => ({ state: 'test' }));
  ipcMain.handle('fs:readFile', (_event, file) => `read:${file}`);
  ipcMain.handle('shell:fileLink', (_event, request) => request);
  ipcMain.handle('watch:start', event => { event.sender.send('watch:changed', '/fixture.md'); return true; });
  win = new BrowserWindow({ show: false, webPreferences: { preload: path.resolve(__dirname, '../dist-electron/preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false } });
  win.webContents.on('preload-error', (_event, _path, error) => { throw error; });
  await win.loadURL('data:text/html,<!doctype html><title>Preload check</title>');
  const result = await win.webContents.executeJavaScript(`(async () => {
    const api = window.electronAPI;
    const changed = new Promise(resolve => { const off = api.watch.onChange(path => { off(); resolve(path); }); });
    await api.watch.start('/fixture.md');
    return { electron: api.isElectron, node: typeof require, status: await api.updates.status(),
      read: await api.fs.readFile('/fixture.md'), link: await api.shell.fileLink({ href: '/fixture.md', root: '/fixture', menu: true }),
      changed: await changed, dropped: typeof api.fs.droppedFile, directory: typeof api.fs.droppedDirectory };
  })()`);
  assert.deepEqual(result, { electron: true, node: 'undefined', status: { state: 'test' }, read: 'read:/fixture.md', link: { href: '/fixture.md', root: '/fixture', menu: true }, changed: '/fixture.md', dropped: 'function', directory: 'function' });
  console.log('Sandboxed generated preload: IPC, watcher, isolation and drop bridges pass.');
  clearTimeout(deadline); win.destroy(); app.quit();
}).catch(error => { console.error(error); app.exit(1); });
app.on('quit', () => rmSync(data, { recursive: true, force: true }));
