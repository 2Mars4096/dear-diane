import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, MessageSquareQuote, Trash2, X } from "lucide-react";
import { InteractivePdfViewer, type PdfCommentFocus } from "./InteractivePdfViewer";
import { readPaperComments, writePaperComments, type PaperComment, type PdfSelectionAnchor } from "./lib/paper-comments";

export type ReaderFile = { name: string; path: string; url: string };
export type ReaderAsk = { quote: string; pageNumber: number; pageText: string; file: ReaderFile };

const PAGE_CONTEXT_LIMIT = 6000;

/**
 * Distraction-free PDF reading: the viewer keeps its own scroll, zoom and position;
 * questions go to the side chat, comments stay as highlights on the page.
 */
export function ReaderView({ file, onAsk, onClose }: { file: ReaderFile; onAsk: (ask: ReaderAsk) => void; onClose: () => void }) {
  const root = useRef<HTMLDivElement>(null);
  const note = useRef<HTMLTextAreaElement>(null);
  const material = useMemo(() => ({ material_id: file.path }), [file.path]);
  const [pageNumber, setPageNumber] = useState(1);
  const [comments, setComments] = useState<PaperComment[]>(() => readPaperComments(material));
  const [commentFocus, setCommentFocus] = useState<PdfCommentFocus | null>(null);
  const [anchor, setAnchor] = useState<PdfSelectionAnchor | null>(null);
  const [draft, setDraft] = useState("");
  const [listOpen, setListOpen] = useState(false);

  useEffect(() => { setComments(readPaperComments(material)); setPageNumber(1); setAnchor(null); setListOpen(false); }, [material]);
  const save = (next: PaperComment[]) => {
    setComments(next);
    try { writePaperComments(material, next); } catch { /* highlights still show for this visit */ }
  };

  const pageText = useCallback((page: number) => {
    const element = root.current?.querySelector<HTMLElement>(`[data-pdf-page="${page}"]`);
    return (element?.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, PAGE_CONTEXT_LIMIT);
  }, []);

  const ask = (selection: PdfSelectionAnchor): string | null => {
    onAsk({ quote: selection.quote, pageNumber: selection.pageNumber, pageText: pageText(selection.pageNumber), file });
    return null;
  };
  const comment = (selection: PdfSelectionAnchor) => {
    setAnchor(selection);
    setDraft("");
    window.requestAnimationFrame(() => note.current?.focus());
  };
  const saveComment = () => {
    if (!anchor) return;
    const now = new Date().toISOString();
    save([...comments, { ...anchor, commentId: crypto.randomUUID(), createdAt: now, materialId: file.path, text: draft.trim(), updatedAt: now }].slice(-200));
    setAnchor(null);
    setDraft("");
  };
  const jump = (item: PaperComment) => {
    setPageNumber(item.pageNumber);
    setCommentFocus({ commentId: item.commentId, pageNumber: item.pageNumber, requestId: Date.now(), top: item.rects[0]?.top ?? 0 });
    setListOpen(false);
  };
  const onPageChange = useCallback((page: number) => setPageNumber(page), []);

  return <div className="wb-reader" ref={root}>
    <header className="wb-reader-bar">
      <BookOpen size={14} aria-hidden="true" />
      <strong title={file.path}>{file.name}</strong>
      <div className="wb-reader-actions">
        <button type="button" aria-expanded={listOpen} onClick={() => setListOpen(!listOpen)} disabled={!comments.length}>
          <MessageSquareQuote size={13} />Comments{comments.length ? ` ${comments.length}` : ""}
        </button>
        <button type="button" onClick={onClose} aria-label="Close reader"><X size={15} /></button>
      </div>
      {listOpen && <ol className="wb-reader-comments" aria-label="Comments">
        {[...comments].sort((a, b) => a.pageNumber - b.pageNumber || (a.rects[0]?.top ?? 0) - (b.rects[0]?.top ?? 0)).map((item) => <li key={item.commentId}>
          <button type="button" onClick={() => jump(item)}>
            <span>p. {item.pageNumber}</span>
            <q>{item.quote}</q>
            {item.text && <em>{item.text}</em>}
          </button>
          <button type="button" aria-label="Delete comment" onClick={() => save(comments.filter((entry) => entry.commentId !== item.commentId))}><Trash2 size={12} /></button>
        </li>)}
      </ol>}
    </header>
    <div className="wb-reader-stage">
      <InteractivePdfViewer
        commentFocus={commentFocus}
        comments={comments}
        currentPageLabel={`Page ${pageNumber}`}
        materialId={file.path}
        positionIdentity={file.path}
        onAskSelection={ask}
        onCommentSelection={comment}
        onPageChange={onPageChange}
        ocrPages={[]}
        pageNumber={pageNumber}
        sourceUrl={file.url}
        title={file.name}
      />
    </div>
    {anchor && <form className="wb-reader-note" onSubmit={(event) => { event.preventDefault(); saveComment(); }}>
      <q>{anchor.quote}</q>
      <textarea ref={note} value={draft} placeholder="Add a note (optional)" aria-label="Comment"
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setAnchor(null);
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); saveComment(); }
        }} />
      <footer><button type="button" onClick={() => setAnchor(null)}>Cancel</button><button type="submit">{draft.trim() ? "Save comment" : "Highlight"}</button></footer>
    </form>}
  </div>;
}
