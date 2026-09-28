import { describe, expect, it, vi } from 'vitest';
vi.mock('electron', () => ({ BrowserWindow: {}, Menu: {}, clipboard: {}, dialog: {}, ipcMain: {}, shell: {} }));
import { resolveFileLink } from '../../electron/fileLinks';
describe('native conversation paths', () => {
  it('resolves folders, Unicode, spaces, and source locations', () => {
    expect(resolveFileLink('output/imagegen', '/Users/me/project')).toBe('/Users/me/project/output/imagegen');
    expect(resolveFileLink('design%20prompts/%E5%9F%8E%E5%A0%A1.md', '/project')).toBe('/project/design prompts/城堡.md');
    expect(resolveFileLink('/project/file.ts:12:3', '')).toBe('/project/file.ts');
    expect(resolveFileLink('file.ts:12', '/project')).toBe('/project/file.ts');
    expect(resolveFileLink('file:///project/a%20b.md#L12', '')).toBe('/project/a b.md');
  });
  it('rejects missing project context, protocols and control characters', () => {
    for (const href of ['javascript:alert(1)', 'https://example.com', 'data:text/plain,a', '//host/path', '/a%00b']) expect(() => resolveFileLink(href, '/project')).toThrow();
    expect(() => resolveFileLink('output/imagegen', '')).toThrow('Select a project');
  });
});
