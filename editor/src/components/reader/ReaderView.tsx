import { transcribeSelection } from "./lib/transcribe-selection";
import { currentOcrPages } from "./lib/ocr-layout";
import type { ComposerAttachmentDraft } from "../../lib/composerAttachments";
import { selectionImage } from "./lib/selection-image";
import { prepareAttachments } from "../../lib/attachFiles";
import { useCallback, useEffect, useRef, useState } from "react";
import { Download, MessageSquareQuote, ScanText } from "lucide-react";
import type { PDFDocumentProxy } from "pdfjs-dist/types/src/pdf";
import { InteractivePdfViewer } from "./InteractivePdfViewer";
import type { PaperComment, PaperCommentAnchor, PdfSelectionAnchor } from "./lib/paper-comments";
import { readerActions, useReaderState } from "./readerStore";
import { fingerprintText, locateVisualSelection, resolveTextAnchor } from "./lib/paper-anchors";
import type { MaterialPdfOcrPage } from "./lib/pdf-ocr";
import { extractPageTexts, ocrPage, ocrPageText, sparsePages } from "./lib/ocr-runner";
import { rectsForQuote } from "./lib/text-layer";

export type ReaderFile = { name: string; path: string; url: string };
export type ReaderAsk = { attachment?: ComposerAttachmentDraft; quote: string; pageNumber: number; pageText: string; file: ReaderFile; anchor?: PaperCommentAnchor | null };

const PAGE_CONTEXT_LIMIT = 6000;
type OcrState = { status: "idle" | "checking" | "running" | "done" | "error"; done: number; total: number; message?: string };

/**
 * Distraction-free PDF reading: the viewer keeps its own scroll, zoom and position;
 * questions go to the side chat, comments stay as anchored highlights on the page.
 * Scanned pages get an OCR text layer; comments export as standard PDF highlights.
 */
export function ReaderView({ file, onAsk, onNotes }: { file: ReaderFile; onAsk: (ask: ReaderAsk) => void; onNotes: () => void }) {
  const root = useRef<HTMLDivElement>(null);
  const { comments, draft, stale, focus: commentFocus } = useReaderState(file.path);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageTexts, setPageTexts] = useState<Map<number, string>>(new Map());
  const [ocrPages, setOcrPages] = useState<MaterialPdfOcrPage[]>([]);
  const [ocr, setOcr] = useState<OcrState>({ status: "idle", done: 0, total: 0 });
  const [exporting, setExporting] = useState(false);
  const documentRef = useRef<PDFDocumentProxy | null>(null);
  const [pdfDocument, setPdfDocument] = useState<PDFDocumentProxy | null>(null);
  const visiblePage = useRef(pageNumber); visiblePage.current = pageNumber;
  const onDocument = useCallback((document: PDFDocumentProxy | null) => { documentRef.current = document; setPdfDocument(document); }, []);

  useEffect(() => {
    setPageNumber(1); setPageTexts(new Map()); setOcrPages([]); setOcr({ status: "idle", done: 0, total: 0 });
  }, [file.path]);
  const save = (next: PaperComment[]) => readerActions.setComments(file.path, next);
  useEffect(() => { if (commentFocus) setPageNumber(commentFocus.pageNumber); }, [commentFocus]);

  /** Page text for anchoring and Ask context: pdf.js text, or the OCR layer for scanned pages. */
  const pageText = useCallback((page: number): string => {
    const scanned = ocrPages.find((entry) => entry.page_number === page);
    const text = scanned ? ocrPageText(scanned) : pageTexts.get(page) ?? "";
    return text.slice(0, PAGE_CONTEXT_LIMIT);
  }, [ocrPages, pageTexts]);

  // Extract text once the document is open; OCR sparse pages (saved per file on the server).
  useEffect(() => {
    const document = pdfDocument;
    if (!document) return;
    let cancelled = false;
    const isCurrent = () => !cancelled && documentRef.current === document;
    setOcrPages([]);
    void (async () => {
      setOcr({ status: "checking", done: 0, total: 0 });
      const texts = await extractPageTexts(document);
      if (!isCurrent()) return;
      setPageTexts(texts);
      const needed = sparsePages(texts);
      if (!needed.length) { setOcr({ status: "idle", done: 0, total: 0 }); return; }
      let saved: MaterialPdfOcrPage[] = [];
      try {
        const response = file.url.startsWith("blob:") ? null : await fetch(`/api/reader/ocr?path=${encodeURIComponent(file.path)}`);
        if (response?.ok) saved = currentOcrPages((await response.json()).pages, "auto");
      } catch { /* OCR cache is optional */ }
      const have = new Set(saved.map((page) => page.page_number));
      const pending = needed.filter((page) => !have.has(page)).sort((a, b) => Math.abs(a - visiblePage.current) - Math.abs(b - visiblePage.current));
      if (!isCurrent()) return;
      setOcrPages(saved);
      if (!pending.length) { setOcr({ status: "done", done: needed.length, total: needed.length }); return; }
      setOcr({ status: "running", done: needed.length - pending.length, total: needed.length });
      const results = [...saved];
      for (const number of pending) {
        if (!isCurrent()) return;
        try {
          const page = await ocrPage(document, number);
          if (!isCurrent()) return;
          results.push(page);
          setOcrPages([...results]);
          setOcr((current) => ({ ...current, done: current.done + 1 }));
        } catch (error) {
          if (!isCurrent()) return;
          setOcr({ status: "error", done: 0, total: needed.length, message: error instanceof Error ? error.message : "OCR failed" });
          return;
        }
      }
      setOcr({ status: "done", done: needed.length, total: needed.length });
      try {
        if (!file.url.startsWith("blob:")) await fetch(`/api/reader/ocr?path=${encodeURIComponent(file.path)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pages: results }) });
      } catch { /* next open will OCR again */ }
    })().catch((error: unknown) => {
      if (isCurrent()) setOcr({ status: "error", done: 0, total: 0, message: error instanceof Error ? error.message : "Text extraction failed" });
    });
    return () => { cancelled = true; };
  }, [file.path, file.url, pdfDocument]);

  const currentComments = useRef(comments); currentComments.current = comments;
  const currentDraft = useRef(draft); currentDraft.current = draft;
  const [repairStatus, setRepairStatus] = useState('');
  const [repairEpoch, setRepairEpoch] = useState(0);
  useEffect(() => {
    if (!pdfDocument || !pageTexts.size || !navigator.onLine) return;
    let cancelled = false;
    const pending = currentComments.current.filter(note => !note.quoteSource && note.rects.length && pageTexts.has(note.pageNumber) && !pageTexts.get(note.pageNumber)?.trim());
    if (!pending.length) return;
    void (async () => {
      let failed = 0;
      for (let index = 0; index < pending.length; index++) {
        if (cancelled) return;
        setRepairStatus(`Updating scanned quotes ${index + 1}/${pending.length}…`);
        const note = pending[index];
        try {
          const resolved = await transcribeSelection(pdfDocument, note);
          if (cancelled) return;
          readerActions.repairQuote(file.path, note.commentId, note.quote, resolved);
        } catch { failed++; readerActions.finishQuote(file.path, note.commentId, null); }
      }
      if (!cancelled) setRepairStatus(failed ? `Could not update ${failed} scanned quote${failed === 1 ? '' : 's'}.` : '');
    })();
    return () => { cancelled = true; };
  }, [file.path, pdfDocument, pageTexts, repairEpoch]);

  useEffect(() => {
    const retry = (event: Event) => {
      if (event.type !== 'online' && (event as CustomEvent).detail !== file.path) return;
      setRepairEpoch(value => value + 1);
      const pending = currentDraft.current, document = documentRef.current;
      if (pending?.quoteStatus && pending.draftId && document) {
        void transcribeSelection(document, pending).then(result => readerActions.finishQuote(file.path, pending.draftId!, result), () => readerActions.finishQuote(file.path, pending.draftId!, null));
      }
    };
    window.addEventListener('online', retry); window.addEventListener('diane:retry-quote', retry);
    return () => { window.removeEventListener('online', retry); window.removeEventListener('diane:retry-quote', retry); };
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
        if (resolved.status === "missing") { readerActions.setStale(file.path, comment.commentId, "missing"); return comment; }
        const rects = rectsForQuote(pageElement, resolved.selection.quote);
        if (!rects.length) return comment;
        changed = true;
        readerActions.setStale(file.path, comment.commentId, null);
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

  const resolveSelection = async (selection: PdfSelectionAnchor): Promise<PdfSelectionAnchor> => {
    const document = documentRef.current;
    if (!document) throw Error('The PDF is still loading.');
    const page = await document.getPage(selection.pageNumber);
    const content = await page.getTextContent();
    const nativeText = content.items.map(item => 'str' in item ? item.str : '').join('');
    if (nativeText.trim().length > 0 && selection.kind !== 'area') return selection;
    const resolved = await transcribeSelection(document, selection);
    if (documentRef.current !== document) throw Error('The document changed. Select the passage again.');
    return resolved;
  };

  const anchorFor = (selection: PdfSelectionAnchor): PaperCommentAnchor | null => {
    const text = pageText(selection.pageNumber);
    if (selection.quoteSource === "vision" || selection.kind === "area" || !text) return null;
    const located = locateVisualSelection(text, selection.quote);
    return located.status === "exact" ? { ...located.selection, pageFingerprint: fingerprintText(text) } : null;
  };
  const ask = async (selection: PdfSelectionAnchor): Promise<string | null> => {
    const document = documentRef.current;
    let context = pageText(selection.pageNumber);
    let attachment: ComposerAttachmentDraft | undefined;
    const pageElement = root.current?.querySelector<HTMLElement>(`[data-pdf-page="${selection.pageNumber}"]`);
    if (selection.kind === "area" || pageElement?.dataset.textLayer === "ocr") {
      if (!document) return "The PDF is still loading.";
      const blob = await selectionImage(document, selection);
      if (documentRef.current !== document) return "The document changed. Select an area again.";
      const result = await prepareAttachments([new File([blob], `${file.name.replace(/\.pdf$/i, "")}-p${selection.pageNumber}-selection.png`, { type: "image/png" })], "PDF selection");
      if (result.errors.length || !result.attachments[0]) return result.errors.join("; ") || "Could not save the selected image.";
      if (documentRef.current !== document) return "The document changed. Select an area again.";
      attachment = result.attachments[0];
      context = `Selected image: ${result.attachments[0].path}\nOpen this image to inspect the selected area. Source: ${file.path}, PDF page ${selection.pageNumber}.`;
    }
    onAsk({ attachment, quote: selection.quote, pageNumber: selection.pageNumber, pageText: context, file, anchor: anchorFor(selection) });
    return null;
  };
  // Comment hands the selection to the side panel's Notes tab, where the note is written.
  const comment = (selection: PdfSelectionAnchor) => {
    const scanned = selection.kind === 'area' || root.current?.querySelector<HTMLElement>(`[data-pdf-page="${selection.pageNumber}"]`)?.dataset.textLayer === 'ocr';
    const draftId = crypto.randomUUID();
    readerActions.setDraft(file.path, { ...selection, draftId, anchor: scanned ? null : anchorFor(selection),
      ...(scanned ? { quote: 'Selected passage', originalQuote: selection.quote, quoteStatus: navigator.onLine ? 'pending' as const : 'deferred' as const } : {}) });
    onNotes();
    const document = documentRef.current;
    if (scanned && document && navigator.onLine) void transcribeSelection(document, selection).then(
      result => readerActions.finishQuote(file.path, draftId, result),
      () => readerActions.finishQuote(file.path, draftId, null),
    );
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
    {repairStatus && <div role="status" className="wb-reader-ocr">{repairStatus}{repairStatus.startsWith('Could not') && <button onClick={() => setRepairEpoch(value => value + 1)}>Retry</button>}</div>}
    <div className="wb-reader-stage">
      <InteractivePdfViewer
        commentFocus={commentFocus}
        comments={visibleComments}
        currentPageLabel={`Page ${pageNumber}`}
        materialId={file.path}
        positionIdentity={file.path}
        onResolveSelection={resolveSelection}
        onAskSelection={ask}
        onCommentSelection={comment}
        onDocument={onDocument}
        onPageChange={onPageChange}
        ocrPages={ocrPages}
        pageNumber={pageNumber}
        sourceUrl={file.url}
        title={file.name}
        toolbarExtras={<>
          {ocr.status === "running" && <span title="Recognizing text on scanned pages"><ScanText size={12} />OCR {ocr.done}/{ocr.total}</span>}
          {ocr.status === "error" && <span data-error title={ocr.message}><ScanText size={12} />OCR failed</span>}
          <button type="button" onClick={onNotes}><MessageSquareQuote size={13} />Notes{comments.length ? ` ${comments.length}` : ""}</button>
          <button type="button" onClick={() => void exportPdf()} disabled={!comments.length || exporting} title="Download a copy with notes as PDF highlights"><Download size={13} />{exporting ? "Exporting…" : "Export"}</button>
        </>}
      />
    </div>
  </div>;
}
