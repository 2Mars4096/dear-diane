import { useEffect, useRef, useState } from "react";
import { Pencil, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import { readerActions, useReaderState } from "./readerStore";
import type { ReaderFile } from "./ReaderView";

/** Side panel "Notes" tab: write a note for the current selection and browse the PDF's highlights. */
export function ReaderNotes({ file, header }: { file: ReaderFile; header?: ReactNode }) {
  const state = useReaderState(file.path);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (state.draft) { setText(""); window.requestAnimationFrame(() => input.current?.focus()); } }, [state.draft]);
  const sorted = [...state.comments].sort((a, b) => a.pageNumber - b.pageNumber || (a.rects[0]?.top ?? 0) - (b.rects[0]?.top ?? 0));
  return <aside className="wb-activity-panel wb-reader-notes" aria-label="Notes">
    {header}
    {state.draft && <form className="wb-notes-composer" onSubmit={(event) => { event.preventDefault(); readerActions.addComment(file.path, state.draft!, text); }}>
      <q>{state.draft.quote}</q>
      <textarea ref={input} value={text} placeholder="Add a note (optional)" aria-label="Note" onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") readerActions.setDraft(file.path, null);
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); readerActions.addComment(file.path, state.draft!, text); }
        }} />
      <footer><button type="button" onClick={() => readerActions.setDraft(file.path, null)}>Cancel</button><button type="submit">{text.trim() ? "Save note" : "Highlight"}</button></footer>
    </form>}
    {!sorted.length && !state.draft && <p className="wb-activity-empty wb-side-empty">Select text in the PDF and choose Comment to highlight it or add a note.</p>}
    <ol className="wb-notes-list">{sorted.map((item) => <li key={item.commentId} data-stale={state.stale[item.commentId]}>
      <button type="button" className="wb-notes-jump" onClick={() => readerActions.focus(file.path, item)} title="Show in the PDF">
        <span>p. {item.pageNumber}{state.stale[item.commentId] === "missing" ? " · not found in this version" : ""}</span>
        <q>{item.quote}</q>
      </button>
      {editing === item.commentId
        ? <form onSubmit={(event) => { event.preventDefault(); readerActions.editComment(file.path, item.commentId, editText); setEditing(null); }}>
            <textarea autoFocus value={editText} aria-label="Edit note" onChange={(event) => setEditText(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setEditing(null); if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); readerActions.editComment(file.path, item.commentId, editText); setEditing(null); } }} />
          </form>
        : item.text && <p>{item.text}</p>}
      <div className="wb-notes-actions">
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
