import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, Download, MessageSquareQuote, ScanText, Trash2, X } from "lucide-react";
import type { PDFDocumentProxy } from "pdfjs-dist/types/src/pdf";
import { InteractivePdfViewer, type PdfCommentFocus } from "./InteractivePdfViewer";
import { readPaperComments, writePaperComments, type PaperComment, type PaperCommentAnchor, type PdfSelectionAnchor } from "./lib/paper-comments";
import { fingerprintText, locateVisualSelection, resolveTextAnchor } from "./lib/paper-anchors";
import type { MaterialPdfOcrPage } from "./lib/pdf-ocr";
import { extractPageTexts, ocrPage, ocrPageText, sparsePages } from "./lib/ocr-runner";
import { rectsForQuote } from "./lib/text-layer";

export type ReaderFile = { name: string; path: string; url: string };
export type ReaderAsk = { quote: string; pageNumber: number; pageText: string; file: ReaderFile; anchor?: PaperCommentAnchor | null };

const PAGE_CONTEXT_LIMIT = 6000;
type OcrState = { status: "idle" | "checking" | "running" | "done" | "error"; done: number; total: number; message?: string };

/**
 * Distraction-free PDF reading: the viewer keeps its own scroll, zoom and position;
 * questions go to the side chat, comments stay as anchored highlights on the page.
 * Scanned pages get an OCR text layer; comments export as standard PDF highlights.
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
  const [pageTexts, setPageTexts] = useState<Map<number, string>>(new Map());
  const [ocrPages, setOcrPages] = useState<MaterialPdfOcrPage[]>([]);
  const [ocr, setOcr] = useState<OcrState>({ status: "idle", done: 0, total: 0 });
  const [stale, setStale] = useState<Record<string, "moved" | "missing">>({});
  const [exporting, setExporting] = useState(false);
  const documentRef = useRef<PDFDocumentProxy | null>(null);

  useEffect(() => {
    setComments(readPaperComments(material)); setPageNumber(1); setAnchor(null); setListOpen(false);
    setPageTexts(new Map()); setOcrPages([]); setOcr({ status: "idle", done: 0, total: 0 }); setStale({});
  }, [material]);
  const save = (next: PaperComment[]) => {
    setComments(next);
    try { writePaperComments(material, next); } catch { /* highlights still show for this visit */ }
  };

  /** Page text for anchoring and Ask context: pdf.js text, or the OCR layer for scanned pages. */
  const pageText = useCallback((page: number): string => {
    const scanned = ocrPages.find((entry) => entry.page_number === page);
    const text = scanned ? ocrPageText(scanned) : pageTexts.get(page) ?? "";
    return text.slice(0, PAGE_CONTEXT_LIMIT);
  }, [ocrPages, pageTexts]);

  // Extract text once the document is open; OCR sparse pages (saved per file on the server).
  const onDocument = useCallback((document: PDFDocumentProxy | null) => {
    documentRef.current = document;
    if (!document) return;
    let cancelled = false;
    void (async () => {
      setOcr({ status: "checking", done: 0, total: 0 });
      const texts = await extractPageTexts(document);
      if (cancelled) return;
      setPageTexts(texts);
      const needed = sparsePages(texts);
      if (!needed.length) { setOcr({ status: "idle", done: 0, total: 0 }); return; }
      let saved: MaterialPdfOcrPage[] = [];
      try {
        const response = await fetch(`/api/reader/ocr?path=${encodeURIComponent(file.path)}`);
        if (response.ok) saved = ((await response.json()).pages ?? []) as MaterialPdfOcrPage[];
      } catch { /* OCR cache is optional */ }
      const have = new Set(saved.map((page) => page.page_number));
      const pending = needed.filter((page) => !have.has(page));
      if (cancelled) return;
      setOcrPages(saved);
      if (!pending.length) { setOcr({ status: "done", done: needed.length, total: needed.length }); return; }
      setOcr({ status: "running", done: needed.length - pending.length, total: needed.length });
      const results = [...saved];
      for (const number of pending) {
        if (cancelled || documentRef.current !== document) return;
        try {
          const page = await ocrPage(document, number);
          results.push(page);
          setOcrPages([...results]);
          setOcr((current) => ({ ...current, done: current.done + 1 }));
        } catch (error) {
          setOcr({ status: "error", done: 0, total: needed.length, message: error instanceof Error ? error.message : "OCR failed" });
          return;
        }
      }
      setOcr({ status: "done", done: needed.length, total: needed.length });
      try {
        await fetch(`/api/reader/ocr?path=${encodeURIComponent(file.path)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pages: results }) });
      } catch { /* next open will OCR again */ }
    })();
    return () => { cancelled = true; };
  }, [file.path]);

  // Re-find anchored highlights whose page text changed; redraw from the text layer.
  useEffect(() => {
    const container = root.current;
    if (!container || !comments.some((comment) => comment.anchor)) return;
    const relocate = (pageElement: HTMLElement) => {
      const number = Number(pageElement.dataset.pdfPage);
      const text = pageText(number);
      if (!text) return;
      const current = fingerprintText(text);
      let changed = false;
      const next = comments.map((comment) => {
        if (comment.pageNumber !== number || !comment.anchor || comment.anchor.pageFingerprint === current) return comment;
        const resolved = resolveTextAnchor(text, comment.anchor);
        if (resolved.status === "missing") { setStale((s) => s[comment.commentId] === "missing" ? s : { ...s, [comment.commentId]: "missing" }); return comment; }
        const rects = rectsForQuote(pageElement, resolved.selection.quote);
        if (!rects.length) return comment;
        changed = true;
        setStale((s) => { const copy = { ...s }; delete copy[comment.commentId]; return copy; });
        return { ...comment, rects, anchor: { ...resolved.selection, pageFingerprint: current } };
      });
      if (changed) save(next);
    };
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        const target = record.target as HTMLElement;
        if (record.attributeName === "data-rendered" && target.dataset.rendered === "true") relocate(target);
      }
    });
    observer.observe(container, { attributes: true, subtree: true, attributeFilter: ["data-rendered"] });
    container.querySelectorAll<HTMLElement>("[data-pdf-page][data-rendered='true']").forEach(relocate);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- save is a stable local helper
  }, [comments, pageText]);

  const anchorFor = (selection: PdfSelectionAnchor): PaperCommentAnchor | null => {
    const text = pageText(selection.pageNumber);
    if (!text) return null;
    const located = locateVisualSelection(text, selection.quote);
    return located.status === "exact" ? { ...located.selection, pageFingerprint: fingerprintText(text) } : null;
  };
  const ask = (selection: PdfSelectionAnchor): string | null => {
    onAsk({ quote: selection.quote, pageNumber: selection.pageNumber, pageText: pageText(selection.pageNumber), file, anchor: anchorFor(selection) });
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
    save([...comments, { ...anchor, anchor: anchorFor(anchor), commentId: crypto.randomUUID(), createdAt: now, materialId: file.path, text: draft.trim(), updatedAt: now }].slice(-200));
    setAnchor(null);
    setDraft("");
  };
  const jump = (item: PaperComment) => {
    setPageNumber(item.pageNumber);
    setCommentFocus({ commentId: item.commentId, pageNumber: item.pageNumber, requestId: Date.now(), top: item.rects[0]?.top ?? 0 });
    setListOpen(false);
  };
  const exportPdf = async () => {
    if (!comments.length || exporting) return;
    setExporting(true);
    try {
      const { createAnnotatedPdf } = await import("./lib/annotated-pdf");
      const blob = await createAnnotatedPdf(file.url, comments.filter((item) => stale[item.commentId] !== "missing"));
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${file.name.replace(/\.pdf$/i, "")}-comments.pdf`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (error) {
      setOcr((current) => ({ ...current, message: error instanceof Error ? error.message : "The annotated PDF could not be created." }));
    } finally { setExporting(false); }
  };
  const onPageChange = useCallback((page: number) => setPageNumber(page), []);
  const visibleComments = comments.filter((item) => stale[item.commentId] !== "missing");

  return <div className="wb-reader" ref={root}>
    <header className="wb-reader-bar">
      <BookOpen size={14} aria-hidden="true" />
      <strong title={file.path}>{file.name}</strong>
      {ocr.status === "running" && <span className="wb-reader-ocr" title="Recognizing text on scanned pages"><ScanText size={12} />OCR {ocr.done}/{ocr.total}</span>}
      {ocr.status === "error" && <span className="wb-reader-ocr" data-error title={ocr.message}><ScanText size={12} />OCR failed</span>}
      <div className="wb-reader-actions">
        <button type="button" aria-expanded={listOpen} onClick={() => setListOpen(!listOpen)} disabled={!comments.length}>
          <MessageSquareQuote size={13} />Comments{comments.length ? ` ${comments.length}` : ""}
        </button>
        <button type="button" onClick={() => void exportPdf()} disabled={!comments.length || exporting} title="Download a copy with comments as PDF highlights">
          <Download size={13} />{exporting ? "Exporting…" : "Export"}
        </button>
        <button type="button" onClick={onClose} aria-label="Close reader"><X size={15} /></button>
      </div>
      {listOpen && <ol className="wb-reader-comments" aria-label="Comments">
        {[...comments].sort((a, b) => a.pageNumber - b.pageNumber || (a.rects[0]?.top ?? 0) - (b.rects[0]?.top ?? 0)).map((item) => <li key={item.commentId} data-stale={stale[item.commentId]}>
          <button type="button" onClick={() => jump(item)}>
            <span>p. {item.pageNumber}{stale[item.commentId] === "missing" ? " · not found in this version" : stale[item.commentId] === "moved" ? " · moved" : ""}</span>
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
        comments={visibleComments}
        currentPageLabel={`Page ${pageNumber}`}
        materialId={file.path}
        positionIdentity={file.path}
        onAskSelection={ask}
        onCommentSelection={comment}
        onDocument={onDocument}
        onPageChange={onPageChange}
        ocrPages={ocrPages}
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
