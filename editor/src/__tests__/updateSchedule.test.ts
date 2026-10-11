import { expect, it, vi } from 'vitest';
import { updateSchedule, UPDATE_CHECK_INTERVAL } from '../../electron/updateSchedule';
import { updateNeedsAttention } from '../components/workbench/desktopUpdateStatus';

it('checks immediately, every fifteen minutes, and after a sleep without overlapping requests', async () => {
  let now = 0;
  let finish: () => void = () => {};
  const check = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  const scheduled = updateSchedule(check, () => now);
  const first = scheduled();
  now = UPDATE_CHECK_INTERVAL * 2;
  await scheduled();
  expect(check).toHaveBeenCalledTimes(1);
  finish(); await first;
  const second = scheduled(); finish(); await second;
  expect(check).toHaveBeenCalledTimes(2);
  now += UPDATE_CHECK_INTERVAL - 1; await scheduled();
  expect(check).toHaveBeenCalledTimes(2);
  now++; const third = scheduled(); finish(); await third;
  expect(check).toHaveBeenCalledTimes(3);
});
it('recovers after a failed scheduled check', async () => {
  let now = 0;
  const check = vi.fn().mockRejectedValueOnce(Error('offline')).mockResolvedValue(undefined);
  const scheduled = updateSchedule(check, () => now);
  await expect(scheduled()).rejects.toThrow('offline');
  now += UPDATE_CHECK_INTERVAL;
  await scheduled(); expect(check).toHaveBeenCalledTimes(2);
});
it('keeps attention on available and downloaded updates only', () => {
  for (const phase of ['available', 'ready']) expect(updateNeedsAttention({ phase } as any)).toBe(true);
  for (const phase of ['idle', 'checking', 'downloading', 'error', 'installing']) expect(updateNeedsAttention({ phase } as any)).toBe(false);
  expect(updateNeedsAttention(null)).toBe(false);
});
