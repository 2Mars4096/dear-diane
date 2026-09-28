import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { execFile } from 'node:child_process';
import { BrowserWindow, Menu, clipboard, dialog, ipcMain, shell } from 'electron';
const exec = promisify(execFile);

export function resolveFileLink(href: string, root: string): string {
  if (typeof href !== 'string' || typeof root !== 'string' || !href || /[\x00-\x1f]/.test(href)) throw Error('Invalid file link.');
  let value = href;
  if (/^file:/i.test(value)) value = fileURLToPath(value);
  else {
    if (/^[a-z][a-z\d+.-]*:/i.test(value.replace(/:\d+(?::\d+)?$/, '')) && !/^[a-z]:[\\/]/i.test(value)) throw Error('Unsupported file link.');
    try { value = decodeURIComponent(value); } catch { throw Error('Invalid file link encoding.'); }
  }
  // Codex/Claude source references can include a line/column or GitHub-style anchor.
  value = value.replace(/(?::\d+(?::\d+)?|#L\d+(?:C\d+)?(?:-L?\d+)?)$/, '');
  if (/[\x00-\x1f]/.test(value) || !value || value.startsWith('//')) throw Error('Invalid file path.');
  if (value.startsWith('~/')) value = path.join(os.homedir(), value.slice(2));
  if (path.isAbsolute(value)) return path.normalize(value);
  if (!path.isAbsolute(root)) throw Error('Select a project folder to open relative file links.');
  return path.resolve(root, value);
}

export function registerFileLinks(getWindow: () => BrowserWindow | null) {
  ipcMain.handle('shell:fileLink', async (event, request: { href: string; root: string; menu?: boolean }) => {
    const window = getWindow();
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw Error('Unsupported file link sender.');
    try {
      const target = resolveFileLink(request.href, request.root);
      const stat = await fs.stat(target).catch(() => { throw Error(`File or folder not found: ${target}`); });
      if (!stat.isFile() && !stat.isDirectory()) throw Error('This link is not a regular file or folder.');
      const open = async () => { const error = await shell.openPath(target); if (error) throw Error(error); };
      if (!request.menu) { await open(); return { ok: true }; }
      const run = (action: () => Promise<unknown> | void) => () => { Promise.resolve().then(action).catch(error => {
        void dialog.showMessageBox(window, { type: 'error', message: 'Could not complete file action', detail: String(error.message || error) });
      }); };
      const items: Electron.MenuItemConstructorOptions[] = [
        { label: stat.isDirectory() ? 'Open folder' : 'Open file', click: run(open) },
        { label: process.platform === 'darwin' ? 'Reveal in Finder' : 'Show in file manager', click: run(() => shell.showItemInFolder(target)) },
      ];
      if (process.platform === 'darwin') {
        const cursor = await Promise.any(['/Applications/Cursor.app', path.join(os.homedir(), 'Applications/Cursor.app')].map(async candidate => { await fs.access(candidate); return candidate; })).catch(() => null);
        if (cursor) items.push({ label: 'Open in Cursor', click: run(() => exec('/usr/bin/open', ['-a', cursor, target])) });
      }
      items.push({ type: 'separator' }, { label: 'Copy path', click: () => clipboard.writeText(target) });
      if (stat.isFile()) items.push(
        { label: 'Copy file contents', enabled: stat.size <= 5 * 1024 * 1024, click: run(async () => {
          const bytes = await fs.readFile(target);
          if (bytes.length > 5 * 1024 * 1024 || bytes.includes(0)) throw Error('Copy contents supports text files up to 5 MB.');
          let text: string; try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { throw Error('This file is not UTF-8 text.'); }
          clipboard.writeText(text);
        }) },
        { label: 'Save as…', click: run(async () => {
          const result = await dialog.showSaveDialog(window, { defaultPath: path.basename(target) });
          if (!result.canceled && result.filePath && path.resolve(result.filePath) !== target) await fs.copyFile(target, result.filePath);
        }) },
      );
      Menu.buildFromTemplate(items).popup({ window });
      return { ok: true };
    } catch (error) { return { ok: false, error: error instanceof Error ? error.message : String(error) }; }
  });
}
