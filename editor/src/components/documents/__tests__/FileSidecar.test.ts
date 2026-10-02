// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import FileSidecar from '../FileSidecar';
vi.mock('../../reader/ReaderView', () => ({ ReaderView: () => null }));
let host: HTMLDivElement, root: ReturnType<typeof createRoot>;
beforeEach(() => { Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const onAsk = vi.fn();
it('keeps missing-file failures inside the sidecar and retries', async () => {
 const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({detail:'File not found'}), {status:404})).mockResolvedValueOnce(new Response(JSON.stringify({content:'Recovered file', revision:'v1'})));
 vi.stubGlobal('fetch', fetcher);
 await act(async () => root.render(createElement(FileSidecar, { request: { href:'/tmp/missing.tex' }, active:true, onAsk })));
 expect(host.querySelector('[role=alert]')?.textContent).toContain('File not found');
 await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === 'Retry')!.click());
 expect(host.textContent).toContain('Recovered file'); expect(host.querySelector('[role=alert]')).toBeNull();
});
it('ignores a late read after switching and preserves each file for reopening', async () => {
 let resolveFirst!: (value:Response) => void;
 vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { resolveFirst = resolve; })).mockResolvedValueOnce(new Response(JSON.stringify({content:'Second file',revision:'v1'}))));
 await act(async () => root.render(createElement(FileSidecar, { request: { href:'/tmp/first.tex' }, active:true, onAsk })));
 await act(async () => root.render(createElement(FileSidecar, { request: { href:'/tmp/second.tex' }, active:true, onAsk })));
 await act(async () => resolveFirst(new Response(JSON.stringify({content:'First file',revision:'v1'}))));
 const visible = host.querySelector('.dan-side-document:not([hidden])');
 expect(visible?.textContent).toContain('Second file'); expect(visible?.textContent).not.toContain('First file');
 expect(host.querySelectorAll('select option')).toHaveLength(2);
});
