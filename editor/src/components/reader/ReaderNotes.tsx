import { useEffect, useRef, useState } from "react";
import { Pencil, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import { readerActions, useReaderState } from "./readerStore";
import type { ReaderFile } from "./ReaderView";

/** Side panel "Notes" tab: write a note for the current selection and browse the PDF's highlights. */
export function ReaderNotes({ file, header, textDocument = false, onAsk }: { file: ReaderFile; header?: ReactNode; textDocument?: boolean; onAsk?: (text: string) => void }) {
  const state = useReaderState(file.path);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!state.noteFocus) return;
    const frame = window.requestAnimationFrame(() => {
      const note = Array.from(panel.current?.querySelectorAll<HTMLElement>('[data-note-id]') ?? []).find(node => node.dataset.noteId === state.noteFocus?.commentId);
      note?.scrollIntoView({ block: "nearest", inline: "nearest" });
      note?.querySelector<HTMLButtonElement>('.wb-notes-jump')?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [file.path, state.noteFocus]);
  useEffect(() => { if (state.draft) { setText(""); window.requestAnimationFrame(() => input.current?.focus()); } }, [state.draft?.draftId]);
  const sorted = [...state.comments].sort((a, b) => a.pageNumber - b.pageNumber || (a.rects[0]?.top ?? 0) - (b.rects[0]?.top ?? 0));
  return <aside ref={panel} className="wb-activity-panel wb-reader-notes" aria-label="Notes">
    {header}
    {state.draft && <form className="wb-notes-composer" onSubmit={(event) => { event.preventDefault(); readerActions.addComment(file.path, state.draft!, text); }}>
      <q>{state.draft.quoteStatus ? 'Selected passage' : state.draft.quote}</q>
      {state.draft.quoteStatus && <small role="status">{state.draft.quoteStatus === 'pending' ? 'Transcribing quote… You can save your note now.' : 'Quote pending. Your note can be saved offline.'}{state.draft.quoteStatus === 'deferred' && <button type="button" onClick={() => window.dispatchEvent(new CustomEvent('diane:retry-quote', { detail: file.path }))}>Retry quote</button>}</small>}
      <textarea ref={input} value={text} placeholder="Add a note (optional)" aria-label="Note" onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") readerActions.setDraft(file.path, null);
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); readerActions.addComment(file.path, state.draft!, text); }
        }} />
      <footer><button type="button" onClick={() => readerActions.setDraft(file.path, null)}>Cancel</button><button type="submit">{text.trim() ? "Save note" : "Highlight"}</button></footer>
    </form>}
    {!sorted.length && !state.draft && <p className="wb-activity-empty wb-side-empty">Select text in the file and choose Annotate selection or Comment to add a note.</p>}
    <ol className="wb-notes-list">{sorted.map((item) => <li key={item.commentId} data-note-id={item.commentId} data-active={state.noteFocus?.commentId === item.commentId || undefined} data-stale={state.stale[item.commentId]}>
      <button type="button" className="wb-notes-jump" onClick={() => readerActions.focus(file.path, item)} title="Show in the file">
        <span>{textDocument ? "Passage" : `p. ${item.pageNumber}`}{state.stale[item.commentId] === "missing" ? " · not found in this version" : ""}</span>
        <q>{item.quoteStatus ? "Selected passage" : item.quote}</q>
      </button>
      {item.quoteStatus && <small>Quote pending · comment saved <button type="button" onClick={() => window.dispatchEvent(new CustomEvent('diane:retry-quote', { detail: file.path }))}>Retry quote</button></small>}
      {editing === item.commentId
        ? <form onSubmit={(event) => { event.preventDefault(); readerActions.editComment(file.path, item.commentId, editText); setEditing(null); }}>
            <textarea autoFocus value={editText} aria-label="Edit note" onChange={(event) => setEditText(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setEditing(null); if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); readerActions.editComment(file.path, item.commentId, editText); setEditing(null); } }} />
          </form>
        : item.text && <p>{item.text}</p>}
      <div className="wb-notes-actions">
        {onAsk && <button type="button" onClick={() => onAsk(`File: ${file.path}\n${textDocument ? "" : `Page ${item.pageNumber}\n`}\n> ${item.quote}\n\n${item.text}`)}>Use in chat</button>}
        <button type="button" onClick={() => { setEditing(item.commentId); setEditText(item.text); }}><Pencil size={11} />{item.text ? "Edit" : "Add note"}</button>
        <button type="button" onClick={() => readerActions.removeComment(file.path, item.commentId)}><Trash2 size={11} />Delete</button>
      </div>
    </li>)}</ol>
  </aside>;
}

/** Count for the Notes tab badge. */
export function useNoteCount(path: string): number {
  return useReaderState(path).comments.length;
}
