// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest';
import { resolveFileRequest } from '../openFile';
afterEach(() => { delete window.electronAPI; document.querySelector('meta[name="dan-remote-machine"]')?.remove(); });
it('resolves encoded relative links and line suffixes on the host', async () => {
 expect(await resolveFileRequest({ href: '../report%20one.tex:14', root: '/project/src' })).toMatchObject({ path: '/project/report one.tex' });
 await expect(resolveFileRequest({ href: 'report.tex' })).rejects.toThrow('project folder');
 await expect(resolveFileRequest({ href: 'https://example.com' })).rejects.toThrow('Invalid');
});
it('never asks the local shell to resolve remote paths', async () => {
 const fileLink = vi.fn(); window.electronAPI = { shell: { fileLink } } as never;
 const meta = document.createElement('meta'); meta.name = 'dan-remote-machine'; document.head.append(meta);
 expect(await resolveFileRequest({ href: '/home/user/file.tex' })).toMatchObject({ source: 'remote', path: '/home/user/file.tex' });
 expect(fileLink).not.toHaveBeenCalled();
});
