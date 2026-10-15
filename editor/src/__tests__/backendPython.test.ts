import { expect, it, vi } from 'vitest';
import { selectBackendPython } from '../../electron/backendPython';
it('selects the first viable environment, skipping broken installations and duplicates', async () => {
  const probe = vi.fn(async (candidate: string) => { if (candidate === 'broken') throw new Error('timeout'); });
  expect(await selectBackendPython(['', 'broken', 'broken', 'working', 'unused'], { cwd: '/', env: {} }, probe)).toBe('working');
  expect(probe.mock.calls.map(call => call[0])).toEqual(['broken', 'working']);
});
it('reports an unusable explicit interpreter without switching accounts/environments silently', async () => {
  await expect(selectBackendPython(['explicit'], { cwd: '/', env: {} }, async () => { throw new Error('missing'); })).rejects.toThrow('DAN_PYTHON');
});
