import { afterEach, expect, it, vi } from 'vitest';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
const mocks = vi.hoisted(() => ({ inspect: vi.fn(), stage: vi.fn(), unpack: vi.fn() }));
vi.mock('../../electron/localUpdate', () => ({ inspectBundle: mocks.inspect, stageBundle: mocks.stage }));
vi.mock('node:child_process', () => ({ execFile: (...args: any[]) => {
  const callback = args.at(-1);
  mocks.unpack(args[1]).then(() => callback(null, '', ''), (error: Error) => callback(error));
} }));
import { stagePublishedMacBundle } from '../../electron/releaseBundle';
const roots: string[] = [];
afterEach(async () => { for (const root of roots.splice(0)) await fs.rm(root, { recursive: true, force: true }); vi.clearAllMocks(); });
it('checks the archive app identity/version before staging and removes extraction files', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'diane-release-test-')); roots.push(root);
  const zip = path.join(root, 'update.zip'); await fs.writeFile(zip, 'synthetic archive');
  mocks.unpack.mockImplementation(async (args: string[]) => fs.mkdir(path.join(args[3], 'Dear Diane.app')));
  mocks.inspect.mockResolvedValue({ version: '0.1.1' });
  mocks.stage.mockResolvedValue({ staged: '/tmp/verified.app' });
  expect(await stagePublishedMacBundle(zip, '/Applications/Dear Diane.app', root, '0.1.1')).toEqual({ staged: '/tmp/verified.app' });
  expect(mocks.stage).toHaveBeenCalledOnce();
  expect(await fs.readdir(root)).toEqual(['update.zip']);
  mocks.stage.mockClear(); mocks.inspect.mockResolvedValue({ version: '0.1.0' });
  await expect(stagePublishedMacBundle(zip, '/Applications/Dear Diane.app', root, '0.1.1')).rejects.toThrow('does not match');
  expect(mocks.stage).not.toHaveBeenCalled();
  expect(await fs.readdir(root)).toEqual(['update.zip']);
});
it('rejects a symlink archive before extraction', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'diane-release-test-')); roots.push(root);
  await fs.writeFile(path.join(root, 'file'), 'synthetic');
  await fs.symlink(path.join(root, 'file'), path.join(root, 'update.zip'));
  await expect(stagePublishedMacBundle(path.join(root, 'update.zip'), '/Applications/Dear Diane.app', root, '0.1.1')).rejects.toThrow('Invalid');
  expect(mocks.unpack).not.toHaveBeenCalled();
});
