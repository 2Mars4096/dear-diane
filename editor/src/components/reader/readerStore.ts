// Shared state between the PDF in the main area and the Notes tab in the side panel.
// Lives in the lazy reader chunk; keyed by file path so several PDFs can be open as tabs.
import { useSyncExternalStore } from "react";
import { readPaperComments, writePaperComments, type PaperComment, type PaperCommentAnchor, type PdfSelectionAnchor } from "./lib/paper-comments";

export type ReaderDraft = PdfSelectionAnchor & { anchor: PaperCommentAnchor | null };
export type ReaderFocus = { commentId: string; pageNumber: number; requestId: number; top: number };
export type ReaderState = { comments: PaperComment[]; stale: Record<string, "moved" | "missing">; draft: ReaderDraft | null; focus: ReaderFocus | null };

const states = new Map<string, ReaderState>();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());

function ensure(path: string): ReaderState {
  let state = states.get(path);
  if (!state) {
    state = { comments: readPaperComments({ material_id: path }), stale: {}, draft: null, focus: null };
    states.set(path, state);
  }
  return state;
}
function update(path: string, patch: Partial<ReaderState>) {
  states.set(path, { ...ensure(path), ...patch });
  emit();
}

export function useReaderState(path: string): ReaderState {
  return useSyncExternalStore((listener) => { listeners.add(listener); return () => listeners.delete(listener); }, () => ensure(path));
}

export const readerActions = {
  setComments(path: string, comments: PaperComment[]) {
    update(path, { comments });
    try { writePaperComments({ material_id: path }, comments); } catch { /* still shown for this visit */ }
    window.dispatchEvent(new CustomEvent("dan:reader-progress", { detail: path }));
  },
  addComment(path: string, draft: ReaderDraft, text: string) {
    const now = new Date().toISOString();
    const { anchor, ...selection } = draft;
    readerActions.setComments(path, [...ensure(path).comments, { ...selection, anchor, commentId: crypto.randomUUID(), createdAt: now, materialId: path, text: text.trim(), updatedAt: now }].slice(-200));
    update(path, { draft: null });
  },
  editComment(path: string, commentId: string, text: string) {
    readerActions.setComments(path, ensure(path).comments.map((comment) => comment.commentId === commentId ? { ...comment, text: text.trim(), updatedAt: new Date().toISOString() } : comment));
  },
  removeComment(path: string, commentId: string) {
    readerActions.setComments(path, ensure(path).comments.filter((comment) => comment.commentId !== commentId));
  },
  setDraft(path: string, draft: ReaderDraft | null) { update(path, { draft }); },
  setStale(path: string, commentId: string, value: "moved" | "missing" | null) {
    const stale = { ...ensure(path).stale };
    if (value) stale[commentId] = value; else delete stale[commentId];
    if (JSON.stringify(stale) !== JSON.stringify(ensure(path).stale)) update(path, { stale });
  },
  focus(path: string, comment: PaperComment) {
    update(path, { focus: { commentId: comment.commentId, pageNumber: comment.pageNumber, requestId: Date.now(), top: comment.rects[0]?.top ?? 0 } });
  },
  forget(path: string) { states.delete(path); },
};
