import { DOCUMENT_SESSION_KEY, readDocumentSession, restorePathDocuments, writeDocumentSession, saveBrowserDocument, removeBrowserDocument, restoreBrowserDocument } from "./documentSession";
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Paper, ReadingSession } from '../papers/library';
import { closeMainTab, type MainTab } from '../workbench/mainTabState';
import type { DocumentDraft, DocumentFile } from './documents';

/** Workspace-owned lifetimes survive tool switches; closing releases only that tab. */
export function useDocumentTabs(keyboard = true, persistenceKey: string | null = keyboard ? DOCUMENT_SESSION_KEY : null) {
  const [saved] = useState(() => readDocumentSession(persistenceKey));
  const [restored, setRestored] = useState(!saved.files.length);
  const [restoreError, setRestoreError] = useState("");
  const [pendingSaves, setPendingSaves] = useState(0);
  const [mainTabs, setMainTabs] = useState<MainTab[]>(() => [{ id: 'chat', kind: 'chat', label: 'Chat' }, ...saved.files.map(file => file.tab)]);
  const [activeMainTab, setActiveMainTab] = useState(() => saved.files.some(file => file.tab.id === saved.active) ? saved.active : 'chat');
  const [libraryReadings, setLibraryReadings] = useState<Record<string, { paper: Paper; session: ReadingSession }>>(() => Object.fromEntries(saved.files.filter(file => file.reading).map(file => [file.tab.id, file.reading!])));
  const [documents, setDocuments] = useState<Record<string, DocumentFile>>(() => restorePathDocuments(saved));
  const [dirtyDocuments, setDirtyDocuments] = useState<Record<string, boolean>>({});
  const documentDrafts = useRef<Record<string, DocumentDraft>>({});
  const latestDocuments = useRef(documents); latestDocuments.current = documents;
  const closedDuringRestore = useRef(new Set<string>());
  const restoreMounted = useRef(false);
  useEffect(() => {
    let disposed = false;
    restoreMounted.current = true;
    void (async () => {
      const errors: string[] = [];
      for (const file of saved.files) {
        try {
          if (file.source === 'browser') {
            const restoredFile = await restoreBrowserDocument(file);
            if (disposed || closedDuringRestore.current.has(file.tab.id)) URL.revokeObjectURL(restoredFile.url);
            else setDocuments(current => ({ ...current, [file.tab.id]: restoredFile }));
          } else if (file.reading) {
            const { openLibraryPaper, closeLibraryPaper } = await import('../papers/reading');
            if (disposed || closedDuringRestore.current.has(file.tab.id)) continue;
            const reading = await openLibraryPaper(file.reading.paper.id);
            if (disposed || closedDuringRestore.current.has(file.tab.id)) { if (!restoreMounted.current || closedDuringRestore.current.has(file.tab.id)) void closeLibraryPaper(file.reading.paper.id); continue; }
            setLibraryReadings(current => ({ ...current, [file.tab.id]: reading }));
            setDocuments(current => ({ ...current, [file.tab.id]: { ...current[file.tab.id], onClose: () => { void closeLibraryPaper(file.reading!.paper.id); } } }));
          }
        } catch (error) { errors.push(error instanceof Error ? error.message : String(error)); }
      }
      if (!disposed) { setRestoreError(errors.join(' ')); setRestored(true); }
    })();
    return () => { disposed = true; restoreMounted.current = false; };
  }, [saved]);
  useEffect(() => {
    if (!persistenceKey || !restored) return;
    try { writeDocumentSession(persistenceKey, mainTabs, documents, libraryReadings, activeMainTab, saved.files); }
    catch { setRestoreError('Reading tabs could not be saved on this device.'); }
  }, [persistenceKey, restored, mainTabs, documents, libraryReadings, activeMainTab, saved]);
  useEffect(() => () => {
    for (const file of Object.values(latestDocuments.current)) {
      file.onClose?.();
      if (file.ownedUrl) URL.revokeObjectURL(file.url);
    }
  }, []);
  useEffect(() => {
    if (!pendingSaves && !Object.values(dirtyDocuments).some(Boolean)) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirtyDocuments, pendingSaves]);
  const openTab = useCallback((tab: MainTab) => {
    setMainTabs(tabs => tabs.some(item => item.id === tab.id) ? tabs : [...tabs, tab]);
    setActiveMainTab(tab.id);
  }, []);
  const openDocument = useCallback((file: DocumentFile) => {
    if (persistenceKey && file.source === 'browser') {
      setPendingSaves(count => count + 1);
      void saveBrowserDocument(file).catch(() => setRestoreError(`Keep a copy of ${file.name}: browser storage could not save it for restart.`)).finally(() => setPendingSaves(count => count - 1));
    }
    const pdf = /\.pdf$/i.test(file.name);
    const id = `${pdf ? 'pdf' : 'file'}:${file.path}`;
    setDocuments(current => {
      const existing = current[id];
      if (existing && !file.onClose) return current;
      return { ...current, [id]: existing ? { ...existing, onClose: file.onClose } : file };
    });
    openTab({ id, kind: pdf ? 'pdf' : 'file', label: file.name, title: file.path });
  }, [openTab, persistenceKey]);
  const closeTab = (id: string) => {
    if (!mainTabs.some(tab => tab.id === id) || (mainTabs.length === 1 && mainTabs[0].kind === 'chat')) return;
    const file = documents[id];
    if (file && dirtyDocuments[file.path] && !window.confirm(`Discard unsaved edits to ${file.name}?`)) return;
    closedDuringRestore.current.add(id);
    file?.onClose?.();
    if (persistenceKey && file?.source === 'browser') void removeBrowserDocument(file.path).catch(() => {});
    if (file) {
      delete documentDrafts.current[file.path];
      if (file.ownedUrl) URL.revokeObjectURL(file.url);
      setDirtyDocuments(current => { const next = { ...current }; delete next[file.path]; return next; });
    }
    setDocuments(current => { const next = { ...current }; delete next[id]; return next; });
    setLibraryReadings(current => { const next = { ...current }; delete next[id]; return next; });
    const closableTabs: MainTab[] = mainTabs.length === 1 ? [...mainTabs, { id:'chat', kind:'chat', label:'Chat' }] : mainTabs;
    const next = closeMainTab(closableTabs, activeMainTab, id);
    setMainTabs(next.tabs); setActiveMainTab(next.active);
  };
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!keyboard) return;
      const mac = /Mac|iPhone|iPad/.test(navigator.platform);
      const modifiers = mac ? event.metaKey && event.ctrlKey && !event.altKey : event.ctrlKey && event.altKey && !event.metaKey;
      if (event.code !== 'KeyW' || !modifiers || event.shiftKey || event.repeat || event.isComposing || event.defaultPrevented || document.querySelector('dialog[open], [role=dialog][aria-modal=true]')) return;
      event.preventDefault(); closeTab(activeMainTab);
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  }, [activeMainTab, closeTab]);
  return { restored: restored && pendingSaves === 0, restoreError, mainTabs, setMainTabs, activeMainTab, setActiveMainTab, openTab, openDocument, closeTab,
    documents, documentDrafts, dirtyDocuments, setDirtyDocuments, libraryReadings, setLibraryReadings,
    libraryReading: libraryReadings[activeMainTab], readerFile: restored && activeMainTab.startsWith('pdf:') ? documents[activeMainTab] ?? null : null };
}
