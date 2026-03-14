import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import {
  BookOpen,
  ZoomIn,
  ZoomOut,
  ChevronUp,
  ChevronDown,
  ChevronRight,
  Search,
  X,
  Sparkles,
  StickyNote,
  Quote,
  MessageSquare,
  FileUp,
  Loader2,
  AlertTriangle,
  Highlighter,
  Columns2,
  Download,
  Pencil,
  Trash2,
  PanelRightOpen,
  PanelRightClose,
} from "lucide-react";
import {
  useResearchStore,
  type ResearchPaper,
  type PdfAnnotation,
} from "../../store/useResearchStore";
import PageSummaryPanel from "./PageSummary";

pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const HIGHLIGHT_COLORS = [
  { name: "Yellow", value: "rgba(250,204,21,0.35)" },
  { name: "Green", value: "rgba(74,222,128,0.35)" },
  { name: "Blue", value: "rgba(96,165,250,0.35)" },
  { name: "Pink", value: "rgba(244,114,182,0.35)" },
  { name: "Orange", value: "rgba(251,146,60,0.35)" },
];

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function PdfLoadingSkeleton() {
  return (
    <div className="flex items-center justify-center h-96">
      <Loader2 size={24} className="animate-spin text-gray-500" />
    </div>
  );
}

function PdfError() {
  return (
    <div className="flex flex-col items-center justify-center h-96 text-gray-500 gap-2">
      <AlertTriangle size={28} className="text-red-400" />
      <p className="text-sm">Failed to load PDF</p>
      <p className="text-xs text-gray-600">
        The file may be corrupted or unsupported.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Highlight color picker toolbar (shown on text selection)
// ---------------------------------------------------------------------------

function HighlightToolbar({
  x,
  y,
  text,
  onHighlight,
  onDismiss,
}: {
  x: number;
  y: number;
  text: string;
  onHighlight: (color: string) => void;
  onDismiss: () => void;
}) {
  const actions = [
    {
      label: "Summarize",
      icon: <Sparkles size={12} />,
      action: () => {
        window.dispatchEvent(
          new CustomEvent("persistent-chat:send", {
            detail: { message: `Summarize the following excerpt from the PDF:\n\n> ${text}` },
          }),
        );
      },
    },
    {
      label: "Add to Notes",
      icon: <StickyNote size={12} />,
      action: () => {
        useResearchStore.getState().addNote({ content: text, tags: [], sourceType: "pdf" });
      },
    },
    {
      label: "Cite This",
      icon: <Quote size={12} />,
      action: () => {
        const paper = useResearchStore.getState().papers.find(
          (p) => p.id === useResearchStore.getState().activePaperId,
        );
        if (paper) {
          const citation = `[@${(paper.authors[0] ?? "unknown").split(" ").pop()?.toLowerCase()}${paper.year}]`;
          navigator.clipboard.writeText(citation);
        }
      },
    },
    {
      label: "Ask About This",
      icon: <MessageSquare size={12} />,
      action: () => {
        window.dispatchEvent(
          new CustomEvent("persistent-chat:send", {
            detail: { message: `Explain the following passage from the paper:\n\n> ${text}` },
          }),
        );
      },
    },
  ];

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest("[data-selection-popup]")) {
        onDismiss();
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [onDismiss]);

  return (
    <div
      data-selection-popup
      className="fixed z-50 bg-gray-800 border border-gray-700 rounded-lg shadow-xl py-1.5 px-1.5"
      style={{ left: x, top: y }}
    >
      {/* Color picker row */}
      <div className="flex items-center gap-1 px-1 pb-1.5 mb-1.5 border-b border-gray-700">
        <Highlighter size={11} className="text-gray-500 mr-1" />
        {HIGHLIGHT_COLORS.map((c) => (
          <button
            key={c.name}
            onClick={() => {
              onHighlight(c.value);
              onDismiss();
            }}
            className="w-5 h-5 rounded-full border border-gray-600 hover:scale-125 transition-transform cursor-pointer"
            style={{ background: c.value.replace("0.35", "0.8") }}
            title={`Highlight ${c.name}`}
          />
        ))}
      </div>
      {/* Action buttons */}
      <div className="flex gap-0.5">
        {actions.map((a) => (
          <button
            key={a.label}
            onClick={() => {
              a.action();
              onDismiss();
            }}
            className="flex items-center gap-1 px-2 py-1.5 text-[10px] text-gray-300 hover:bg-gray-700 rounded cursor-pointer"
            title={a.label}
          >
            {a.icon}
            {a.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Annotation note editor popup
// ---------------------------------------------------------------------------

function NoteEditor({
  annotation,
  onSave,
  onClose,
}: {
  annotation: PdfAnnotation;
  onSave: (note: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(annotation.note ?? "");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="fixed z-50 bg-[#252526] border border-gray-700 rounded-lg shadow-xl p-3 w-72"
      style={{ left: "50%", top: "50%", transform: "translate(-50%, -50%)" }}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-300 font-medium">Annotation Note</span>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
          <X size={14} />
        </button>
      </div>
      <p className="text-[10px] text-gray-500 mb-2 line-clamp-2 italic">"{annotation.text}"</p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Add your note..."
        className="w-full h-20 bg-[#1e1e1e] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 placeholder-gray-600 outline-none focus:border-blue-500/50 resize-none"
        autoFocus
      />
      <div className="flex justify-end gap-1.5 mt-2">
        <button
          onClick={onClose}
          className="px-2 py-1 text-[10px] text-gray-500 hover:text-gray-300 rounded"
        >
          Cancel
        </button>
        <button
          onClick={() => {
            onSave(text);
            onClose();
          }}
          className="px-2 py-1 text-[10px] text-white bg-blue-600 hover:bg-blue-500 rounded"
        >
          Save
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Annotation sidebar
// ---------------------------------------------------------------------------

function AnnotationSidebar({
  paperId,
  onJumpToPage,
  onClose,
}: {
  paperId: string;
  onJumpToPage: (page: number) => void;
  onClose: () => void;
}) {
  const annotations = useResearchStore((s) => s.annotations).filter(
    (a) => a.paperId === paperId,
  );
  const updateAnnotation = useResearchStore((s) => s.updateAnnotation);
  const removeAnnotation = useResearchStore((s) => s.removeAnnotation);
  const [editingId, setEditingId] = useState<string | null>(null);

  const grouped = useMemo(() => {
    const map = new Map<number, PdfAnnotation[]>();
    for (const ann of annotations) {
      const list = map.get(ann.page) ?? [];
      list.push(ann);
      map.set(ann.page, list);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a - b);
  }, [annotations]);

  const exportAnnotations = () => {
    const lines: string[] = ["# Annotations\n"];
    for (const [page, anns] of grouped) {
      lines.push(`## Page ${page}\n`);
      for (const ann of anns) {
        lines.push(`> ${ann.text}\n`);
        if (ann.note) lines.push(`**Note:** ${ann.note}\n`);
        lines.push("");
      }
    }
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "annotations.md";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="w-64 border-l border-gray-800 bg-[#1e1e1e] flex flex-col h-full shrink-0">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <span className="text-xs text-gray-300 font-medium">Annotations</span>
        <div className="flex items-center gap-1">
          <button
            onClick={exportAnnotations}
            className="p-1 text-gray-500 hover:text-gray-300"
            title="Export Notes"
          >
            <Download size={12} />
          </button>
          <button onClick={onClose} className="p-1 text-gray-500 hover:text-gray-300">
            <X size={12} />
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {grouped.length === 0 ? (
          <div className="px-3 py-8 text-center text-gray-600 text-xs">
            <Highlighter size={20} className="mx-auto mb-2 opacity-40" />
            <p>No annotations yet</p>
            <p className="text-[10px] mt-1">Select text in the PDF to highlight</p>
          </div>
        ) : (
          grouped.map(([page, anns]) => (
            <div key={page}>
              <button
                onClick={() => onJumpToPage(page)}
                className="w-full text-left px-3 py-1.5 text-[10px] font-medium text-gray-500 bg-gray-800/30 hover:bg-gray-800/60 flex items-center gap-1"
              >
                <ChevronRight size={10} />
                Page {page}
                <span className="text-gray-600 ml-auto">{anns.length}</span>
              </button>
              {anns.map((ann) => (
                <div
                  key={ann.id}
                  className="px-3 py-1.5 border-b border-gray-800/30 group hover:bg-gray-800/20"
                >
                  <div className="flex items-start gap-1.5">
                    <div
                      className="w-2 h-2 rounded-full mt-1 shrink-0"
                      style={{ background: ann.color.replace("0.35", "0.8") }}
                    />
                    <p
                      className="text-[10px] text-gray-400 line-clamp-2 flex-1 cursor-pointer"
                      onClick={() => onJumpToPage(ann.page)}
                    >
                      {ann.text}
                    </p>
                  </div>
                  {ann.note && (
                    <p className="text-[10px] text-blue-400/70 mt-0.5 ml-3.5 italic">
                      {ann.note}
                    </p>
                  )}
                  <div className="flex gap-1 mt-0.5 ml-3 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => setEditingId(ann.id)}
                      className="text-gray-600 hover:text-gray-400"
                      title="Edit note"
                    >
                      <Pencil size={10} />
                    </button>
                    <button
                      onClick={() => removeAnnotation(ann.id)}
                      className="text-gray-600 hover:text-red-400"
                      title="Delete"
                    >
                      <Trash2 size={10} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      {editingId && (
        <NoteEditor
          annotation={annotations.find((a) => a.id === editingId)!}
          onSave={(note) => updateAnnotation(editingId, { note })}
          onClose={() => setEditingId(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Highlight overlay on PDF page
// ---------------------------------------------------------------------------

function HighlightOverlay({
  annotations,
  scale,
  onClickAnnotation,
}: {
  annotations: PdfAnnotation[];
  scale: number;
  onClickAnnotation: (ann: PdfAnnotation) => void;
}) {
  if (annotations.length === 0) return null;

  return (
    <div className="absolute inset-0 pointer-events-none">
      {annotations.map((ann) =>
        ann.rects.map((rect, i) => (
          <div
            key={`${ann.id}-${i}`}
            className="absolute pointer-events-auto cursor-pointer hover:opacity-80 transition-opacity"
            style={{
              left: rect.x * scale,
              top: rect.y * scale,
              width: rect.w * scale,
              height: rect.h * scale,
              background: ann.color,
              borderRadius: 2,
            }}
            onClick={() => onClickAnnotation(ann)}
            title={ann.note || ann.text.slice(0, 60)}
          />
        )),
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Drag-and-drop overlay
// ---------------------------------------------------------------------------

function DropOverlay() {
  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-blue-900/40 border-2 border-dashed border-blue-400 rounded-lg pointer-events-none">
      <div className="text-center">
        <FileUp size={36} className="mx-auto mb-2 text-blue-300" />
        <p className="text-sm text-blue-200 font-medium">Drop PDF to open</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export interface PdfReaderProps {
  paperId?: string | null;
  showSplitButton?: boolean;
  onRequestSplit?: () => void;
}

export default function PdfReader({
  paperId: externalPaperId,
  showSplitButton = true,
  onRequestSplit,
}: PdfReaderProps = {}) {
  const papers = useResearchStore((s) => s.papers);
  const storeActivePaperId = useResearchStore((s) => s.activePaperId);
  const activePaperId = externalPaperId !== undefined ? externalPaperId : storeActivePaperId;
  const activePaper = papers.find((p: ResearchPaper) => p.id === activePaperId);

  const annotations = useResearchStore((s) => s.annotations);
  const addAnnotation = useResearchStore((s) => s.addAnnotation);
  const updateAnnotation = useResearchStore((s) => s.updateAnnotation);

  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [scale, setScale] = useState(1.2);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchVisible, setSearchVisible] = useState(false);
  const [selectedText, setSelectedText] = useState("");
  const [selectionActions, setSelectionActions] = useState<{ x: number; y: number } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [showAnnotationSidebar, setShowAnnotationSidebar] = useState(false);
  const [showSummarySidebar, setShowSummarySidebar] = useState(false);
  const [editingAnnotation, setEditingAnnotation] = useState<PdfAnnotation | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const currentPageAnnotations = useMemo(
    () =>
      activePaperId
        ? annotations.filter((a) => a.paperId === activePaperId && a.page === currentPage)
        : [],
    [annotations, activePaperId, currentPage],
  );

  const totalAnnotations = useMemo(
    () => (activePaperId ? annotations.filter((a) => a.paperId === activePaperId).length : 0),
    [annotations, activePaperId],
  );

  useEffect(() => {
    setCurrentPage(1);
    setNumPages(0);
    setSearchQuery("");
    setSearchVisible(false);
    setSelectedText("");
    setSelectionActions(null);
  }, [activePaperId]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "f") {
        e.preventDefault();
        setSearchVisible(true);
        setTimeout(() => searchInputRef.current?.focus(), 0);
      }
      if (e.key === "Escape" && searchVisible) {
        setSearchVisible(false);
      }
    };
    const el = containerRef.current?.closest("[data-pdf-reader]");
    if (el) {
      el.addEventListener("keydown", handler as EventListener);
      return () => el.removeEventListener("keydown", handler as EventListener);
    }
  }, [searchVisible]);

  const handleTextSelection = useCallback(() => {
    const selection = window.getSelection();
    const text = selection?.toString().trim();
    if (text && text.length > 2) {
      setSelectedText(text);
      const range = selection!.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      setSelectionActions({
        x: Math.min(rect.left + rect.width / 2 - 160, window.innerWidth - 360),
        y: rect.top - 80,
      });
    } else {
      setSelectedText("");
      setSelectionActions(null);
    }
  }, []);

  const handleHighlight = useCallback(
    (color: string) => {
      if (!activePaperId || !selectedText) return;
      const selection = window.getSelection();
      if (!selection || selection.rangeCount === 0) return;

      const range = selection.getRangeAt(0);
      const pageEl = pageRef.current;
      if (!pageEl) return;

      const pageRect = pageEl.getBoundingClientRect();
      const rects: Array<{ x: number; y: number; w: number; h: number }> = [];
      const clientRects = range.getClientRects();
      for (let i = 0; i < clientRects.length; i++) {
        const r = clientRects[i];
        rects.push({
          x: (r.left - pageRect.left) / scale,
          y: (r.top - pageRect.top) / scale,
          w: r.width / scale,
          h: r.height / scale,
        });
      }

      addAnnotation({
        paperId: activePaperId,
        page: currentPage,
        type: "highlight",
        color,
        text: selectedText,
        rects,
      });

      selection.removeAllRanges();
      setSelectedText("");
      setSelectionActions(null);
    },
    [activePaperId, selectedText, currentPage, scale, addAnnotation],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes("Files")) {
      e.preventDefault();
      setDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (e.currentTarget === e.target || !e.currentTarget.contains(e.relatedTarget as Node)) {
      setDragOver(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    for (const file of files) {
      if (file.type === "application/pdf" || file.name.endsWith(".pdf")) {
        const filePath = (file as unknown as { path?: string }).path;
        if (filePath) {
          useResearchStore.getState().addPaper({
            title: file.name.replace(/\.pdf$/i, ""),
            authors: [],
            year: new Date().getFullYear(),
            filePath,
            status: "unread",
            tags: [],
          });
        }
      }
    }
  }, []);

  const handleLoadSuccess = useCallback(({ numPages: n }: { numPages: number }) => {
    setNumPages(n);
  }, []);

  const jumpToPage = useCallback((page: number) => {
    setCurrentPage(page);
  }, []);

  // -------------------------------------------------------------------------
  // Empty state
  // -------------------------------------------------------------------------

  if (!activePaper?.filePath) {
    return (
      <div
        data-pdf-reader
        className="h-full flex items-center justify-center text-gray-500 relative"
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {dragOver && <DropOverlay />}
        <div className="text-center">
          <BookOpen size={32} className="mx-auto mb-3 text-gray-600" />
          <p className="text-sm">No paper selected</p>
          <p className="text-xs text-gray-600 mt-1">
            Select a paper from the sidebar or drag a PDF here
          </p>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Active PDF view
  // -------------------------------------------------------------------------

  return (
    <div
      data-pdf-reader
      className="h-full flex bg-[#1a1a1a] relative"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div className="flex-1 flex flex-col min-w-0">
        {dragOver && <DropOverlay />}

        {/* Toolbar */}
        <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-800 bg-[#252526] shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-xs text-gray-300 truncate max-w-[200px]">
              {activePaper.title}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            {/* Zoom */}
            <button
              onClick={() => setScale((s) => Math.max(0.5, +(s - 0.1).toFixed(1)))}
              className="p-1 text-gray-400 hover:text-gray-200 cursor-pointer"
              title="Zoom out"
            >
              <ZoomOut size={14} />
            </button>
            <span className="text-[10px] text-gray-500 w-10 text-center select-none">
              {Math.round(scale * 100)}%
            </span>
            <button
              onClick={() => setScale((s) => Math.min(3, +(s + 0.1).toFixed(1)))}
              className="p-1 text-gray-400 hover:text-gray-200 cursor-pointer"
              title="Zoom in"
            >
              <ZoomIn size={14} />
            </button>

            <div className="w-px h-4 bg-gray-700 mx-1" />

            {/* Page nav */}
            <button
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage <= 1}
              className="p-1 text-gray-400 hover:text-gray-200 disabled:opacity-30 cursor-pointer disabled:cursor-default"
              title="Previous page"
            >
              <ChevronUp size={14} />
            </button>
            <span className="text-[10px] text-gray-500 select-none">
              {currentPage}/{numPages || "–"}
            </span>
            <button
              onClick={() => setCurrentPage((p) => Math.min(numPages, p + 1))}
              disabled={currentPage >= numPages}
              className="p-1 text-gray-400 hover:text-gray-200 disabled:opacity-30 cursor-pointer disabled:cursor-default"
              title="Next page"
            >
              <ChevronDown size={14} />
            </button>

            <div className="w-px h-4 bg-gray-700 mx-1" />

            {/* Search toggle */}
            <button
              onClick={() => {
                setSearchVisible((v) => !v);
                if (!searchVisible) setTimeout(() => searchInputRef.current?.focus(), 0);
              }}
              className={`p-1 rounded cursor-pointer ${searchVisible ? "text-blue-400 bg-gray-700/50" : "text-gray-400 hover:text-gray-200"}`}
              title="Search in PDF (Ctrl+F)"
            >
              <Search size={14} />
            </button>

            {/* Annotation sidebar toggle */}
            <button
              onClick={() => setShowAnnotationSidebar((v) => !v)}
              className={`p-1 rounded cursor-pointer relative ${showAnnotationSidebar ? "text-yellow-400 bg-gray-700/50" : "text-gray-400 hover:text-gray-200"}`}
              title="Annotations"
            >
              {showAnnotationSidebar ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
              {totalAnnotations > 0 && (
                <span className="absolute -top-0.5 -right-0.5 text-[8px] bg-yellow-600 text-white rounded-full w-3.5 h-3.5 flex items-center justify-center">
                  {totalAnnotations}
                </span>
              )}
            </button>

            {/* Summary sidebar toggle */}
            <button
              onClick={() => setShowSummarySidebar((v) => !v)}
              className={`p-1 rounded cursor-pointer ${showSummarySidebar ? "text-purple-400 bg-gray-700/50" : "text-gray-400 hover:text-gray-200"}`}
              title="Page Summaries"
            >
              <Sparkles size={14} />
            </button>

            {/* Split button */}
            {showSplitButton && onRequestSplit && (
              <button
                onClick={onRequestSplit}
                className="p-1 text-gray-400 hover:text-gray-200 cursor-pointer"
                title="Split view"
              >
                <Columns2 size={14} />
              </button>
            )}
          </div>
        </div>

        {/* Search bar */}
        {searchVisible && (
          <div className="px-3 py-1.5 border-b border-gray-800 bg-[#252526] flex items-center gap-2 shrink-0">
            <Search size={12} className="text-gray-500 shrink-0" />
            <input
              ref={searchInputRef}
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search in PDF..."
              className="flex-1 bg-transparent border-none outline-none text-xs text-gray-200 placeholder-gray-500"
              autoFocus
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="p-0.5 text-gray-500 hover:text-gray-300 cursor-pointer"
              >
                <X size={12} />
              </button>
            )}
            <button
              onClick={() => setSearchVisible(false)}
              className="text-[10px] text-gray-500 hover:text-gray-300 cursor-pointer"
            >
              Esc
            </button>
          </div>
        )}

        {/* PDF content */}
        <div ref={containerRef} className="flex-1 overflow-auto" onMouseUp={handleTextSelection}>
          <div ref={pageRef} className="relative inline-block mx-auto">
            <Document
              file={activePaper.filePath}
              onLoadSuccess={handleLoadSuccess}
              loading={<PdfLoadingSkeleton />}
              error={<PdfError />}
            >
              <Page
                pageNumber={currentPage}
                scale={scale}
                renderTextLayer={true}
                renderAnnotationLayer={true}
                className="mx-auto"
                customTextRenderer={
                  searchQuery
                    ? ({ str }: { str: string }) => highlightSearchText(str, searchQuery)
                    : undefined
                }
              />
            </Document>

            {/* Highlight overlay */}
            <HighlightOverlay
              annotations={currentPageAnnotations}
              scale={scale}
              onClickAnnotation={(ann) => setEditingAnnotation(ann)}
            />
          </div>
        </div>

        {/* Highlight toolbar popup */}
        {selectionActions && selectedText && (
          <HighlightToolbar
            x={selectionActions.x}
            y={selectionActions.y}
            text={selectedText}
            onHighlight={handleHighlight}
            onDismiss={() => {
              setSelectionActions(null);
              setSelectedText("");
            }}
          />
        )}

        {/* Note editor popup */}
        {editingAnnotation && (
          <NoteEditor
            annotation={editingAnnotation}
            onSave={(note) => updateAnnotation(editingAnnotation.id, { note })}
            onClose={() => setEditingAnnotation(null)}
          />
        )}
      </div>

      {/* Annotation sidebar */}
      {showAnnotationSidebar && activePaperId && (
        <AnnotationSidebar
          paperId={activePaperId}
          onJumpToPage={jumpToPage}
          onClose={() => setShowAnnotationSidebar(false)}
        />
      )}

      {/* Page summary sidebar */}
      {showSummarySidebar && activePaperId && (
        <PageSummaryPanel
          paperId={activePaperId}
          currentPage={currentPage}
          numPages={numPages}
          onJumpToPage={jumpToPage}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Search highlighting
// ---------------------------------------------------------------------------

function highlightSearchText(str: string, query: string): string {
  if (!query) return str;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(${escaped})`, "gi");
  return str.replace(
    re,
    '<mark style="background:rgba(250,204,21,0.4);color:inherit;border-radius:2px;padding:0 1px">$1</mark>',
  );
}
