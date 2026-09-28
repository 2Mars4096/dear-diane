import { requestJson, type RequestOptions } from '../../lib/http';
import { useSyncExternalStore } from 'react';
import type { PaperPdfPosition } from '../reader/lib/paper-reading-position';
import type { PaperReferenceTray } from '../reader/lib/paper-reference-tags';
import type { PaperComment } from '../reader/lib/paper-comments';

export type Paper = { id: string; key: string; title: string; authors: string; year: string; journal: string; tags: string[]; abstract: string; bibtex: string; notes: string; reading_notes?: string; note_path: string; path: string; added: string; source: string; source_root: string; available: boolean };
export type ReadingLink = { threadId: string; runId?: string; assistantId?: string };
export type ReadingSummary = { id: string; paper_id: string; title: string; last_opened: string; position: PaperPdfPosition | null; pinned: boolean; revision: number };
export type ReadingSession = ReadingSummary & { workflow_id: string; comments: PaperComment[]; references: PaperReferenceTray; link: ReadingLink };
export type PaperSource = { kind: 'hugo' | 'pdf'; path: string; name: string };
export type Library = { papers: Paper[]; sessions: ReadingSummary[]; warnings: string[]; settings: { sources: PaperSource[] }; loading: boolean; error: string };
let snapshot: Library = { papers: [], sessions: [], warnings: [], settings: { sources: [] }, loading: true, error: '' };
const listeners = new Set<() => void>();
const emit = () => listeners.forEach(listener => listener());
let pending: Promise<void> | null = null;
export const paperRequest = <T>(path: string, init?: RequestOptions) => requestJson<T>(`/api/papers${path}`, init);
export const jsonBody = (value: unknown, method = 'PUT'): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
export function refreshLibrary() {
  if (pending) return pending;
  pending = paperRequest<Omit<Library, 'loading' | 'error'>>('').then(value => { snapshot = { ...value, loading: false, error: '' }; emit(); })
    .catch(error => { snapshot = { ...snapshot, loading: false, error: String(error) }; emit(); }).finally(() => { pending = null; });
  return pending;
}
export function updateReadingSummary(session: ReadingSummary) {
  snapshot = { ...snapshot, sessions: [session, ...snapshot.sessions.filter(item => item.id !== session.id)].sort((a,b) => b.last_opened.localeCompare(a.last_opened)) }; emit();
}
let poller: ReturnType<typeof setInterval> | undefined;
const refreshVisible = () => { if (!document.hidden) void refreshLibrary(); };
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    window.addEventListener('focus', refreshVisible);
    poller = setInterval(refreshVisible, 30_000);
    void refreshLibrary();
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearInterval(poller); poller = undefined;
      window.removeEventListener('focus', refreshVisible);
    }
  };
}
export function usePaperLibrary() {
  return useSyncExternalStore(subscribe, () => snapshot);
}
export async function pinPaper(paper: Paper, pinned: boolean) {
  await paperRequest(`/${paper.id}/pin`, jsonBody({ pinned }));
  await refreshLibrary();
}
export function citation(paper: Paper) { return [paper.authors.replace(/ and /g, '; '), paper.year && `(${paper.year})`, paper.title, paper.journal].filter(Boolean).join('. '); }
export function paperReference(papers: Paper[]) {
  return papers.map(paper => `${citation(paper)}\nCitation key: @${paper.key}\nPDF: ${paper.path || 'Unavailable'}${paper.note_path ? `\nReading notes: ${paper.note_path}` : ''}${paper.abstract ? `\nAbstract: ${paper.abstract}` : ''}`).join('\n\n');
}
