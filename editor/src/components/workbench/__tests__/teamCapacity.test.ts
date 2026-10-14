import { expect, it } from 'vitest';
import { normalizeTeamCapacity } from '../teamCapacity';
it('preserves supported capacities and migrates absent or invalid preferences to two', () => {
  for (const value of [1, 2, 3, 4]) expect(normalizeTeamCapacity(value)).toBe(value);
  for (const value of [undefined, null, {}, '4', 0, 5, 2.5, true]) expect(normalizeTeamCapacity(value)).toBe(2);
});
