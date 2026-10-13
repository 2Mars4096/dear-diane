/** Stage an electron-updater checksum-verified ZIP through the existing Mac installer. */
// Bundle operations must bypass Electron's virtual ASAR directories.
import { physicalFs } from "./physicalFs";
const fs = physicalFs.promises;
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { inspectBundle, stageBundle } from './localUpdate';
const exec = promisify(execFile);

export async function stagePublishedMacBundle(zip: string, target: string, updatesDir: string, version: string) {
  if (!path.isAbsolute(zip) || path.extname(zip) !== '.zip') throw new Error('Invalid downloaded update archive.');
  const stat = await fs.lstat(zip);
  if (!stat.isFile() || stat.size > 2 * 1024 * 1024 * 1024) throw new Error('Invalid downloaded update archive.');
  await fs.mkdir(updatesDir, { recursive: true });
  const directory = await fs.mkdtemp(path.join(updatesDir, 'release-'));
  try {
    await exec('/usr/bin/ditto', ['-x', '-k', zip, directory]);
    const entries = (await fs.readdir(directory, { withFileTypes: true })).filter(entry => entry.name !== '__MACOSX');
    if (entries.length !== 1 || !entries[0].isDirectory() || !entries[0].name.endsWith('.app')) throw new Error('The update must contain exactly one Dear Diane app.');
    const source = path.join(directory, entries[0].name);
    const info = await inspectBundle(source);
    if (info.version !== version) throw new Error('The downloaded app version does not match its release.');
    return await stageBundle(source, target, updatesDir);
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
}
