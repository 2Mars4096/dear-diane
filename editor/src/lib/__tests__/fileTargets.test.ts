// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest';
import { browserFileTarget, pathFileTarget } from '../fileTargets';
afterEach(() => document.querySelector('meta[name="dan-remote-machine"]')?.remove());
it('normalizes parent roots and retains actual names in encoded URLs', () => {
  expect(pathFileTarget('/论文 #1.pdf', 'Reading')).toMatchObject({ source: 'local', root: '/', name: 'Reading' });
  expect(pathFileTarget('/论文 #1.pdf', 'Reading').url).toContain(encodeURIComponent('论文 #1.pdf'));
  expect(pathFileTarget('C:\\notes.md').root).toBe('C:/');
  expect(pathFileTarget('/tmp/notes.md').root).toBe('/tmp');
  expect(pathFileTarget('notes.md').root).toBeUndefined();
});
it('distinguishes remote host paths from local paths and browser copies', () => {
  const meta = document.createElement('meta'); meta.name = 'dan-remote-machine'; meta.content = 'mini'; document.head.append(meta);
  expect(pathFileTarget('/home/me/paper.pdf').source).toBe('remote');
  const local = new File(['text'], 'note.txt'), copy = browserFileTarget(local);
  expect(copy).toMatchObject({ source: 'browser', local, ownedUrl: true });
  expect(copy.path).toMatch(/^browser:/); URL.revokeObjectURL(copy.url);
});
