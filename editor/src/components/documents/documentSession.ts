import { pathDocument, type DocumentFile } from './documents';
import type { MainTab } from '../workbench/mainTabState';
import { currentFileHost } from '../../lib/fileTargets';
import type { Paper, ReadingSession } from '../papers/library';

type Reading = { paper: Paper; session: ReadingSession };
type SavedFile = Omit<DocumentFile, 'local' | 'onClose' | 'ownedUrl'> & { tab: MainTab; reading?: Reading };
export type DocumentSession = { files: SavedFile[]; active: string };
export const DOCUMENT_SESSION_KEY = 'diane.documents.main.v1';
const empty = (): DocumentSession => ({ files: [], active: 'chat' });
function savedReadingWorkflow(path: string): string | undefined {
  const suffix = `:reader:${path}`;
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i) || '';
    if (key.startsWith('dan.sidecar.v1:') && key.endsWith(suffix)) return key.slice('dan.sidecar.v1:'.length, -suffix.length);
  }
}

export function readDocumentSession(key: string | null): DocumentSession {
  if (!key) return empty();
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null');
    if (!value && key === DOCUMENT_SESSION_KEY) {
      const visits: unknown = JSON.parse(localStorage.getItem('dan.workspaceVisits.v1') || '[]');
      const files: SavedFile[] = Array.isArray(visits) ? [...new Set(visits)].flatMap(entry => {
        if (typeof entry !== 'string' || !/^tab:(pdf|file):/.test(entry)) return [];
        const id = entry.slice(4), path = id.slice(id.indexOf(':') + 1);
        if (!path.startsWith('/') && !/^[a-z]:[\\/]/i.test(path)) return [];
        const file = pathDocument(path);
        return [{ ...file, readingWorkflowId: savedReadingWorkflow(path), tab: { id, kind: id.startsWith('pdf:') ? 'pdf' as const : 'file' as const, label: file.name, title: path } }];
      }) : [];
      return { files, active: 'chat' };
    }
    if (!Array.isArray(value?.files)) return empty();
    const files = value.files.filter((item: SavedFile) => item && typeof item.path === 'string' && typeof item.name === 'string'
      && ['local', 'remote', 'browser'].includes(item.source) && (item.source === 'browser' || item.source === currentFileHost())
      && item.tab?.id === `${/\.pdf$/i.test(item.name) ? 'pdf' : 'file'}:${item.path}`).map((item: SavedFile) => ({ ...item, ...(item.reading?.paper?.id && item.reading?.session?.id ? {} : { reading: undefined }) }));
    return { files, active: typeof value.active === 'string' ? value.active : 'chat' };
  } catch { return empty(); }
}

export function restorePathDocuments(session: DocumentSession): Record<string, DocumentFile> {
  return Object.fromEntries(session.files.filter(file => file.source !== 'browser').map(file => [file.tab.id, {
    ...pathDocument(file.path, file.name, { source: file.source as 'local' | 'remote', root: file.root,
      ...(file.reading?.paper?.id ? { url: `/api/papers/${encodeURIComponent(file.reading.paper.id)}/pdf` } : {}) }),
    workspaceId: file.workspaceId, projectName: file.projectName, readingWorkflowId: file.readingWorkflowId,
  }]));
}

export function writeDocumentSession(key: string, tabs: MainTab[], documents: Record<string, DocumentFile>, readings: Record<string, Reading>, active: string, unavailable: SavedFile[] = []) {
  const files: SavedFile[] = tabs.flatMap(tab => {
    const file = documents[tab.id];
    if (!file) return [];
    return [{ name: file.name, path: file.path, source: file.source, url: '', root: file.root,
      workspaceId: file.workspaceId, projectName: file.projectName, readingWorkflowId: file.readingWorkflowId, tab, ...(readings[tab.id] ? { reading: readings[tab.id] } : {}) }];
  });
  files.push(...unavailable.filter(file => tabs.some(tab => tab.id === file.tab.id) && !documents[file.tab.id]));
  localStorage.setItem(key, JSON.stringify({ files, active }));
}

/** Browser-picked bytes stay on this device; object URLs themselves cannot survive restart. */
async function browserFileStore<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('diane-reading-files', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('files');
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
  return new Promise<T>((resolve, reject) => {
    const transaction = db.transaction('files', mode); const request = action(transaction.objectStore('files'));
    transaction.oncomplete = () => { db.close(); resolve(request.result); };
    transaction.onerror = transaction.onabort = () => { db.close(); reject(transaction.error || request.error); };
  });
}
export const saveBrowserDocument = (file: DocumentFile) => file.local ? browserFileStore('readwrite', store => store.put(file.local, file.path)) : Promise.resolve();
export const removeBrowserDocument = (path: string) => browserFileStore('readwrite', store => store.delete(path));
export async function restoreBrowserDocument(file: SavedFile): Promise<DocumentFile> {
  const blob = await browserFileStore<Blob | undefined>('readonly', store => store.get(file.path));
  if (!blob) throw Error(`Reopen ${file.name}: its browser copy is no longer available.`);
  const local = new File([blob], file.name, { type: blob.type });
  return { source: 'browser', name: file.name, path: file.path, local, ownedUrl: true, url: URL.createObjectURL(local), workspaceId: file.workspaceId, projectName: file.projectName, readingWorkflowId: file.readingWorkflowId };
}
