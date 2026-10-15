import { expect, it } from 'vitest';
import { namedWorkers, nextWorkerName } from '../workerNames';
import { EMPTY_PROFILE } from '../modelSelection';
it('fills the missing first name, preserves custom names and avoids duplicate defaults', () => {
  const profiles = { first: { ...EMPTY_PROFILE, enabled: true, backend: 'claude', name: '' }, second: { ...EMPTY_PROFILE, enabled: true, name: 'Worker 2' }, custom: { ...EMPTY_PROFILE, enabled: true, name: 'Visual reviewer' }, hidden: { ...EMPTY_PROFILE } };
  const named = namedWorkers(profiles);
  expect(named.first.name).toBe('Worker 1');
  expect(named.second.name).toBe('Worker 2');
  expect(named.custom.name).toBe('Visual reviewer');
  expect(named.hidden.name).toBeUndefined();
  expect(nextWorkerName(named)).toBe('Worker 3');
  expect(namedWorkers(named)).toEqual(named);
});
