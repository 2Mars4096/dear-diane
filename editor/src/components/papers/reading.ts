import { useSyncExternalStore } from 'react';
import { readPaperPdfPosition, writePaperPdfPosition, clearPaperPdfPosition } from '../reader/lib/paper-reading-position';
import { readPaperComments, writePaperComments, normalizePaperComments } from '../reader/lib/paper-comments';
import { readPaperReferenceTray, writePaperReferenceTray, normalizePaperReferenceTray } from '../reader/lib/paper-reference-tags';
import { readerActions } from '../reader/readerStore';
import { jsonBody, paperRequest, updateReadingSummary, type Paper, type ReadingSession, type ReadingLink } from './library';

type Progress = Pick<ReadingSession, 'position' | 'comments' | 'references'>;
type Sync = { session: ReadingSession; paper: Paper; sent: string; pending: Promise<void> | null; active: boolean; blocked: boolean; timer?: ReturnType<typeof setTimeout>; status: string };
const syncing = new Map<string, Sync>();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach(listener => listener());
const key = (id: string) => `dan.papers.pending.v1:${id}`;
const snapshot = (path: string): Progress => ({ position: readPaperPdfPosition(path), comments: readPaperComments({ material_id: path }), references: readPaperReferenceTray(path) });
function preserve(sync: Sync, progress: Progress) {
  if (sync.blocked) return;
  try { localStorage.setItem(key(sync.session.id), JSON.stringify({ revision: sync.session.revision, ...progress })); } catch { /* Server save still runs. */ }
}
function discardClosed(sync: Sync) {
  if (!sync.active && !sync.pending && !sync.blocked && JSON.stringify(snapshot(sync.paper.path)) === sync.sent && syncing.get(sync.session.id) === sync) {
    syncing.delete(sync.session.id);
  }
}
function flush(sync: Sync, keepalive = false): Promise<void> {
  if (sync.pending) return sync.pending;
  if (sync.blocked) return Promise.resolve();
  const progress = snapshot(sync.paper.path);
  const serialized = JSON.stringify(progress);
  if (serialized === sync.sent) { discardClosed(sync); return Promise.resolve(); }
  preserve(sync, progress);
  sync.status = 'Saving reading session…'; emit();
  sync.pending = (async () => {
    try {
      sync.session = await paperRequest<ReadingSession>(`/sessions/${sync.session.id}/progress`, { ...jsonBody({ revision: sync.session.revision, ...progress }), keepalive });
      sync.sent = serialized;
      if (JSON.stringify(snapshot(sync.paper.path)) === serialized) localStorage.removeItem(key(sync.session.id));
      sync.status = 'Reading session saved'; updateReadingSummary(sync.session);
    } catch (error) {
      sync.status = String(error);
      sync.blocked = sync.status.includes('changed elsewhere');
    } finally { sync.pending = null; emit(); }
    // Coalesce edits during a request, without retrying failed saves in a loop.
    if (sync.sent === serialized && JSON.stringify(snapshot(sync.paper.path)) !== serialized) await flush(sync);
    discardClosed(sync);
  })();
  return sync.pending;
}
export async function closeLibraryPaper(id: string) {
  const sync = syncing.get(id);
  if (!sync) return;
  sync.active = false;
  clearTimeout(sync.timer);
  await flush(sync);
  discardClosed(sync);
}
let listening = false;
function listen() {
  if (listening) return;
  listening = true;
  window.addEventListener('dan:reader-progress', ((event: CustomEvent<string>) => {
    for (const sync of syncing.values()) {
      if (sync.paper.path !== event.detail) continue;
      preserve(sync, snapshot(sync.paper.path));
      clearTimeout(sync.timer);
      sync.timer = setTimeout(() => void flush(sync), 500);
    }
  }) as EventListener);
  window.addEventListener('pagehide', () => { for (const sync of syncing.values()) void flush(sync, true); });
  window.addEventListener('online', () => { for (const sync of syncing.values()) void flush(sync); });
}
const opening = new Map<string, Promise<{ paper: Paper; session: ReadingSession }>>();
export function openLibraryPaper(id: string) {
  const pending = opening.get(id);
  if (pending) return pending;
  const request = openReading(id).finally(() => opening.delete(id));
  opening.set(id, request);
  return request;
}
async function openReading(id: string): Promise<{ paper: Paper; session: ReadingSession }> {
  const existing = syncing.get(id);
  if (existing?.active) {
    if (existing.blocked) throw new Error(existing.status);
    await flush(existing);
    // Keep a live reader's current state; opening another tab must not overwrite it.
    const opened = await paperRequest<{ paper: Paper; session: ReadingSession }>(`/${id}/open`, { method: 'POST' });
    existing.session.last_opened = opened.session.last_opened;
    updateReadingSummary(existing.session);
    return { paper: opened.paper, session: existing.session };
  }
  if (existing) {
    // Keep ownership while a final save settles; close must not drop a reopened session.
    existing.active = true;
    await flush(existing);
    existing.active = false;
  }
  const opened = await paperRequest<{ paper: Paper; session: ReadingSession }>(`/${id}/open`, { method: 'POST' });
  const { paper, session } = opened;
  // Hydration writes emit reader-progress events. Retire the old listener first.
  if (existing) { clearTimeout(existing.timer); syncing.delete(id); }
  let progress: Progress = session.revision ? { position: session.position, comments: normalizePaperComments(session.comments, paper.path), references: normalizePaperReferenceTray(session.references, paper.path) } : snapshot(paper.path);
  let conflict = false;
  try {
    const pending = JSON.parse(localStorage.getItem(key(id)) || 'null');
    if (pending) {
      if (pending.revision === session.revision) progress = { position: pending.position, comments: normalizePaperComments(pending.comments, paper.path), references: normalizePaperReferenceTray(pending.references, paper.path) };
      else conflict = true;
    }
  } catch { /* Use server state if the recovery record is invalid. */ }
  if (progress.position) writePaperPdfPosition(paper.path, progress.position);
  else clearPaperPdfPosition(paper.path);
  writePaperComments({ material_id: paper.path }, progress.comments);
  writePaperReferenceTray(paper.path, progress.references);
  readerActions.forget(paper.path);
  const sync: Sync = { session, paper, sent: JSON.stringify({ position: session.position, comments: normalizePaperComments(session.comments, paper.path), references: normalizePaperReferenceTray(session.references, paper.path) }), pending: null, active: true, blocked: conflict,
    status: conflict ? 'Saved session loaded. Unsynced notes from another revision remain on this device; download them before continuing.' : 'Reading session saved' };
  syncing.set(id, sync); listen(); updateReadingSummary(session); emit();
  if (!conflict) void flush(sync);
  return opened;
}
export async function saveReadingLink(id: string, link: ReadingLink) {
  await paperRequest(`/sessions/${id}/link`, jsonBody(link));
  const sync = syncing.get(id);
  if (sync) sync.session.link = link;
}
export function retryReadingSave(id: string) { const sync = syncing.get(id); if (sync && !sync.blocked) void flush(sync); }
export function downloadPendingReading(id: string) {
  const value = localStorage.getItem(key(id));
  if (!value) return;
  const url = URL.createObjectURL(new Blob([value], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = `reading-${id}-unsynced.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function useReadingStatus(id: string) {
  return useSyncExternalStore(listener => { listeners.add(listener); return () => { listeners.delete(listener); }; }, () => syncing.get(id)?.status ?? 'Reading session');
}

export function reloadSavedReading(id: string) {
  downloadPendingReading(id);
  localStorage.removeItem(key(id));
  const sync = syncing.get(id);
  if (sync) sync.blocked = true;
  window.location.reload();
}
