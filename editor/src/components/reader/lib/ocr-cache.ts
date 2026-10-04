import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/pdf';
import type { MaterialPdfOcrPage } from './pdf-ocr';

/** Blob URLs change after restart. Match the actual PDF bytes instead. */
export async function browserOcrIdentity(document: Pick<PDFDocumentProxy, 'getData'>): Promise<string> {
  const bytes = await document.getData();
  const digest = await crypto.subtle.digest('SHA-256', bytes as Uint8Array<ArrayBuffer>);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

async function withStore<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('diane-reader-ocr', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('pages', { keyPath: ['identity', 'page.page_number'] });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return new Promise<T>((resolve, reject) => {
    const transaction = db.transaction('pages', mode);
    const request = action(transaction.objectStore('pages'));
    transaction.oncomplete = () => { db.close(); resolve(request.result); };
    transaction.onerror = transaction.onabort = () => { db.close(); reject(transaction.error || request.error); };
  });
}

export async function readBrowserOcr(identity: string): Promise<MaterialPdfOcrPage[]> {
  const rows = await withStore<{ identity: string; page: MaterialPdfOcrPage }[]>('readonly', store =>
    store.getAll(IDBKeyRange.bound([identity, 1], [identity, Number.MAX_SAFE_INTEGER])));
  return rows.map(row => row.page);
}

/** Write a single completed page, preserving other tabs' saved pages. */
export async function saveBrowserOcr(identity: string, page: MaterialPdfOcrPage): Promise<void> {
  await withStore('readwrite', store => store.put({ identity, page }));
}
