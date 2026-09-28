import { useCallback, useEffect, useRef, useState } from 'react';
import type { Paper, ReadingSession } from '../papers/library';
import { closeMainTab, type MainTab } from '../workbench/mainTabState';
import type { DocumentDraft, DocumentFile } from './documents';

/** Workspace-owned lifetimes survive tool switches; closing releases only that tab. */
export function useDocumentTabs() {
  const [mainTabs, setMainTabs] = useState<MainTab[]>([{ id: 'chat', kind: 'chat', label: 'Chat' }]);
  const [activeMainTab, setActiveMainTab] = useState('chat');
  const [libraryReadings, setLibraryReadings] = useState<Record<string, { paper: Paper; session: ReadingSession }>>({});
  const [documents, setDocuments] = useState<Record<string, DocumentFile>>({});
  const [dirtyDocuments, setDirtyDocuments] = useState<Record<string, boolean>>({});
  const documentDrafts = useRef<Record<string, DocumentDraft>>({});
  const latestDocuments = useRef(documents); latestDocuments.current = documents;
  useEffect(() => () => {
    for (const file of Object.values(latestDocuments.current)) {
      file.onClose?.();
      if (file.ownedUrl) URL.revokeObjectURL(file.url);
    }
  }, []);
  useEffect(() => {
    if (!Object.values(dirtyDocuments).some(Boolean)) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirtyDocuments]);
  const openTab = useCallback((tab: MainTab) => {
    setMainTabs(tabs => tabs.some(item => item.id === tab.id) ? tabs : [...tabs, tab]);
    setActiveMainTab(tab.id);
  }, []);
  const openDocument = useCallback((file: DocumentFile) => {
    const pdf = /\.pdf$/i.test(file.name);
    const id = `${pdf ? 'pdf' : 'file'}:${file.path}`;
    setDocuments(current => {
      const existing = current[id];
      if (existing && !file.onClose) return current;
      return { ...current, [id]: existing ? { ...existing, onClose: file.onClose } : file };
    });
    openTab({ id, kind: pdf ? 'pdf' : 'file', label: file.name, title: file.path });
  }, [openTab]);
  const closeTab = (id: string) => {
    if (mainTabs.length <= 1 || !mainTabs.some(tab => tab.id === id)) return;
    const file = documents[id];
    if (file && dirtyDocuments[file.path] && !window.confirm(`Discard unsaved edits to ${file.name}?`)) return;
    file?.onClose?.();
    if (file) {
      delete documentDrafts.current[file.path];
      if (file.ownedUrl) URL.revokeObjectURL(file.url);
      setDirtyDocuments(current => { const next = { ...current }; delete next[file.path]; return next; });
    }
    setDocuments(current => { const next = { ...current }; delete next[id]; return next; });
    setLibraryReadings(current => { const next = { ...current }; delete next[id]; return next; });
    const next = closeMainTab(mainTabs, activeMainTab, id);
    setMainTabs(next.tabs); setActiveMainTab(next.active);
  };
  return { mainTabs, setMainTabs, activeMainTab, setActiveMainTab, openTab, openDocument, closeTab,
    documents, documentDrafts, dirtyDocuments, setDirtyDocuments, libraryReadings, setLibraryReadings,
    libraryReading: libraryReadings[activeMainTab], readerFile: activeMainTab.startsWith('pdf:') ? documents[activeMainTab] ?? null : null };
}
