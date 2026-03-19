import {
  useState,
  useRef,
  useMemo,
  useEffect,
  useCallback,
} from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import type { editor as monacoEditor, IRange } from "monaco-editor";
import katex from "katex";
import "katex/dist/katex.min.css";
import { Marked, Renderer } from "marked";
import {
  Download,
  PenLine,
  SplitSquareHorizontal,
  Eye,
  Sparkles,
  BookOpen,
  AlignLeft,
  Languages,
  Quote,
  Check,
  X,
  Loader2,
} from "lucide-react";
import { useResearchStore, type ResearchPaper } from "../../store/useResearchStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";
import { requestEditorChatText } from "../../lib/editorChat";
import { sanitizeHtml } from "../../lib/sanitizeHtml";

// ---------------------------------------------------------------------------
// Markdown rendering with KaTeX math support
// ---------------------------------------------------------------------------

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderKatex(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex.trim(), {
      displayMode,
      throwOnError: false,
      strict: false,
    });
  } catch {
    const tag = displayMode ? "div" : "span";
    return `<${tag} class="text-red-400 text-xs">[Math error]</${tag}>`;
  }
}

function buildMarkedInstance(): Marked {
  const renderer = new Renderer();

  renderer.heading = ({ text, depth }: { text: string; depth: number }) => {
    const tag = `h${depth}` as keyof HTMLElementTagNameMap;
    const anchor = text
      .toLowerCase()
      .replace(/[^\w]+/g, "-")
      .replace(/(^-|-$)/g, "");
    return `<${tag} id="${anchor}">${text}</${tag}>`;
  };

  renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
    const escaped = escapeHtml(text);
    const langLabel = lang ? `<span class="text-[10px] text-gray-500 absolute top-1 right-2">${lang}</span>` : "";
    return `<div class="relative"><pre class="bg-[#12122a] rounded-md p-3 my-2 overflow-x-auto text-[13px] leading-relaxed">${langLabel}<code>${escaped}</code></pre></div>`;
  };

  renderer.blockquote = ({ raw }: { raw: string }) =>
    `<blockquote class="border-l-3 border-blue-500/50 pl-4 my-3 text-gray-400 italic">${raw}</blockquote>`;

  return new Marked({ renderer, async: false });
}

const markedInstance = buildMarkedInstance();

function renderMarkdownWithMath(content: string): string {
  let processed = content;

  // Block math: $$...$$
  processed = processed.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => {
    const html = renderKatex(tex, true);
    return `<div class="math-block my-4 text-center">${html}</div>`;
  });

  // Inline math: $...$  (but not $$)
  processed = processed.replace(
    /(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)/g,
    (_, tex) => renderKatex(tex, false),
  );

  return markedInstance.parse(processed) as string;
}

// ---------------------------------------------------------------------------
// Citation utilities
// ---------------------------------------------------------------------------

const CITATION_PATTERNS = [
  /\[@([a-zA-Z]+\d{4}[a-z]?)\]/g,               // [@author2024]
  /\[([A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?,\s*\d{4})\]/g, // [Author, 2024]
  /\(([A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?\s+\d{4})\)/g,  // (Author 2024)
];

function extractCiteKey(citation: string): string {
  return citation
    .replace(/[@\[\]\(\)]/g, "")
    .replace(/,\s*/g, "")
    .replace(/\s+/g, "")
    .toLowerCase();
}

function matchPaper(citeKey: string, papers: ResearchPaper[]): ResearchPaper | undefined {
  const normalized = citeKey.toLowerCase();
  return papers.find((p) => {
    for (const author of p.authors) {
      const lastName = author.split(" ").pop()?.toLowerCase() ?? "";
      const yearStr = String(p.year);
      const key = `${lastName}${yearStr}`;
      if (normalized === key || normalized.includes(lastName + yearStr)) return true;
    }
    return false;
  });
}

function makeCitationsClickable(html: string, papers: ResearchPaper[]): string {
  let result = html;
  for (const pattern of CITATION_PATTERNS) {
    result = result.replace(new RegExp(pattern.source, "g"), (fullMatch, _inner) => {
      const key = extractCiteKey(fullMatch);
      const paper = matchPaper(key, papers);
      if (paper) {
        return `<span class="citation-link cursor-pointer text-blue-400 hover:text-blue-300 underline decoration-dotted underline-offset-2" data-paper-id="${paper.id}" data-cite-key="${key}" title="${paper.title}">${fullMatch}</span>`;
      }
      return `<span class="text-gray-500" title="Unmatched citation">${fullMatch}</span>`;
    });
  }
  return result;
}

// ---------------------------------------------------------------------------
// MarkdownPreview — with clickable citations
// ---------------------------------------------------------------------------

function MarkdownPreview({
  content,
  onCitationClick,
}: {
  content: string;
  onCitationClick?: (paperId: string) => void;
}) {
  const papers = useResearchStore((s) => s.papers);
  const html = useMemo(() => {
    const rendered = renderMarkdownWithMath(content);
    return sanitizeHtml(makeCitationsClickable(rendered, papers));
  }, [content, papers]);

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      const target = (e.target as HTMLElement).closest("[data-paper-id]");
      if (target && onCitationClick) {
        const paperId = target.getAttribute("data-paper-id");
        if (paperId) onCitationClick(paperId);
      }
    },
    [onCitationClick],
  );

  return (
    <div
      className="writing-preview prose prose-invert prose-sm max-w-none
        prose-headings:font-serif prose-headings:tracking-tight
        prose-h1:text-2xl prose-h1:mt-8 prose-h1:mb-4
        prose-h2:text-xl prose-h2:mt-6 prose-h2:mb-3
        prose-h3:text-base prose-h3:mt-4 prose-h3:mb-2
        prose-p:leading-relaxed prose-p:text-gray-300
        prose-a:text-blue-400 prose-a:no-underline hover:prose-a:underline
        prose-strong:text-gray-200 prose-em:text-gray-300
        prose-li:text-gray-300 prose-li:leading-relaxed
        prose-code:text-pink-400 prose-code:bg-gray-800/50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-[13px]"
      dangerouslySetInnerHTML={{ __html: html }}
      onClick={handleClick}
    />
  );
}

// ---------------------------------------------------------------------------
// SectionStatusBadges — track which sections exist in the document
// ---------------------------------------------------------------------------

type SectionStatus = "draft" | "generating" | "review" | "final";

interface SectionInfo {
  title: string;
  status: SectionStatus;
}

const STATUS_COLORS: Record<SectionStatus, string> = {
  draft: "bg-gray-700 text-gray-400",
  generating: "bg-yellow-900/60 text-yellow-400",
  review: "bg-blue-900/60 text-blue-400",
  final: "bg-green-900/60 text-green-400",
};

function SectionStatusBadges() {
  const content = useResearchStore((s) => s.documentContent);

  const sections = useMemo<SectionInfo[]>(() => {
    const headings = content.match(/^#{1,3}\s+.+$/gm) ?? [];
    return headings.map((h) => {
      const title = h.replace(/^#+\s+/, "");
      return { title, status: "draft" as SectionStatus };
    });
  }, [content]);

  if (sections.length === 0) return null;

  return (
    <div className="flex items-center gap-1 ml-2">
      {sections.slice(0, 5).map((s, i) => (
        <span
          key={i}
          className={`text-[9px] px-1.5 py-0.5 rounded ${STATUS_COLORS[s.status]}`}
          title={`${s.title} (${s.status})`}
        >
          {s.title.length > 14 ? s.title.slice(0, 14) + "…" : s.title}
        </span>
      ))}
      {sections.length > 5 && (
        <span className="text-[9px] text-gray-600">+{sections.length - 5}</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ViewModeToggle
// ---------------------------------------------------------------------------

type ViewMode = "edit" | "split" | "preview";

const VIEW_ICONS: Record<ViewMode, typeof PenLine> = {
  edit: PenLine,
  split: SplitSquareHorizontal,
  preview: Eye,
};

function ViewModeToggle({
  mode,
  onChange,
}: {
  mode: ViewMode;
  onChange: (m: ViewMode) => void;
}) {
  return (
    <div className="flex bg-gray-800 rounded overflow-hidden">
      {(["edit", "split", "preview"] as const).map((m) => {
        const Icon = VIEW_ICONS[m];
        return (
          <button
            key={m}
            onClick={() => onChange(m)}
            className={`flex items-center gap-1 px-2 py-0.5 text-[10px] transition-colors ${
              mode === m
                ? "bg-blue-600 text-white"
                : "text-gray-400 hover:text-gray-200 hover:bg-gray-700"
            }`}
            title={m.charAt(0).toUpperCase() + m.slice(1)}
          >
            <Icon size={10} />
            {m.charAt(0).toUpperCase() + m.slice(1)}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Export — Markdown / LaTeX / PDF
// ---------------------------------------------------------------------------

function convertToLaTeX(markdown: string): string {
  const preamble = [
    "\\documentclass[12pt]{article}",
    "\\usepackage[utf8]{inputenc}",
    "\\usepackage{amsmath,amssymb,amsfonts}",
    "\\usepackage{graphicx}",
    "\\usepackage{hyperref}",
    "\\usepackage[margin=1in]{geometry}",
    "",
    "\\begin{document}",
    "",
  ].join("\n");

  let body = markdown
    .replace(/^# (.+)$/gm, "\\section{$1}")
    .replace(/^## (.+)$/gm, "\\subsection{$1}")
    .replace(/^### (.+)$/gm, "\\subsubsection{$1}")
    .replace(/\*\*(.+?)\*\*/g, "\\textbf{$1}")
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "\\textit{$1}")
    .replace(/`([^`]+)`/g, "\\texttt{$1}")
    .replace(/^\- (.+)$/gm, "\\item $1")
    .replace(/^\d+\.\s+(.+)$/gm, "\\item $1");

  // Wrap consecutive \item blocks in itemize
  body = body.replace(
    /(\\item .+\n?)+/g,
    (match) => `\\begin{itemize}\n${match}\\end{itemize}\n`,
  );

  return preamble + body + "\n\n\\end{document}\n";
}

function downloadFile(filename: string, content: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function ExportButton() {
  const [showMenu, setShowMenu] = useState(false);
  const content = useResearchStore((s) => s.documentContent);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showMenu) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node))
        setShowMenu(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showMenu]);

  const exportAs = (format: "markdown" | "latex") => {
    if (format === "markdown") {
      downloadFile("research-document.md", content, "text/markdown");
    } else {
      downloadFile("research-document.tex", convertToLaTeX(content), "text/x-tex");
    }
    setShowMenu(false);
  };

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setShowMenu((v) => !v)}
        className="flex items-center gap-1 px-2 py-1 text-[11px] text-gray-400 hover:text-gray-200 bg-gray-800 rounded transition-colors"
      >
        <Download size={12} />
        Export
      </button>
      {showMenu && (
        <div className="absolute right-0 top-full mt-1 bg-[#252526] border border-gray-700 rounded shadow-lg py-1 z-50 min-w-36">
          <button
            onClick={() => exportAs("markdown")}
            className="block w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-[#094771] hover:text-white"
          >
            Markdown (.md)
          </button>
          <button
            onClick={() => exportAs("latex")}
            className="block w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-[#094771] hover:text-white"
          >
            LaTeX (.tex)
          </button>
          <button
            disabled
            className="block w-full text-left px-3 py-1.5 text-xs text-gray-500 cursor-not-allowed"
          >
            PDF (requires LaTeX)
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Floating AI Toolbar — appears when text is selected
// ---------------------------------------------------------------------------

interface AIAction {
  label: string;
  icon: typeof Sparkles;
  prompt: string;
}

const AI_ACTIONS: AIAction[] = [
  { label: "Expand", icon: Sparkles, prompt: "Expand and elaborate on this text with more detail and supporting arguments." },
  { label: "Formal", icon: BookOpen, prompt: "Rewrite this text in a formal academic tone suitable for a research paper." },
  { label: "Cite", icon: Quote, prompt: "Add appropriate citations and references to support the claims in this text." },
  { label: "Simplify", icon: AlignLeft, prompt: "Simplify this text using plain, accessible language while preserving the meaning." },
  { label: "Translate", icon: Languages, prompt: "Translate this text to English, preserving academic tone." },
];

interface FloatingToolbarState {
  visible: boolean;
  x: number;
  y: number;
  selectedText: string;
  selection: IRange | null;
}

function FloatingAIToolbar({
  state,
  editor: _editor,
  onAction,
  onClose,
}: {
  state: FloatingToolbarState;
  editor: monacoEditor.IStandaloneCodeEditor;
  onAction: (action: AIAction, selectedText: string, range: IRange) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  if (!state.visible || !state.selection) return null;

  return (
    <div
      ref={ref}
      className="fixed z-50 flex items-center gap-0.5 bg-[#1e1e1e] border border-[#3c3c3c] rounded-lg shadow-2xl p-1"
      style={{ left: state.x, top: state.y }}
    >
      {AI_ACTIONS.map((action) => {
        const Icon = action.icon;
        return (
          <button
            key={action.label}
            onClick={() => onAction(action, state.selectedText, state.selection!)}
            className="flex items-center gap-1 px-2 py-1 text-[10px] text-gray-400 hover:text-white hover:bg-[#094771] rounded transition-colors"
            title={action.prompt}
          >
            <Icon size={11} />
            {action.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Track Changes — inline diff decorations with accept/reject
// ---------------------------------------------------------------------------

export interface TrackChange {
  id: string;
  range: IRange;
  originalText: string;
  proposedText: string;
  status: "pending" | "accepted" | "rejected";
  source: string;
}

function useTrackChanges(editor: monacoEditor.IStandaloneCodeEditor | null) {
  const [changes, setChanges] = useState<TrackChange[]>([]);
  const decorationsRef = useRef<string[]>([]);

  const applyDecorations = useCallback(
    (pending: TrackChange[]) => {
      if (!editor) return;
      const decorations = pending.map((c) => ({
        range: c.range,
        options: {
          className: "track-change-pending",
          glyphMarginClassName: "track-change-glyph",
          hoverMessage: { value: `**Suggested:** ${c.proposedText.slice(0, 100)}…\n\n_from ${c.source}_` },
          isWholeLine: false,
          stickiness: 1 as monacoEditor.TrackedRangeStickiness,
        },
      }));
      decorationsRef.current = editor.deltaDecorations(
        decorationsRef.current,
        decorations,
      );
    },
    [editor],
  );

  useEffect(() => {
    const pending = changes.filter((c) => c.status === "pending");
    applyDecorations(pending);
  }, [changes, applyDecorations]);

  const addChange = useCallback((change: Omit<TrackChange, "id" | "status">) => {
    const id = `tc-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    setChanges((prev) => [...prev, { ...change, id, status: "pending" }]);
  }, []);

  const acceptChange = useCallback(
    (id: string) => {
      const change = changes.find((c) => c.id === id);
      if (!change || !editor) return;
      const model = editor.getModel();
      if (!model) return;
      editor.executeEdits("track-changes", [
        { range: change.range, text: change.proposedText },
      ]);
      setChanges((prev) =>
        prev.map((c) => (c.id === id ? { ...c, status: "accepted" } : c)),
      );
    },
    [changes, editor],
  );

  const rejectChange = useCallback(
    (id: string) => {
      setChanges((prev) =>
        prev.map((c) => (c.id === id ? { ...c, status: "rejected" } : c)),
      );
    },
    [],
  );

  return { changes, addChange, acceptChange, rejectChange };
}

function TrackChangesBar({
  changes,
  onAccept,
  onReject,
}: {
  changes: TrackChange[];
  onAccept: (id: string) => void;
  onReject: (id: string) => void;
}) {
  const pending = changes.filter((c) => c.status === "pending");
  if (pending.length === 0) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-1 border-t border-gray-800 bg-[#1a1a2e] text-xs">
      <span className="text-yellow-400">{pending.length} pending change{pending.length !== 1 ? "s" : ""}</span>
      <div className="flex-1" />
      <button
        onClick={() => pending.forEach((c) => onAccept(c.id))}
        className="flex items-center gap-1 px-2 py-0.5 text-[10px] text-green-400 hover:bg-green-400/10 rounded transition-colors"
      >
        <Check size={10} /> Accept All
      </button>
      <button
        onClick={() => pending.forEach((c) => onReject(c.id))}
        className="flex items-center gap-1 px-2 py-0.5 text-[10px] text-red-400 hover:bg-red-400/10 rounded transition-colors"
      >
        <X size={10} /> Reject All
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Citation completion provider registration
// ---------------------------------------------------------------------------

let citationProviderRegistered = false;

function registerCitationProvider() {
  if (citationProviderRegistered) return;
  citationProviderRegistered = true;

  monaco.languages.registerCompletionItemProvider("markdown", {
    triggerCharacters: ["@"],
    provideCompletionItems: (model, position) => {
      const lineContent = model.getLineContent(position.lineNumber);
      const textBefore = lineContent.substring(0, position.column - 1);
      if (!textBefore.endsWith("[@")) return { suggestions: [] };

      const papers = useResearchStore.getState().papers;
      return {
        suggestions: papers.map((p) => {
          const lastAuthor = (p.authors[0] ?? "unknown").split(" ").pop()?.toLowerCase() ?? "unknown";
          const citeKey = `${lastAuthor}${p.year}`;
          return {
            label: `${p.authors[0] ?? "Unknown"} (${p.year})`,
            kind: monaco.languages.CompletionItemKind.Reference,
            insertText: `${citeKey}]`,
            documentation: {
              value: `**${p.title}**\n\n${p.authors.join(", ")} (${p.year})${p.doi ? `\n\nDOI: ${p.doi}` : ""}`,
            },
            detail: p.title,
            range: {
              startLineNumber: position.lineNumber,
              startColumn: position.column,
              endLineNumber: position.lineNumber,
              endColumn: position.column,
            },
          };
        }),
      };
    },
  });
}

// ---------------------------------------------------------------------------
// AI Action execution
// ---------------------------------------------------------------------------

function useAIAction() {
  const [loading, setLoading] = useState(false);
  const setDocumentContent = useResearchStore((s) => s.setDocumentContent);
  const documentContent = useResearchStore((s) => s.documentContent);

  const execute = useCallback(
    async (
      action: AIAction,
      selectedText: string,
      range: IRange,
      editor: monacoEditor.IStandaloneCodeEditor,
    ) => {
      setLoading(true);
      try {
        const result = await requestEditorChatText({
          message: `${action.prompt}\n\nText:\n${selectedText}`,
          mode: "ask",
          scope: `research-writing:${action.label.toLowerCase()}`,
          surfaceContext: {
            action: action.label,
            selected_text: selectedText,
          },
        });

        if (result) {
          const model = editor.getModel();
          if (model) {
            editor.executeEdits("ai-action", [{ range, text: result }]);
            setDocumentContent(model.getValue());
          }
        }
      } catch {
        // silently fail
      } finally {
        setLoading(false);
      }
    },
    [setDocumentContent, documentContent],
  );

  return { execute, loading };
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function WritingPane() {
  const { documentContent, setDocumentContent } = useResearchStore();
  const theme = useSettingsStore((s) => resolveMonacoTheme(s.theme));
  const [viewMode, setViewMode] = useState<ViewMode>("split");
  const editorRef = useRef<monacoEditor.IStandaloneCodeEditor | null>(null);
  const [toolbar, setToolbar] = useState<FloatingToolbarState>({
    visible: false,
    x: 0,
    y: 0,
    selectedText: "",
    selection: null,
  });

  const { changes, addChange, acceptChange, rejectChange } = useTrackChanges(editorRef.current);
  const { execute: executeAI, loading: aiLoading } = useAIAction();

  // Register the citation completion provider once Monaco loads
  const handleEditorMount: OnMount = useCallback(
    (editor, _monacoInstance) => {
      editorRef.current = editor;

      registerCitationProvider();

      // Cmd+Click on citations → navigate to PDF
      editor.onMouseDown((e: any) => {
        if (!e.event.metaKey && !e.event.ctrlKey) return;
        const pos = e.target.position;
        if (!pos) return;
        const model = editor.getModel();
        if (!model) return;
        const line = model.getLineContent(pos.lineNumber);
        const papers = useResearchStore.getState().papers;
        for (const pattern of CITATION_PATTERNS) {
          const re = new RegExp(pattern.source, "g");
          let m;
          while ((m = re.exec(line)) !== null) {
            const start = m.index + 1;
            const end = start + m[0].length;
            if (pos.column >= start && pos.column <= end) {
              const key = extractCiteKey(m[0]);
              const paper = matchPaper(key, papers);
              if (paper) {
                useResearchStore.getState().setActivePaper(paper.id);
                useResearchStore.getState().setPrimaryTab("reader");
                return;
              }
            }
          }
        }
      });

      // Selection → floating AI toolbar
      editor.onDidChangeCursorSelection(() => {
        const selection = editor.getSelection();
        if (!selection || selection.isEmpty()) {
          setToolbar((t) => ({ ...t, visible: false }));
          return;
        }

        const selectedText = editor.getModel()?.getValueInRange(selection) ?? "";
        if (selectedText.length < 5) {
          setToolbar((t) => ({ ...t, visible: false }));
          return;
        }

        const pos = editor.getScrolledVisiblePosition({
          lineNumber: selection.startLineNumber,
          column: selection.startColumn,
        });
        const domNode = editor.getDomNode();
        if (!pos || !domNode) return;

        const editorRect = domNode.getBoundingClientRect();
        const x = Math.max(
          editorRect.left + 8,
          Math.min(editorRect.left + pos.left, editorRect.right - 400),
        );
        const y = Math.max(0, editorRect.top + pos.top - 40);

        setToolbar({
          visible: true,
          x,
          y,
          selectedText,
          selection: {
            startLineNumber: selection.startLineNumber,
            startColumn: selection.startColumn,
            endLineNumber: selection.endLineNumber,
            endColumn: selection.endColumn,
          },
        });
      });
    },
    [],
  );

  const handleCitationClick = useCallback(
    (paperId: string) => {
      useResearchStore.getState().setActivePaper(paperId);
      useResearchStore.getState().setPrimaryTab("reader");
    },
    [],
  );

  const handleChange = useCallback(
    (value: string | undefined) => {
      if (value !== undefined) setDocumentContent(value);
    },
    [setDocumentContent],
  );

  const handleAIAction = useCallback(
    (action: AIAction, selectedText: string, range: IRange) => {
      const editor = editorRef.current;
      if (!editor) return;
      setToolbar((t) => ({ ...t, visible: false }));
      executeAI(action, selectedText, range, editor);
    },
    [executeAI],
  );

  // Expose addChange for external agents via custom event
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as Omit<TrackChange, "id" | "status"> | undefined;
      if (detail) addChange(detail);
    };
    window.addEventListener("research:trackChange", handler);
    return () => window.removeEventListener("research:trackChange", handler);
  }, [addChange]);

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-800 bg-[#252526]">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs text-gray-400 shrink-0">Research Document</span>
          <SectionStatusBadges />
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {aiLoading && (
            <div className="flex items-center gap-1 text-[10px] text-blue-400 mr-2">
              <Loader2 size={10} className="animate-spin" />
              AI working…
            </div>
          )}
          <ViewModeToggle mode={viewMode} onChange={setViewMode} />
          <ExportButton />
        </div>
      </div>

      {/* Editor + Preview */}
      <div className="flex-1 min-h-0 flex">
        {(viewMode === "edit" || viewMode === "split") && (
          <div className={viewMode === "split" ? "w-1/2 min-w-0" : "w-full"}>
            <Editor
              language="markdown"
              value={documentContent}
              onChange={handleChange}
              theme={theme}
              onMount={handleEditorMount}
              options={{
                wordWrap: "on",
                lineNumbers: "off",
                minimap: { enabled: false },
                fontSize: 14,
                fontFamily: "'Georgia', 'Crimson Text', serif",
                lineHeight: 1.8,
                padding: { top: 16, bottom: 16 },
                renderLineHighlight: "none",
                occurrencesHighlight: "off" as unknown as undefined,
                scrollBeyondLastLine: false,
                automaticLayout: true,
                quickSuggestions: { other: true, comments: false, strings: true },
                suggestOnTriggerCharacters: true,
                tabSize: 2,
                contextmenu: false,
                unicodeHighlight: { ambiguousCharacters: false },
                cursorBlinking: "smooth",
                cursorStyle: "line",
                smoothScrolling: true,
              }}
            />
          </div>
        )}
        {(viewMode === "preview" || viewMode === "split") && (
          <div
            className={`${
              viewMode === "split" ? "w-1/2 border-l border-gray-800" : "w-full"
            } overflow-y-auto bg-[#1a1a2e] p-8`}
          >
            {documentContent.trim() ? (
              <MarkdownPreview content={documentContent} onCitationClick={handleCitationClick} />
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-gray-600">
                <PenLine size={32} className="mb-3" />
                <p className="text-sm">Start writing to see the preview</p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Track changes bar */}
      <TrackChangesBar
        changes={changes}
        onAccept={acceptChange}
        onReject={rejectChange}
      />

      {/* Floating AI toolbar */}
      {editorRef.current && (
        <FloatingAIToolbar
          state={toolbar}
          editor={editorRef.current}
          onAction={handleAIAction}
          onClose={() => setToolbar((t) => ({ ...t, visible: false }))}
        />
      )}
    </div>
  );
}
