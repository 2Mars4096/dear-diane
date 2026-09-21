// Ported from learning-assistant apps/web/app/materials/[materialId]/guide/interactive-pdf-viewer.tsx.
// Changes: local import paths and a bundled pdf.js worker.
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject
} from "react";
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  PDFPageProxy,
  RenderTask
} from "pdfjs-dist/types/src/pdf";
import type {
  PaperComment,
  PdfSelectionAnchor
} from "./lib/paper-comments";
import type {
  MaterialPdfOcrPage,
  PdfOcrTextSpan
} from "./lib/pdf-ocr";
import {
  isPaperHighlightEdgeShadeArtifact,
  paperHighlightDisplayPath
} from "./lib/paper-highlight-display";
import {
  DEFAULT_PAPER_REFERENCE_LINE_ID,
  MAX_PAPER_REFERENCE_LINES,
  MAX_PAPER_REFERENCE_TAGS,
  movePaperReferenceTag,
  readPaperReferenceTray,
  writePaperReferenceTray,
  type PaperReferenceLine,
  type PaperReferenceTag
} from "./lib/paper-reference-tags";
import { readPaperPdfPosition, writePaperPdfPosition, type PaperPdfPosition } from "./lib/paper-reading-position";
import styles from "./interactive-pdf-viewer.module.css";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

type SelectionAction = PdfSelectionAnchor;

export type PdfCommentFocus = {
  commentId: string;
  pageNumber: number;
  requestId: number;
  top: number;
};

type InteractivePdfViewerProps = {
  commentFocus: PdfCommentFocus | null;
  comments: PaperComment[];
  currentPageLabel: string;
  materialId: string;
  positionIdentity: string;
  onAskSelection: (selection: SelectionAction) => string | null;
  onCommentSelection: (selection: SelectionAction) => void;
  onDocument?: (document: PDFDocumentProxy | null) => void; // DAN: lets the reader extract text / run OCR
  onPageChange: (pageNumber: number) => void;
  ocrPages: MaterialPdfOcrPage[];
  pageNumber: number;
  sourceUrl: string;
  title: string;
};

type SelectionPrompt = SelectionAction & {
  left: number;
  top: number;
};

type ZoomAnchor = {
  pageNumber: number;
  pageRatio: number;
};

type PdfTextLayer = {
  cancel: () => void;
  render: () => Promise<void>;
};

type ReferencePeekProps = {
  documentProxy: PDFDocumentProxy;
  onCancelClose: () => void;
  onClose: () => void;
  onOpen: () => void;
  onRemove: () => void;
  tag: PaperReferenceTag;
};

// A new empty array would invalidate every non-OCR page render on each parent update.
const EMPTY_OCR_SPANS: PdfOcrTextSpan[] = [];

type PdfPageProps = {
  comments: PaperComment[];
  containerSize: { height: number; width: number };
  documentProxy: PDFDocumentProxy;
  onCaptureSelection: (pageNumber: number, textLayer: HTMLDivElement | null) => void;
  onRenderError: () => void;
  ocrSpans: PdfOcrTextSpan[];
  pageNumber: number;
  stageRef: RefObject<HTMLDivElement | null>;
  title: string;
  zoom: number;
};

const MAX_ZOOM = 4;
const MIN_ZOOM = 0.7;
const SCROLLBAR_ALLOWANCE = 16;
const ZOOM_STEP = 0.1;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function ReferencePeek({
  documentProxy,
  onCancelClose,
  onClose,
  onOpen,
  onRemove,
  tag
}: ReferencePeekProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let cancelled = false;
    setStatus("loading");
    void documentProxy.getPage(tag.pageNumber).then((page) => {
      if (cancelled) return;
      const naturalViewport = page.getViewport({ scale: 1 });
      const availableWidth = window.innerWidth <= 700
        ? 620
        : Math.min(720, Math.max(480, window.innerWidth - 80));
      const scale = availableWidth / naturalViewport.width;
      const viewport = page.getViewport({ scale });
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.35);
      const context = canvas.getContext("2d", { alpha: false });
      if (!context) throw new Error("Canvas unavailable");
      canvas.width = Math.floor(viewport.width * pixelRatio);
      canvas.height = Math.floor(viewport.height * pixelRatio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      const renderTask = page.render({
        canvas,
        canvasContext: context,
        transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
        viewport
      });
      renderTaskRef.current = renderTask;
      return renderTask.promise;
    }).then(() => {
      if (!cancelled) setStatus("ready");
    }).catch((error: unknown) => {
      if (!cancelled && !(error instanceof Error && error.name === "RenderingCancelledException")) {
        setStatus("error");
      }
    });
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
    };
  }, [documentProxy, tag.pageNumber]);

  return (
    <aside
      aria-label={`${tag.label}, page ${tag.pageNumber} preview`}
      className={styles.referencePeek}
      onBlur={onClose}
      onFocus={onCancelClose}
      onMouseEnter={onCancelClose}
      onMouseLeave={onClose}
    >
      <header>
        <div>
          <span>p. {tag.pageNumber}</span>
          <strong>{tag.label}</strong>
        </div>
        <div>
          <button onClick={onOpen} type="button">Open page</button>
          <button aria-label={`Remove ${tag.label} from references`} onClick={onRemove} type="button">Remove</button>
        </div>
      </header>
      <div className={styles.referencePage} data-ready={status === "ready" ? "true" : "false"}>
        <canvas aria-hidden="true" ref={canvasRef} />
        {status === "loading" ? <span>Loading page…</span> : null}
        {status === "error" ? <span>Preview unavailable. Open the page instead.</span> : null}
      </div>
    </aside>
  );
}

function PdfPage({
  comments,
  containerSize,
  documentProxy,
  onCaptureSelection,
  onRenderError,
  ocrSpans,
  pageNumber,
  stageRef,
  title,
  zoom
}: PdfPageProps) {
  const pageElementRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const [pageProxy, setPageProxy] = useState<PDFPageProxy | null>(null);
  const [nearViewport, setNearViewport] = useState(pageNumber <= 2);
  const [rendered, setRendered] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void documentProxy.getPage(pageNumber).then((page) => {
      if (!cancelled) {
        setPageProxy(page);
      }
    }).catch(() => {
      if (!cancelled) {
        onRenderError();
      }
    });
    return () => {
      cancelled = true;
    };
  }, [documentProxy, onRenderError, pageNumber]);

  useEffect(() => {
    const pageElement = pageElementRef.current;
    const stage = stageRef.current;
    if (!pageElement || !stage || typeof IntersectionObserver === "undefined") {
      setNearViewport(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(entry.isIntersecting),
      { root: stage, rootMargin: "125% 0px" }
    );
    observer.observe(pageElement);
    return () => observer.disconnect();
  }, [stageRef]);

  const viewport = useMemo(() => {
    const availableWidth = Math.max(240, containerSize.width - 48 - SCROLLBAR_ALLOWANCE);
    const availableHeight = Math.max(320, containerSize.height - 48 - SCROLLBAR_ALLOWANCE);
    if (!pageProxy) {
      const fallbackHeight = availableHeight;
      return {
        height: fallbackHeight * zoom,
        pdfViewport: null,
        width: Math.min(availableWidth, fallbackHeight * 0.773) * zoom
      };
    }
    const naturalViewport = pageProxy.getViewport({ scale: 1 });
    const fitWidth = availableWidth / naturalViewport.width;
    const fitHeight = availableHeight / naturalViewport.height;
    const fitScale = Math.max(0.2, Math.min(fitWidth, fitHeight));
    const pdfViewport = pageProxy.getViewport({ scale: fitScale * zoom });
    return {
      height: pdfViewport.height,
      pdfViewport,
      width: pdfViewport.width
    };
  }, [containerSize, pageProxy, zoom]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const textLayerElement = textLayerRef.current;
    if (!nearViewport || !pageProxy || !viewport.pdfViewport || !canvas || !textLayerElement) {
      setRendered(false);
      if (canvas && !nearViewport) {
        canvas.width = 1;
        canvas.height = 1;
      }
      if (textLayerElement && !nearViewport) {
        textLayerElement.replaceChildren();
      }
      return;
    }

    let cancelled = false;
    let textLayer: PdfTextLayer | null = null;
    const pdfViewport = viewport.pdfViewport;
    setRendered(false);

    void (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        const context = canvas.getContext("2d", { alpha: false });
        if (!context) {
          throw new Error("Canvas unavailable");
        }
        const pixelRatio = Math.min(
          window.devicePixelRatio || 1,
          Math.max(1, 2 / zoom)
        );
        canvas.width = Math.floor(pdfViewport.width * pixelRatio);
        canvas.height = Math.floor(pdfViewport.height * pixelRatio);
        canvas.style.width = `${pdfViewport.width}px`;
        canvas.style.height = `${pdfViewport.height}px`;
        textLayerElement.replaceChildren();
        textLayerElement.style.width = `${pdfViewport.width}px`;
        textLayerElement.style.height = `${pdfViewport.height}px`;
        textLayerElement.style.setProperty("--total-scale-factor", String(pdfViewport.scale));

        renderTaskRef.current?.cancel();
        const renderTask = pageProxy.render({
          canvas,
          canvasContext: context,
          transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
          viewport: pdfViewport
        });
        renderTaskRef.current = renderTask;
        const renderPromise = renderTask.promise.catch((error: unknown) => {
          if (error && typeof error === "object" && "name" in error &&
            error.name === "RenderingCancelledException") {
            return;
          }
          throw error;
        });
        if (ocrSpans.length > 0) {
          ocrSpans.forEach((ocrSpan) => {
            const span = document.createElement("span");
            const targetWidth = Math.max(1, ocrSpan.width * pdfViewport.width);
            const targetHeight = Math.max(1, ocrSpan.height * pdfViewport.height);
            span.className = styles.ocrLine;
            span.textContent = `${ocrSpan.text} `;
            span.style.left = `${ocrSpan.left * pdfViewport.width}px`;
            span.style.top = `${ocrSpan.top * pdfViewport.height}px`;
            span.style.fontSize = `${Math.max(4, targetHeight * 0.86)}px`;
            span.style.lineHeight = `${targetHeight}px`;
            textLayerElement.append(span);
            const naturalWidth = Math.max(1, span.getBoundingClientRect().width);
            span.style.transform = `scaleX(${targetWidth / naturalWidth})`;
          });
          await renderPromise;
        } else {
          const textContent = await pageProxy.getTextContent();
          const nextTextLayer = new pdfjs.TextLayer({
            container: textLayerElement,
            textContentSource: textContent,
            viewport: pdfViewport
          });
          textLayer = nextTextLayer;
          await Promise.all([renderPromise, nextTextLayer.render()]);
        }
        if (!cancelled) {
          setRendered(true);
        }
      } catch (error) {
        if (!cancelled && !(error instanceof Error && error.name === "RenderingCancelledException")) {
          onRenderError();
        }
      }
    })();

    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      textLayer?.cancel();
    };
  }, [nearViewport, ocrSpans, onRenderError, pageProxy, viewport, zoom]);

  return (
    <div
      aria-busy={!rendered}
      aria-label={`${title}, page ${pageNumber}`}
      className={styles.page}
      data-pdf-page={pageNumber}
      data-pdf-sized={viewport.pdfViewport ? "true" : "false"}
      data-pdf-rotation={pageProxy?.rotate ?? 0}
      data-rendered={rendered ? "true" : "false"}
      data-text-layer={ocrSpans.length > 0 ? "ocr" : "native"}
      ref={pageElementRef}
      role="document"
      style={{ height: viewport.height, width: viewport.width }}
    >
      <canvas aria-hidden="true" ref={canvasRef} />
      {comments.map((comment) => {
        const path = paperHighlightDisplayPath(comment.rects);
        return path ? (
          <svg
            aria-hidden="true"
            className={styles.commentHighlight}
            data-comment-id={comment.commentId}
            key={comment.commentId}
            preserveAspectRatio="none"
            viewBox="0 0 100 100"
          >
            <path d={path} />
          </svg>
        ) : null;
      })}
      <div
        className={styles.textLayer}
        onKeyUp={() => onCaptureSelection(pageNumber, textLayerRef.current)}
        onMouseUp={() => onCaptureSelection(pageNumber, textLayerRef.current)}
        onTouchEnd={() => window.setTimeout(
          () => onCaptureSelection(pageNumber, textLayerRef.current),
          80
        )}
        ref={textLayerRef}
      />
      {ocrSpans.length > 0 ? (
        <span className={styles.ocrStatus}>Image text ready</span>
      ) : null}
    </div>
  );
}

export function InteractivePdfViewer({
  commentFocus,
  comments,
  currentPageLabel,
  materialId,
  positionIdentity,
  onAskSelection,
  onCommentSelection,
  onDocument,
  onPageChange,
  ocrPages,
  pageNumber,
  sourceUrl,
  title
}: InteractivePdfViewerProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const documentElementRef = useRef<HTMLDivElement>(null);
  const loadingTaskRef = useRef<PDFDocumentLoadingTask | null>(null);
  const documentRef = useRef<PDFDocumentProxy | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const reportedPageRef = useRef<number | null>(null);
  const visiblePageRef = useRef(pageNumber);
  const zoomAnchorRef = useRef<ZoomAnchor | null>(null);
  const referenceOpenTimerRef = useRef<number | null>(null);
  const referenceCloseTimerRef = useRef<number | null>(null);
  const referenceTrayOpenTimerRef = useRef<number | null>(null);
  const referenceTrayCloseTimerRef = useRef<number | null>(null);
  const referenceHoldTimerRef = useRef<number | null>(null);
  const heldReferenceRef = useRef<string | null>(null);
  const suppressReferenceClickRef = useRef(false);
  const [containerSize, setContainerSize] = useState({ height: 0, width: 0 });
  const [pageCount, setPageCount] = useState(0);
  const [zoom, setZoom] = useState(1);
  const positionRestoredRef = useRef(false);
  const savedPositionRef = useRef<PaperPdfPosition | null>(null);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("Loading PDF…");
  const [selectionPrompt, setSelectionPrompt] = useState<SelectionPrompt | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [referenceLines, setReferenceLines] = useState<PaperReferenceLine[]>([{
    label: "Saved",
    lineId: DEFAULT_PAPER_REFERENCE_LINE_ID
  }]);
  const [referenceTags, setReferenceTags] = useState<PaperReferenceTag[]>([]);
  const [referencesHydrated, setReferencesHydrated] = useState(false);
  const [referencesForMaterialId, setReferencesForMaterialId] = useState("");
  const [referenceTrayOpen, setReferenceTrayOpen] = useState(false);
  const [referenceTrayPinned, setReferenceTrayPinned] = useState(false);
  const [peekedReferenceId, setPeekedReferenceId] = useState<string | null>(null);
  const [draggedReferenceId, setDraggedReferenceId] = useState<string | null>(null);
  const [referenceDropLineId, setReferenceDropLineId] = useState<string | null>(null);
  const [referenceDropTagId, setReferenceDropTagId] = useState<string | null>(null);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) {
      return;
    }
    const updateSize = () => {
      const bounds = stage.getBoundingClientRect();
      const nextSize = {
        height: Math.round(bounds.height),
        width: Math.round(bounds.width)
      };
      setContainerSize((current) =>
        current.height === nextSize.height && current.width === nextSize.width
          ? current
          : nextSize
      );
    };
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    savedPositionRef.current = readPaperPdfPosition(positionIdentity);
    positionRestoredRef.current = !savedPositionRef.current;
    setZoom(savedPositionRef.current?.zoom ?? 1);
  }, [positionIdentity]);

  const saveReadingPosition = useCallback(() => {
    const stage = stageRef.current;
    const page = documentElementRef.current?.querySelector<HTMLElement>(
      `[data-pdf-page="${visiblePageRef.current}"]`
    );
    if (!positionRestoredRef.current || !stage || !page || page.dataset.pdfSized !== "true" || !page.offsetHeight) return;
    writePaperPdfPosition(positionIdentity, {
      page: visiblePageRef.current,
      top: (stage.scrollTop - page.offsetTop) / page.offsetHeight,
      left: stage.scrollLeft / Math.max(1, stage.scrollWidth),
      zoom: zoomRef.current
    });
  }, [positionIdentity]);

  useEffect(() => {
    window.addEventListener("pagehide", saveReadingPosition);
    return () => {
      saveReadingPosition();
      window.removeEventListener("pagehide", saveReadingPosition);
    };
  }, [saveReadingPosition]);

  useEffect(() => {
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(saveReadingPosition);
    });
    return () => { cancelAnimationFrame(firstFrame); cancelAnimationFrame(secondFrame); };
  }, [zoom, saveReadingPosition]);

  useEffect(() => {
    if (status !== "ready" || !pageCount || positionRestoredRef.current) return;
    const saved = savedPositionRef.current;
    const stage = stageRef.current;
    const documentElement = documentElementRef.current;
    if (!saved || !stage || !documentElement) return;
    let frame = 0;
    const restore = () => {
      if (positionRestoredRef.current || !stage.clientWidth) return;
      const targetPage = Math.min(pageCount, saved.page);
      const pages = Array.from(documentElement.querySelectorAll<HTMLElement>("[data-pdf-page]"));
      // Wait for real dimensions, including all preceding pages. Placeholder heights can differ.
      if (pages.length < targetPage || pages.slice(0, targetPage).some((page) => page.dataset.pdfSized !== "true")) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const page = pages[targetPage - 1];
        stage.scrollTo({
          behavior: "instant",
          top: Math.max(0, page.offsetTop + saved.top * page.offsetHeight),
          left: saved.left * stage.scrollWidth
        });
        visiblePageRef.current = targetPage;
        reportedPageRef.current = targetPage;
        positionRestoredRef.current = true;
        onPageChange(targetPage);
      });
    };
    const observer = new MutationObserver(restore);
    observer.observe(documentElement, { attributes: true, childList: true, subtree: true, attributeFilter: ["data-pdf-sized", "style"] });
    const resize = new ResizeObserver(restore);
    resize.observe(stage);
    restore();
    return () => { observer.disconnect(); resize.disconnect(); cancelAnimationFrame(frame); };
  }, [status, pageCount, positionIdentity, onPageChange, zoom]);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setMessage("Loading PDF…");

    void (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const loadingTask = pdfjs.getDocument({ url: sourceUrl });
        loadingTaskRef.current = loadingTask;
        const pdfDocument = await loadingTask.promise;
        if (cancelled) {
          await loadingTask.destroy();
          return;
        }
        documentRef.current = pdfDocument;
        setPageCount(pdfDocument.numPages);
        setStatus("ready");
        setMessage("");
        onDocument?.(pdfDocument);
      } catch {
        if (!cancelled) {
          setStatus("error");
          setMessage("The PDF could not be rendered here. Open the text view or original file.");
        }
      }
    })();

    return () => {
      cancelled = true;
      if (scrollFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollFrameRef.current);
      }
      const loadingTask = loadingTaskRef.current;
      loadingTaskRef.current = null;
      documentRef.current = null;
      onDocument?.(null);
      void loadingTask?.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onDocument is a stable callback from the reader
  }, [sourceUrl]);

  useEffect(() => {
    if (pageCount <= 0) return;
    const tray = readPaperReferenceTray(materialId, pageCount);
    setReferenceLines(tray.lines);
    setReferenceTags(tray.tags);
    setReferencesForMaterialId(materialId);
    setReferencesHydrated(true);
  }, [materialId, pageCount]);

  useEffect(() => {
    if (!referencesHydrated || referencesForMaterialId !== materialId || pageCount <= 0) return;
    writePaperReferenceTray(materialId, {
      lines: referenceLines,
      tags: referenceTags,
      version: 2
    }, pageCount);
  }, [materialId, pageCount, referenceLines, referenceTags, referencesForMaterialId, referencesHydrated]);

  useEffect(() => () => {
    if (referenceOpenTimerRef.current !== null) {
      window.clearTimeout(referenceOpenTimerRef.current);
    }
    if (referenceCloseTimerRef.current !== null) {
      window.clearTimeout(referenceCloseTimerRef.current);
    }
    if (referenceHoldTimerRef.current !== null) {
      window.clearTimeout(referenceHoldTimerRef.current);
    }
    if (referenceTrayOpenTimerRef.current !== null) {
      window.clearTimeout(referenceTrayOpenTimerRef.current);
    }
    if (referenceTrayCloseTimerRef.current !== null) {
      window.clearTimeout(referenceTrayCloseTimerRef.current);
    }
  }, []);

  const scrollToPage = useCallback((targetPage: number, behavior: ScrollBehavior) => {
    const stage = stageRef.current;
    const documentElement = documentElementRef.current;
    const target = documentElement?.querySelector<HTMLElement>(`[data-pdf-page="${targetPage}"]`);
    if (!stage || !target) {
      return;
    }
    stage.scrollTo({
      behavior,
      left: Math.max(0, target.offsetLeft - 24),
      top: Math.max(0, target.offsetTop - 24)
    });
  }, []);

  useEffect(() => {
    if (status !== "ready" || pageCount === 0 || !positionRestoredRef.current) {
      return;
    }
    const nextPage = clamp(pageNumber, 1, pageCount);
    if (reportedPageRef.current === nextPage) {
      reportedPageRef.current = null;
      visiblePageRef.current = nextPage;
      return;
    }
    visiblePageRef.current = nextPage;
    window.requestAnimationFrame(() => scrollToPage(nextPage, "auto"));
  }, [pageCount, pageNumber, scrollToPage, status]);

  useEffect(() => {
    const stage = stageRef.current;
    const documentElement = documentElementRef.current;
    if (!commentFocus || !stage || !documentElement) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      const page = documentElement.querySelector<HTMLElement>(
        `[data-pdf-page="${commentFocus.pageNumber}"]`
      );
      if (!page) return;
      stage.scrollTo({
        behavior: "auto",
        left: stage.scrollLeft,
        top: Math.max(
          0,
          page.offsetTop + page.offsetHeight * commentFocus.top - stage.clientHeight / 2
        )
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [commentFocus]);

  useEffect(() => {
    const anchor = zoomAnchorRef.current;
    const stage = stageRef.current;
    const documentElement = documentElementRef.current;
    if (!anchor || !stage || !documentElement) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      const target = documentElement.querySelector<HTMLElement>(
        `[data-pdf-page="${anchor.pageNumber}"]`
      );
      if (target) {
        stage.scrollTo({
          behavior: "auto",
          left: stage.scrollLeft,
          top: Math.max(
            0,
            target.offsetTop + target.offsetHeight * anchor.pageRatio - stage.clientHeight / 2
          )
        });
      }
      zoomAnchorRef.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [zoom]);

  const captureSelection = useCallback((selectedPage: number, textLayer: HTMLDivElement | null) => {
    const selection = window.getSelection();
    const root = rootRef.current;
    if (!selection || selection.isCollapsed || selection.rangeCount !== 1 || !textLayer || !root) {
      setSelectionPrompt(null);
      return;
    }
    const range = selection.getRangeAt(0);
    if (!textLayer.contains(range.commonAncestorContainer)) {
      setSelectionPrompt(null);
      return;
    }
    const quote = selection.toString().replace(/\s+/g, " ").trim();
    if (quote.length < 2 || quote.length > 700) {
      setSelectionPrompt(null);
      return;
    }
    const rangeRect = range.getBoundingClientRect();
    const rootRect = root.getBoundingClientRect();
    const pageElement = textLayer.closest<HTMLElement>("[data-pdf-page]");
    const pageRect = pageElement?.getBoundingClientRect();
    if (!pageElement || !pageRect || pageRect.width <= 0 || pageRect.height <= 0) {
      setSelectionPrompt(null);
      return;
    }
    const rects = Array.from(range.getClientRects()).flatMap((rect) => {
      const left = clamp((rect.left - pageRect.left) / pageRect.width, 0, 1);
      const top = clamp((rect.top - pageRect.top) / pageRect.height, 0, 1);
      const right = clamp((rect.right - pageRect.left) / pageRect.width, 0, 1);
      const bottom = clamp((rect.bottom - pageRect.top) / pageRect.height, 0, 1);
      if (right <= left || bottom <= top) return [];
      const rectAnchor = { height: bottom - top, left, top, width: right - left };
      return isPaperHighlightEdgeShadeArtifact(rectAnchor) ? [] : [rectAnchor];
    }).slice(0, 40);
    if (!rects.length) {
      setSelectionPrompt(null);
      return;
    }
    const rawRotation = Number(pageElement.dataset.pdfRotation);
    const rotation = [0, 90, 180, 270].includes(rawRotation)
      ? rawRotation as PdfSelectionAnchor["rotation"]
      : 0;
    setSelectionError(null);
    setSelectionPrompt({
      left: clamp(rangeRect.left - rootRect.left + rangeRect.width / 2, 54, rootRect.width - 54),
      pageNumber: selectedPage,
      quote,
      rects,
      rotation,
      top: clamp(rangeRect.bottom - rootRect.top + 10, 58, rootRect.height - 58)
    });
  }, []);

  const askAboutSelection = () => {
    if (!selectionPrompt) {
      return;
    }
    const error = onAskSelection(selectionPrompt);
    if (error) {
      setSelectionError(error);
      return;
    }
    setSelectionPrompt(null);
    setSelectionError(null);
    window.getSelection()?.removeAllRanges();
  };

  const commentOnSelection = () => {
    if (!selectionPrompt) return;
    onCommentSelection(selectionPrompt);
    setSelectionPrompt(null);
    setSelectionError(null);
    window.getSelection()?.removeAllRanges();
  };

  const reportVisiblePage = useCallback(() => {
    const stage = stageRef.current;
    const documentElement = documentElementRef.current;
    if (!stage || !documentElement) {
      return;
    }
    if (!positionRestoredRef.current) return;
    const viewportTop = stage.scrollTop;
    const viewportBottom = viewportTop + stage.clientHeight;
    let bestPage = visiblePageRef.current;
    let bestOverlap = -1;
    let bestDistance = Number.POSITIVE_INFINITY;
    documentElement.querySelectorAll<HTMLElement>("[data-pdf-page]").forEach((element) => {
      const elementTop = element.offsetTop;
      const elementBottom = elementTop + element.offsetHeight;
      const overlap = Math.max(0, Math.min(viewportBottom, elementBottom) - Math.max(viewportTop, elementTop));
      const distance = Math.abs((elementTop + elementBottom) / 2 - (viewportTop + viewportBottom) / 2);
      if (overlap > bestOverlap || (overlap === bestOverlap && distance < bestDistance)) {
        bestOverlap = overlap;
        bestDistance = distance;
        bestPage = Number(element.dataset.pdfPage) || bestPage;
      }
    });
    if (bestPage !== visiblePageRef.current) {
      visiblePageRef.current = bestPage;
      reportedPageRef.current = bestPage;
      onPageChange(bestPage);
    }
  }, [onPageChange]);

  const handleScroll = () => {
    setSelectionPrompt(null);
    setSelectionError(null);
    if (scrollFrameRef.current !== null) {
      return;
    }
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      reportVisiblePage();
      saveReadingPosition();
    });
  };

  const navigatePage = (nextPage: number) => {
    if (nextPage < 1 || nextPage > pageCount || nextPage === pageNumber) {
      return;
    }
    reportedPageRef.current = null;
    onPageChange(nextPage);
  };

  const changeZoom = (nextZoom: number) => {
    const stage = stageRef.current;
    const documentElement = documentElementRef.current;
    const anchorPage = visiblePageRef.current;
    const anchorElement = documentElement?.querySelector<HTMLElement>(
      `[data-pdf-page="${anchorPage}"]`
    );
    if (stage && anchorElement) {
      const viewportCenter = stage.scrollTop + stage.clientHeight / 2;
      zoomAnchorRef.current = {
        pageNumber: anchorPage,
        pageRatio: clamp(
          (viewportCenter - anchorElement.offsetTop) / Math.max(1, anchorElement.offsetHeight),
          0,
          1
        )
      };
    }
    setZoom(nextZoom);
  };

  const clearReferenceOpenTimer = () => {
    if (referenceOpenTimerRef.current !== null) {
      window.clearTimeout(referenceOpenTimerRef.current);
      referenceOpenTimerRef.current = null;
    }
  };

  const clearReferenceTrayOpenTimer = () => {
    if (referenceTrayOpenTimerRef.current !== null) {
      window.clearTimeout(referenceTrayOpenTimerRef.current);
      referenceTrayOpenTimerRef.current = null;
    }
  };

  const cancelReferenceTrayClose = () => {
    if (referenceTrayCloseTimerRef.current !== null) {
      window.clearTimeout(referenceTrayCloseTimerRef.current);
      referenceTrayCloseTimerRef.current = null;
    }
  };

  const showReferenceTrayFromHover = () => {
    cancelReferenceTrayClose();
    if (referenceTrayOpen || referenceTrayPinned) return;
    clearReferenceTrayOpenTimer();
    referenceTrayOpenTimerRef.current = window.setTimeout(() => {
      setReferenceTrayOpen(true);
      referenceTrayOpenTimerRef.current = null;
    }, 140);
  };

  const showReferenceTrayFromFocus = () => {
    clearReferenceTrayOpenTimer();
    cancelReferenceTrayClose();
    setReferenceTrayOpen(true);
  };

  const scheduleReferenceTrayClose = () => {
    clearReferenceTrayOpenTimer();
    if (referenceTrayPinned) return;
    cancelReferenceTrayClose();
    referenceTrayCloseTimerRef.current = window.setTimeout(() => {
      setReferenceTrayOpen(false);
      setPeekedReferenceId(null);
      referenceTrayCloseTimerRef.current = null;
    }, 240);
  };

  const toggleReferenceTrayPin = () => {
    clearReferenceTrayOpenTimer();
    cancelReferenceTrayClose();
    if (referenceTrayPinned) {
      setReferenceTrayPinned(false);
      setReferenceTrayOpen(false);
      setPeekedReferenceId(null);
      return;
    }
    setReferenceTrayPinned(true);
    setReferenceTrayOpen(true);
  };

  const cancelReferenceClose = () => {
    if (referenceCloseTimerRef.current !== null) {
      window.clearTimeout(referenceCloseTimerRef.current);
      referenceCloseTimerRef.current = null;
    }
  };

  const scheduleReferenceClose = () => {
    cancelReferenceClose();
    referenceCloseTimerRef.current = window.setTimeout(() => {
      setPeekedReferenceId(null);
      referenceCloseTimerRef.current = null;
    }, 140);
  };

  const showReferencePeek = (tagId: string, delayed: boolean) => {
    if (draggedReferenceId) return;
    cancelReferenceClose();
    clearReferenceOpenTimer();
    if (!delayed) {
      setPeekedReferenceId(tagId);
      return;
    }
    referenceOpenTimerRef.current = window.setTimeout(() => {
      setPeekedReferenceId(tagId);
      referenceOpenTimerRef.current = null;
    }, 160);
  };

  const beginReferenceHold = (tag: PaperReferenceTag, pointerType: string) => {
    if (pointerType === "mouse") return;
    if (referenceHoldTimerRef.current !== null) {
      window.clearTimeout(referenceHoldTimerRef.current);
    }
    referenceHoldTimerRef.current = window.setTimeout(() => {
      heldReferenceRef.current = tag.tagId;
      suppressReferenceClickRef.current = true;
      showReferencePeek(tag.tagId, false);
      referenceHoldTimerRef.current = null;
    }, 180);
  };

  const endReferenceHold = () => {
    if (referenceHoldTimerRef.current !== null) {
      window.clearTimeout(referenceHoldTimerRef.current);
      referenceHoldTimerRef.current = null;
    }
    if (heldReferenceRef.current) {
      heldReferenceRef.current = null;
      setPeekedReferenceId(null);
    }
  };

  const openReferencePage = (tag: PaperReferenceTag) => {
    navigatePage(tag.pageNumber);
    setReferenceTrayPinned(false);
    setReferenceTrayOpen(false);
    setPeekedReferenceId(null);
  };

  const keepCurrentPage = () => {
    const existing = referenceTags.find((tag) => tag.pageNumber === pageNumber);
    const tag: PaperReferenceTag = {
      createdAt: new Date().toISOString(),
      label: currentPageLabel.trim() || `Page ${pageNumber}`,
      lineId: existing?.lineId ?? referenceLines[0]?.lineId ?? DEFAULT_PAPER_REFERENCE_LINE_ID,
      materialId,
      pageNumber,
      tagId: `page-${pageNumber}`
    };
    setReferenceTags((current) => [
      ...current.filter((entry) => entry.pageNumber !== pageNumber),
      tag
    ].slice(-MAX_PAPER_REFERENCE_TAGS));
    setReferenceTrayPinned(true);
    setReferenceTrayOpen(true);
    setPeekedReferenceId(tag.tagId);
  };

  const removeReferenceTag = (tagId: string) => {
    setReferenceTags((current) => current.filter((entry) => entry.tagId !== tagId));
    setPeekedReferenceId((current) => current === tagId ? null : current);
  };

  const addReferenceLine = () => {
    if (referenceLines.length >= MAX_PAPER_REFERENCE_LINES) return;
    setReferenceTrayPinned(true);
    const lineId = `line-${Date.now().toString(36)}`;
    setReferenceLines((current) => [...current, {
      label: `Line ${current.length + 1}`,
      lineId
    }]);
    window.requestAnimationFrame(() => {
      document.getElementById(`reference-line-name-${lineId}`)?.focus();
    });
  };

  const renameReferenceLine = (lineId: string, label: string) => {
    setReferenceLines((current) => current.map((line) =>
      line.lineId === lineId ? { ...line, label: label.slice(0, 40) } : line
    ));
  };

  const removeReferenceLine = (lineId: string) => {
    if (referenceLines.length <= 1 || referenceTags.some((tag) => tag.lineId === lineId)) return;
    setReferenceLines((current) => current.filter((line) => line.lineId !== lineId));
  };

  const moveReference = (tagId: string, lineId: string, beforeTagId: string | null) => {
    setReferenceTags((current) => movePaperReferenceTag(current, tagId, lineId, beforeTagId));
    setDraggedReferenceId(null);
    setReferenceDropLineId(null);
    setReferenceDropTagId(null);
  };

  const beginReferenceDrag = (event: ReactDragEvent<HTMLButtonElement>, tagId: string) => {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", tagId);
    suppressReferenceClickRef.current = true;
    setReferenceTrayPinned(true);
    setDraggedReferenceId(tagId);
    setPeekedReferenceId(null);
  };

  const finishReferenceDrag = () => {
    setDraggedReferenceId(null);
    setReferenceDropLineId(null);
    setReferenceDropTagId(null);
    window.setTimeout(() => {
      suppressReferenceClickRef.current = false;
    }, 0);
  };

  const referenceDragId = (event: ReactDragEvent<HTMLElement>): string =>
    event.dataTransfer.getData("text/plain") || draggedReferenceId || "";

  const moveReferenceWithKeyboard = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    tag: PaperReferenceTag
  ) => {
    if (!event.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const lineIndex = referenceLines.findIndex((line) => line.lineId === tag.lineId);
    const lineTags = referenceTags.filter((entry) => entry.lineId === tag.lineId);
    const tagIndex = lineTags.findIndex((entry) => entry.tagId === tag.tagId);
    if (event.key === "ArrowLeft" && tagIndex > 0) {
      moveReference(tag.tagId, tag.lineId, lineTags[tagIndex - 1].tagId);
    } else if (event.key === "ArrowRight" && tagIndex >= 0 && tagIndex < lineTags.length - 1) {
      moveReference(tag.tagId, tag.lineId, lineTags[tagIndex + 2]?.tagId ?? null);
    } else if (event.key === "ArrowUp" && lineIndex > 0) {
      moveReference(tag.tagId, referenceLines[lineIndex - 1].lineId, null);
    } else if (event.key === "ArrowDown" && lineIndex >= 0 && lineIndex < referenceLines.length - 1) {
      moveReference(tag.tagId, referenceLines[lineIndex + 1].lineId, null);
    }
  };

  const peekedReference = referenceTags.find((tag) => tag.tagId === peekedReferenceId) ?? null;

  useEffect(() => {
    if (!referenceTrayOpen && !peekedReferenceId) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setPeekedReferenceId(null);
      setReferenceTrayPinned(false);
      setReferenceTrayOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [peekedReferenceId, referenceTrayOpen]);

  const canGoBack = pageNumber > 1;
  const canGoForward = pageCount > 0 && pageNumber < pageCount;
  const documentProxy = documentRef.current;
  const handlePageRenderError = useCallback(() => {
    setMessage("One page could not be rendered. Try the text view.");
  }, []);

  return (
    <div className={styles.viewer} ref={rootRef}>
      <div className={styles.toolbar} aria-label="PDF page controls">
        <button
          aria-label="Previous page"
          disabled={!canGoBack}
          onClick={() => navigatePage(pageNumber - 1)}
          type="button"
        >
          ←
        </button>
        <span>Page {pageNumber}{pageCount ? ` / ${pageCount}` : ""}</span>
        <button
          aria-label="Next page"
          disabled={!canGoForward}
          onClick={() => navigatePage(pageNumber + 1)}
          type="button"
        >
          →
        </button>
        <span className={styles.toolbarDivider} aria-hidden="true" />
        <button
          aria-label="Zoom out"
          disabled={zoom <= MIN_ZOOM}
          onClick={() => changeZoom(Math.max(
            MIN_ZOOM,
            Math.round((zoom - ZOOM_STEP) * 10) / 10
          ))}
          type="button"
        >
          −
        </button>
        <span>{zoom === 1 ? "Fit" : `${Math.round(zoom * 100)}%`}</span>
        <button
          aria-label="Zoom in"
          disabled={zoom >= MAX_ZOOM}
          onClick={() => changeZoom(Math.min(
            MAX_ZOOM,
            Math.round((zoom + ZOOM_STEP) * 10) / 10
          ))}
          type="button"
        >
          +
        </button>
        <button
          aria-expanded={referenceTrayOpen}
          aria-label={`References, ${referenceTags.length} saved`}
          aria-pressed={referenceTrayPinned}
          className={styles.referenceTrayToggle}
          disabled={!referencesHydrated}
          onBlur={scheduleReferenceTrayClose}
          onClick={toggleReferenceTrayPin}
          onFocus={showReferenceTrayFromFocus}
          onMouseEnter={showReferenceTrayFromHover}
          onMouseLeave={scheduleReferenceTrayClose}
          type="button"
        >
          Refs{referenceTags.length ? ` ${referenceTags.length}` : ""}
        </button>
      </div>

      {referenceTrayOpen ? (
        <div
          className={styles.referenceOverlay}
          onBlur={scheduleReferenceTrayClose}
          onFocus={cancelReferenceTrayClose}
          onMouseEnter={() => {
            cancelReferenceTrayClose();
            cancelReferenceClose();
          }}
          onMouseLeave={() => {
            scheduleReferenceClose();
            scheduleReferenceTrayClose();
          }}
        >
          <section aria-label="Saved page references" className={styles.referenceTray}>
            <header>
              <strong>References</strong>
              <div className={styles.referenceTrayActions}>
                <button
                  disabled={referenceLines.length >= MAX_PAPER_REFERENCE_LINES}
                  onClick={addReferenceLine}
                  type="button"
                >
                  + Line
                </button>
                <button className={styles.keepReferenceButton} onClick={keepCurrentPage} type="button">
                  + Keep p. {pageNumber}
                </button>
              </div>
            </header>
            <div className={styles.referenceLines}>
              {referenceLines.map((line) => {
                const lineTags = referenceTags.filter((tag) => tag.lineId === line.lineId);
                const lineDropActive = referenceDropLineId === line.lineId && !referenceDropTagId;
                return (
                  <section
                    className={`${styles.referenceLine} ${lineDropActive ? styles.referenceLineDrop : ""}`}
                    key={line.lineId}
                    onDragOver={(event) => {
                      event.preventDefault();
                      event.dataTransfer.dropEffect = "move";
                      setReferenceDropLineId(line.lineId);
                      setReferenceDropTagId(null);
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      const tagId = referenceDragId(event);
                      if (tagId) moveReference(tagId, line.lineId, null);
                    }}
                  >
                    <header>
                      <input
                        aria-label="Reference line name"
                        id={`reference-line-name-${line.lineId}`}
                        maxLength={40}
                        onChange={(event) => renameReferenceLine(line.lineId, event.target.value)}
                        onFocus={() => setReferenceTrayPinned(true)}
                        value={line.label}
                      />
                      {referenceLines.length > 1 && lineTags.length === 0 ? (
                        <button
                          aria-label={`Remove ${line.label || "empty"} line`}
                          onClick={() => removeReferenceLine(line.lineId)}
                          type="button"
                        >
                          Remove
                        </button>
                      ) : null}
                    </header>
                    <div className={styles.referenceTags}>
                      {lineTags.map((tag) => (
                        <button
                          aria-describedby="reference-tray-help"
                          aria-grabbed={draggedReferenceId === tag.tagId}
                          className={`${draggedReferenceId === tag.tagId ? styles.referenceTagDragging : ""} ${referenceDropTagId === tag.tagId ? styles.referenceTagDrop : ""}`}
                          draggable
                          key={tag.tagId}
                          onBlur={scheduleReferenceClose}
                          onClick={(event) => {
                            if (suppressReferenceClickRef.current) {
                              event.preventDefault();
                              suppressReferenceClickRef.current = false;
                              return;
                            }
                            openReferencePage(tag);
                          }}
                          onDragEnd={finishReferenceDrag}
                          onDragOver={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            event.dataTransfer.dropEffect = "move";
                            setReferenceDropLineId(line.lineId);
                            setReferenceDropTagId(tag.tagId);
                          }}
                          onDragStart={(event) => beginReferenceDrag(event, tag.tagId)}
                          onDrop={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            const tagId = referenceDragId(event);
                            if (tagId) moveReference(tagId, line.lineId, tag.tagId);
                          }}
                          onFocus={() => showReferencePeek(tag.tagId, false)}
                          onKeyDown={(event) => moveReferenceWithKeyboard(event, tag)}
                          onMouseEnter={() => showReferencePeek(tag.tagId, true)}
                          onMouseLeave={scheduleReferenceClose}
                          onPointerCancel={() => {
                            endReferenceHold();
                            suppressReferenceClickRef.current = false;
                          }}
                          onPointerDown={(event) => beginReferenceHold(tag, event.pointerType)}
                          onPointerUp={endReferenceHold}
                          type="button"
                        >
                          <span>p. {tag.pageNumber}</span>
                          <strong>{tag.label}</strong>
                        </button>
                      ))}
                      {lineTags.length === 0 ? <span className={styles.referenceLineEmpty}>Drop tags here</span> : null}
                    </div>
                  </section>
                );
              })}
            </div>
            {referenceTags.length === 0 ? <p>Keep a page you expect to revisit.</p> : null}
            <small id="reference-tray-help">Hover or hold to peek. Drag to arrange; Alt + arrows also move tags.</small>
          </section>

          {peekedReference && documentProxy ? (
            <ReferencePeek
              documentProxy={documentProxy}
              onCancelClose={cancelReferenceClose}
              onClose={scheduleReferenceClose}
              onOpen={() => openReferencePage(peekedReference)}
              onRemove={() => removeReferenceTag(peekedReference.tagId)}
              tag={peekedReference}
            />
          ) : null}
        </div>
      ) : null}

      <div
        aria-label="Scrollable PDF document"
        className={styles.stage}
        onScroll={handleScroll}
        ref={stageRef}
        tabIndex={0}
      >
        <div className={styles.document} ref={documentElementRef}>
          {status === "ready" && documentProxy ? Array.from(
            { length: pageCount },
            (_, index) => (
              <PdfPage
                comments={comments.filter((comment) => comment.pageNumber === index + 1)}
                containerSize={containerSize}
                documentProxy={documentProxy}
                key={index + 1}
                onCaptureSelection={captureSelection}
                onRenderError={handlePageRenderError}
                ocrSpans={ocrPages.find((page) => page.page_number === index + 1)?.spans ?? EMPTY_OCR_SPANS}
                pageNumber={index + 1}
                stageRef={stageRef}
                title={title}
                zoom={zoom}
              />
            )
          ) : null}
        </div>
      </div>

      {message ? (
        <p className={styles.status} role={status === "error" ? "alert" : "status"}>{message}</p>
      ) : null}

      {selectionPrompt ? (
        <div
          className={styles.selectionActions}
          onMouseDown={(event) => event.preventDefault()}
          style={{ left: selectionPrompt.left, top: selectionPrompt.top }}
        >
          <button onClick={commentOnSelection} type="button">Comment</button>
          <button onClick={askAboutSelection} type="button">Ask</button>
        </div>
      ) : null}
      {selectionError ? (
        <p className={styles.selectionError} role="alert">{selectionError}</p>
      ) : null}
    </div>
  );
}
