import { expect, it } from 'vitest';
import { sessionWindowDestination } from '../../electron/sessionWindows';
it('allows only a bounded internal session destination and discards unrelated query state', () => {
  const base='http://127.0.0.1:45173';
  expect(sessionWindowDestination(`${base}/?session=s&workflow=p&other=1#notes`,base)).toBe(`${base}/?session=s&workflow=p`);
  for (const url of ['https://example.com/?session=s&workflow=p',`${base}/api?session=s&workflow=p`,`${base}/?session=s`,'javascript:alert(1)',`${base}/?session=${'s'.repeat(201)}&workflow=p`]) expect(sessionWindowDestination(url,base)).toBeNull();
});
