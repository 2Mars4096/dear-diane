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
