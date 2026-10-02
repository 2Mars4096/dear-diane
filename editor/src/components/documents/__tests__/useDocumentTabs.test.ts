// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useDocumentTabs } from '../useDocumentTabs';
import { pathDocument } from '../documents';
import { browserFileTarget } from '../../../lib/fileTargets';
let tabs: ReturnType<typeof useDocumentTabs>;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  localStorage.clear();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  root = createRoot(document.createElement('div'));
  function Harness() { tabs = useDocumentTabs(); return null; }
  act(() => root.render(createElement(Harness)));
});
afterEach(() => { act(() => root.unmount()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('retains document identity on selection and deduplicates opens', () => {
  const close = vi.fn(), file = { ...pathDocument('/paper.pdf'), onClose: close };
  act(() => tabs.openDocument(file));
  expect(tabs.readerFile).toBe(file);
  act(() => tabs.setActiveMainTab('chat')); expect(tabs.readerFile).toBeNull();
  act(() => tabs.openDocument(pathDocument('/paper.pdf')));
  expect(tabs.readerFile).toBe(file); expect(tabs.mainTabs).toHaveLength(2); expect(close).not.toHaveBeenCalled();
  act(() => tabs.closeTab('pdf:/paper.pdf'));
  expect(close).toHaveBeenCalledOnce(); expect(tabs.activeMainTab).toBe('chat');
  act(() => tabs.closeTab('chat')); expect(tabs.mainTabs).toHaveLength(1);
});
it('guards dirty close/unload, keeps drafts on cancellation, and releases them on confirmation', () => {
  const file = pathDocument('/notes.md');
  act(() => tabs.openDocument(file));
  tabs.documentDrafts.current[file.path] = { text: 'edit', saved: 'old', revision: '1' };
  act(() => tabs.setDirtyDocuments({ [file.path]: true }));
  const unloading = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unloading); expect(unloading.defaultPrevented).toBe(true);
  const confirm = vi.fn(() => false); vi.stubGlobal('confirm', confirm);
  act(() => tabs.closeTab('file:/notes.md')); expect(tabs.documentDrafts.current[file.path].text).toBe('edit');
  confirm.mockReturnValue(true); act(() => tabs.closeTab('file:/notes.md'));
  expect(tabs.documents).toEqual({}); expect(tabs.documentDrafts.current).toEqual({});
  const clean = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(clean); expect(clean.defaultPrevented).toBe(false);
});
it('releases browser object URLs and reading ownership exactly once', () => {
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {}), close = vi.fn();
  const file = { ...browserFileTarget(new File(['pdf'], 'paper.pdf')), onClose: close };
  act(() => tabs.openDocument(file));
  act(() => tabs.closeTab(`pdf:${file.path}`));
  act(() => tabs.closeTab(`pdf:${file.path}`));
  expect(revoke).toHaveBeenCalledOnce(); expect(close).toHaveBeenCalledOnce();
  const another = { ...browserFileTarget(new File(['text'], 'notes.txt')), onClose: close };
  act(() => tabs.openDocument(another));
  act(() => root.render(null));
  expect(revoke).toHaveBeenCalledTimes(2); expect(close).toHaveBeenCalledTimes(2);
});
it('closes the final file into a chat placeholder without losing the dirty-close guard', () => {
  const file=pathDocument('/last.txt');
  act(()=>tabs.openDocument(file));act(()=>tabs.closeTab('chat'));
  expect(tabs.mainTabs).toHaveLength(1);
  act(()=>tabs.setDirtyDocuments({[file.path]:true}));const confirm=vi.fn(()=>false);vi.stubGlobal('confirm',confirm);
  act(()=>tabs.closeTab('file:/last.txt'));expect(tabs.documents['file:/last.txt']).toBeTruthy();
  confirm.mockReturnValue(true);act(()=>tabs.closeTab('file:/last.txt'));expect(tabs.mainTabs).toEqual([{id:'chat',kind:'chat',label:'Chat'}]);expect(tabs.documents).toEqual({});
});
it('restores reading entries and active document after a full remount, and remembers explicit closes', async () => {
  act(() => { tabs.openDocument({ ...pathDocument('/book.pdf'), workspaceId: 'research', projectName: 'Research', readingWorkflowId: 'original-project' }); tabs.openDocument(pathDocument('/notes.md')); });
  act(() => tabs.setActiveMainTab('pdf:/book.pdf'));
  act(() => root.unmount()); root = createRoot(document.createElement('div'));
  function Restored() { tabs = useDocumentTabs(); return null; }
  await act(async () => root.render(createElement(Restored)));
  expect(tabs.mainTabs.map(tab => tab.id)).toEqual(['chat', 'pdf:/book.pdf', 'file:/notes.md']);
  expect(tabs.readerFile).toMatchObject({ path: '/book.pdf', workspaceId: 'research', projectName: 'Research', readingWorkflowId: 'original-project' });
  expect(tabs.readerFile?.url).toContain('/api/workspace-files/preview');
  act(() => tabs.closeTab('pdf:/book.pdf'));
  act(() => root.unmount()); root = createRoot(document.createElement('div'));
  await act(async () => root.render(createElement(Restored)));
  expect(tabs.mainTabs.map(tab => tab.id)).toEqual(['chat', 'file:/notes.md']);
});
it('recovers older visited reading paths once without reviving explicitly closed files again', async () => {
  act(() => root.unmount());
  localStorage.removeItem('diane.documents.main.v1');
  localStorage.setItem('dan.sidecar.v1:original:reader:/old-book.pdf', JSON.stringify({ threadId: 'reading-chat' }));
  localStorage.setItem('dan.workspaceVisits.v1', JSON.stringify(['tab:pdf:/old-book.pdf', 'project:chat', 'tab:pdf:browser:gone:book.pdf']));
  root = createRoot(document.createElement('div'));
  function Restored() { tabs = useDocumentTabs(); return null; }
  await act(async () => root.render(createElement(Restored)));
  expect(tabs.documents['pdf:/old-book.pdf']).toMatchObject({ readingWorkflowId: 'original' });
  act(() => tabs.closeTab('pdf:/old-book.pdf'));
  act(() => root.unmount()); root = createRoot(document.createElement('div'));
  await act(async () => root.render(createElement(Restored)));
  expect(tabs.documents).toEqual({});
});
it('does not let the optional document sidecar overwrite main reading persistence', async () => {
  act(() => tabs.openDocument(pathDocument('/main.pdf')));
  const saved = localStorage.getItem('diane.documents.main.v1');
  const side = createRoot(document.createElement('div'));
  function Sidecar() { const state = useDocumentTabs(false); return createElement('button', { onClick: () => state.openDocument(pathDocument('/side.pdf')) }); }
  await act(async () => side.render(createElement(Sidecar)));
  expect(localStorage.getItem('diane.documents.main.v1')).toBe(saved);
  act(() => side.unmount());
});
it('guards restart until a browser copy finishes saving', async () => {
  const persistence = await import('../documentSession');
  let finish!: () => void;
  vi.spyOn(persistence, 'saveBrowserDocument').mockReturnValue(new Promise<void>(resolve => { finish = resolve; }));
  act(() => tabs.openDocument(browserFileTarget(new File(['copy'], 'saved.txt'))));
  expect(tabs.restored).toBe(false);
  const busy = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(busy); expect(busy.defaultPrevented).toBe(true);
  await act(async () => finish());
  expect(tabs.restored).toBe(true);
  const ready = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(ready); expect(ready.defaultPrevented).toBe(false);
});
