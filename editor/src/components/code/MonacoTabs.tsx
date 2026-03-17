import { useCallback, useEffect, useRef, useState } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor as monacoEditor } from "monaco-editor";
import {
  X,
  FileCode2,
  FileJson,
  FileText,
  FileType,
  AlertTriangle,
  GitCompare,
  Sparkles,
  Loader2,
  TestTube2,
  BookOpen,
  Wrench,
  Zap,
} from "lucide-react";
import { useCodeStore, type OpenFile } from "../../store/useCodeStore";
import { useDebugStore } from "../../store/useDebugStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";
import { nativeFs, nativeDebug } from "../../lib/electronBridge";
import { useGitBlame } from "../../hooks/useGitBlame";
import { getLargeFileConfig } from "../../lib/largeFileMode";
import { useMergeConflictDecorations } from "../../hooks/useMergeConflicts";
import { pushCursorPosition } from "../../hooks/useCursorHistory";
import { useInlineDiffDecorations } from "../../hooks/useInlineDiff";
import InlineEdit from "./InlineEdit";
import ExplainPopup from "./ExplainPopup";
import { sendToChat } from "./ChatSidebar";
import { registerInlineCompletion, onCompletionStatusChange } from "./InlineCompletion";
import {
  explainCode,
  generateTests,
  generateDocs,
  suggestRefactoring,
  deriveTestPath,
  stripFences,
} from "../../lib/aiCodeActions";
import Breadcrumbs from "./Breadcrumbs";
import PeekDefinition, { findDefinitions, findDefinitionsLsp, type PeekDef } from "./PeekDefinition";
import {
  registerSnippetProviders,
  registerColorProvider,
  registerLinkedEditingProvider,
  registerEmmetProvider,
} from "../../lib/snippets";
import { pushLocalHistory } from "../../lib/localHistory";

// ---------------------------------------------------------------------------
// File icon / color mapping
// ---------------------------------------------------------------------------

const LANG_COLORS: Record<string, string> = {
  typescript: "#3178c6",
  javascript: "#f0db4f",
  python: "#3572A5",
  rust: "#dea584",
  go: "#00ADD8",
  json: "#c4a000",
  markdown: "#6b7280",
  html: "#e34c26",
  css: "#264de4",
  scss: "#c6538c",
  yaml: "#cb171e",
  toml: "#9c4121",
  shell: "#4EAA25",
  sql: "#e38c00",
  graphql: "#e10098",
  xml: "#f26522",
  plaintext: "#9ca3af",
};

function langColor(language: string): string {
  return LANG_COLORS[language] ?? "#9ca3af";
}

function LangIcon({ language, size = 14 }: { language: string; size?: number }) {
  const color = langColor(language);

  if (language === "json") return <FileJson size={size} color={color} />;
  if (language === "markdown") return <FileText size={size} color={color} />;
  if (["typescript", "javascript", "python", "rust", "go"].includes(language))
    return <FileCode2 size={size} color={color} />;
  if (["html", "css", "scss", "xml"].includes(language))
    return <FileType size={size} color={color} />;

  return (
    <span
      className="inline-block rounded-full shrink-0"
      style={{ width: size - 2, height: size - 2, backgroundColor: color }}
    />
  );
}

function basename(filePath: string): string {
  const parts = filePath.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || filePath;
}

// ---------------------------------------------------------------------------
// Tab context menu
// ---------------------------------------------------------------------------

interface ContextMenuProps {
  x: number;
  y: number;
  filePath: string;
  onClose: () => void;
}

function TabContextMenu({ x, y, filePath, onClose }: ContextMenuProps) {
  const { openFiles, closeFile, setActiveFile } = useCodeStore();
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const closeOthers = () => {
    openFiles.forEach((f) => {
      if (f.path !== filePath) closeFile(f.path);
    });
    setActiveFile(filePath);
    onClose();
  };

  const closeAll = () => {
    openFiles.forEach((f) => closeFile(f.path));
    onClose();
  };

  const closeToRight = () => {
    const idx = openFiles.findIndex((f) => f.path === filePath);
    openFiles.forEach((f, i) => {
      if (i > idx) closeFile(f.path);
    });
    onClose();
  };

  const copyPath = () => {
    navigator.clipboard.writeText(filePath);
    onClose();
  };

  const items = [
    { label: "Close", action: () => { closeFile(filePath); onClose(); } },
    { label: "Close Others", action: closeOthers },
    { label: "Close All", action: closeAll },
    { label: "Close to the Right", action: closeToRight },
    { label: "separator" },
    { label: "Copy Path", action: copyPath },
  ] as const;

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-44 rounded-md border border-[#3c3c3c] bg-[#252526] py-1 shadow-lg text-xs text-gray-300"
      style={{ left: x, top: y }}
    >
      {items.map((item, i) =>
        item.label === "separator" ? (
          <div key={i} className="my-1 border-t border-[#3c3c3c]" />
        ) : (
          <button
            key={i}
            className="block w-full px-3 py-1.5 text-left hover:bg-[#094771] hover:text-white"
            onClick={item.action}
          >
            {item.label}
          </button>
        ),
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Editor right-click context menu
// ---------------------------------------------------------------------------

interface EditorCtxMenuProps {
  x: number;
  y: number;
  editor: monacoEditor.IStandaloneCodeEditor;
  onClose: () => void;
}

function EditorContextMenu({ x, y, editor, onClose, onExplain, onGenerateTests, onGenerateDocs, onSuggestRefactoring }: EditorCtxMenuProps & {
  onExplain: (code: string, lang: string, pos: { x: number; y: number }) => void;
  onGenerateTests: (code: string, lang: string, filePath: string) => void;
  onGenerateDocs: (code: string, lang: string) => void;
  onSuggestRefactoring: (code: string, lang: string) => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const activeFile = useCodeStore((s) => s.openFiles.find((f) => f.path === s.activeFilePath));

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const selection = editor.getSelection();
  const hasSelection = selection ? !selection.isEmpty() : false;
  const language = activeFile?.language ?? "plaintext";

  const getSelectedCode = (): string => {
    const sel = editor.getSelection();
    if (!sel) return "";
    return editor.getModel()?.getValueInRange(sel) ?? "";
  };

  const sendSelection = (action: string) => {
    const selectedText = getSelectedCode();
    if (!selectedText) return;
    const filePath = activeFilePath ?? "unknown file";
    const block = `\n\`\`\`\n${selectedText}\n\`\`\``;
    if (action === "explain") sendToChat(`Explain this code from ${filePath}:${block}`);
    else if (action === "fix") sendToChat(`Fix this code from ${filePath}:${block}`);
    else if (action === "refactor") sendToChat(`Refactor this code from ${filePath}:${block}`);
  };

  type MenuItem = { label: string; shortcut?: string; icon?: React.ReactNode; action?: () => void; disabled?: boolean };

  const items: MenuItem[] = [
    { label: "Cut", shortcut: "⌘X", action: () => { editor.trigger("menu", "editor.action.clipboardCutAction", null); onClose(); }, disabled: !hasSelection },
    { label: "Copy", shortcut: "⌘C", action: () => { editor.trigger("menu", "editor.action.clipboardCopyAction", null); onClose(); } },
    { label: "Paste", shortcut: "⌘V", action: () => { editor.trigger("menu", "editor.action.clipboardPasteAction", null); onClose(); } },
    { label: "separator" },
    { label: "Find", shortcut: "⌘F", action: () => { editor.trigger("menu", "actions.find", null); onClose(); } },
    { label: "Replace", shortcut: "⌘H", action: () => { editor.trigger("menu", "editor.action.startFindReplaceAction", null); onClose(); } },
    { label: "separator" },
    { label: "Go to Definition", shortcut: "F12", action: () => { editor.trigger("menu", "editor.action.revealDefinition", null); onClose(); } },
    { label: "Peek Definition", shortcut: "⌥F12", action: () => { editor.trigger("menu", "editor.action.peekDefinition", null); onClose(); } },
    { label: "Find All References", shortcut: "⇧F12", action: () => { editor.trigger("menu", "editor.action.referenceSearch.trigger", null); onClose(); } },
    { label: "Show Call Hierarchy", shortcut: "⇧⌥H", action: () => { window.dispatchEvent(new CustomEvent("codemode:showCallHierarchy")); onClose(); } },
    { label: "separator" },
    { label: "Explain This Code", icon: <Sparkles size={12} className="text-purple-400" />, action: () => { onExplain(getSelectedCode(), language, { x, y }); onClose(); }, disabled: !hasSelection },
    { label: "Generate Tests", icon: <TestTube2 size={12} className="text-green-400" />, action: () => { onGenerateTests(getSelectedCode(), language, activeFilePath ?? ""); onClose(); }, disabled: !hasSelection },
    { label: "Generate Docs", icon: <BookOpen size={12} className="text-blue-400" />, action: () => { onGenerateDocs(getSelectedCode(), language); onClose(); }, disabled: !hasSelection },
    { label: "Suggest Refactoring", icon: <Wrench size={12} className="text-amber-400" />, action: () => { onSuggestRefactoring(getSelectedCode(), language); onClose(); }, disabled: !hasSelection },
    { label: "separator" },
    { label: "Ask DAN about this", icon: <Zap size={12} className="text-yellow-400" />, action: () => { sendSelection("explain"); onClose(); }, disabled: !hasSelection },
    { label: "Fix this", action: () => { sendSelection("fix"); onClose(); }, disabled: !hasSelection },
    { label: "separator" },
    { label: "Format Document", shortcut: "⇧⌥F", action: () => { editor.trigger("menu", "editor.action.formatDocument", null); onClose(); } },
    { label: "Change All Occurrences", shortcut: "⌘F2", action: () => { editor.trigger("menu", "editor.action.changeAll", null); onClose(); } },
  ];

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-52 rounded-md border border-[#3c3c3c] bg-[#252526] py-1 shadow-xl text-xs"
      style={{ left: x, top: y }}
    >
      {items.map((item, i) =>
        item.label === "separator" ? (
          <div key={i} className="my-1 border-t border-[#3c3c3c]" />
        ) : (
          <button
            key={i}
            className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-gray-300 hover:bg-[#094771] hover:text-white ${item.disabled ? "opacity-40 pointer-events-none" : ""}`}
            onClick={item.action}
            disabled={item.disabled}
          >
            <span className="flex items-center gap-1.5">
              {item.icon}
              {item.label}
            </span>
            {item.shortcut && <span className="ml-4 text-[10px] text-gray-500">{item.shortcut}</span>}
          </button>
        ),
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single tab
// ---------------------------------------------------------------------------

interface TabItemProps {
  file: OpenFile;
  isActive: boolean;
  onActivate: () => void;
  onClose: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  index: number;
  onDragStart: (index: number) => void;
  onDragOver: (e: React.DragEvent, index: number) => void;
  onDrop: (index: number) => void;
}

function TabItem({ file, isActive, onActivate, onClose, onContextMenu, index, onDragStart, onDragOver, onDrop }: TabItemProps) {
  const [hovered, setHovered] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const handleAuxClick = (e: React.MouseEvent) => {
    if (e.button === 1) {
      e.preventDefault();
      onClose();
    }
  };

  const showClose = hovered || !file.dirty;

  return (
    <div
      role="tab"
      aria-selected={isActive}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        onDragStart(index);
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setDragOver(true);
        onDragOver(e, index);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        onDrop(index);
      }}
      className={`group relative flex items-center gap-1.5 shrink-0 cursor-pointer select-none border-r border-[#252526] px-3 py-1.5 text-xs ${
        isActive
          ? "bg-[#1e1e1e] text-white border-b-2 border-b-blue-500"
          : "bg-[#2d2d2d] text-gray-400 hover:bg-[#353535] border-b-2 border-b-transparent"
      } ${dragOver ? "border-l-2 border-l-blue-500" : ""}`}
      onClick={onActivate}
      onAuxClick={handleAuxClick}
      onContextMenu={onContextMenu}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <LangIcon language={file.language} size={13} />
      <span className="max-w-32 truncate">{basename(file.path)}</span>

      <button
        className={`ml-1 flex h-4 w-4 items-center justify-center rounded-sm hover:bg-[#3c3c3c] ${
          !showClose && file.dirty ? "opacity-0 absolute right-2" : ""
        }`}
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        tabIndex={-1}
      >
        {showClose ? (
          <X size={12} />
        ) : null}
      </button>

      {file.dirty && !hovered && (
        <span
          className="ml-1 inline-block h-2 w-2 rounded-full bg-gray-400"
          title="Unsaved changes"
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Welcome screen
// ---------------------------------------------------------------------------

function WelcomeScreen() {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-6 bg-[#1e1e1e] text-gray-500">
      <h1 className="text-5xl font-bold tracking-tight text-gray-600">DAN</h1>
      <div className="flex flex-col items-center gap-2 text-sm">
        <Shortcut keys={["⌘", "P"]} label="Open File" />
        <Shortcut keys={["⌘", "O"]} label="Open Folder" />
      </div>
      <div className="mt-4 text-xs text-gray-400">No files open</div>
    </div>
  );
}

function Shortcut({ keys, label }: { keys: string[]; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-0.5">
        {keys.map((k) => (
          <kbd
            key={k}
            className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-[#555] bg-[#3c3c3c] px-1 text-[10px] font-medium text-gray-400 shadow-sm"
          >
            {k}
          </kbd>
        ))}
      </div>
      <span className="text-xs text-gray-500">{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function MonacoTabs() {
  const {
    openFiles,
    activeFilePath,
    setActiveFile,
    closeFile,
    reorderFile,
    updateFileContent,
    markFileSaved,
  } = useCodeStore();

  const settings = useSettingsStore();
  const monacoTheme = resolveMonacoTheme(settings.theme);

  const editorRef = useRef<monacoEditor.IStandaloneCodeEditor | null>(null);
  const tabBarRef = useRef<HTMLDivElement>(null);
  const prevDecorationsRef = useRef<string[]>([]);

  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; path: string } | null>(null);
  const [editorCtxMenu, setEditorCtxMenu] = useState<{ x: number; y: number } | null>(null);
  const [inlineEditVisible, setInlineEditVisible] = useState(false);
  const [showInlineDiff, setShowInlineDiff] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [fileDragOver, setFileDragOver] = useState(false);
  const [peekDefs, setPeekDefs] = useState<PeekDef[] | null>(null);
  const [peekPosition, setPeekPosition] = useState<{ top: number; left: number } | null>(null);

  // AI feature state
  const [completionStatus, setCompletionStatus] = useState<"idle" | "loading" | "error">("idle");
  const [explainPopup, setExplainPopup] = useState<{
    explanation: string | null;
    loading: boolean;
    position: { x: number; y: number };
  } | null>(null);
  const [aiActionLoading, setAiActionLoading] = useState<string | null>(null);

  // Gutter breakpoint context menu
  const [gutterCtx, setGutterCtx] = useState<{
    x: number;
    y: number;
    line: number;
  } | null>(null);
  const [condBpInput, setCondBpInput] = useState<{
    line: number;
    x: number;
    y: number;
  } | null>(null);

  const openFile = useCodeStore((s) => s.openFile);

  const activeFile = openFiles.find((f) => f.path === activeFilePath) ?? null;
  const largeFileConfig = activeFile
    ? getLargeFileConfig(activeFile.content.length)
    : null;

  const blame = useGitBlame(largeFileConfig?.disableGitBlame);
  const cursorLine = useCodeStore((s) => s.cursorPosition.lineNumber);
  const [blameTooltip, setBlameTooltip] = useState<{
    author: string;
    email: string;
    hash: string;
    summary: string;
    dateRel: string;
    dateAbs: string;
    x: number;
    y: number;
  } | null>(null);

  useMergeConflictDecorations(editorRef.current, activeFile?.content);

  useInlineDiffDecorations(
    editorRef,
    showInlineDiff,
    activeFile?.originalContent ?? null,
    activeFile?.content ?? null,
  );

  // ---- Listen for editor:insertCode from ChatSidebar ---------------------
  useEffect(() => {
    const handler = (e: Event) => {
      const { code } = (e as CustomEvent).detail;
      const editor = editorRef.current;
      if (!editor) return;
      const selection = editor.getSelection();
      if (selection) {
        editor.executeEdits("ai-insert", [
          { range: selection, text: code },
        ]);
      }
    };
    window.addEventListener("editor:insertCode", handler);
    return () => window.removeEventListener("editor:insertCode", handler);
  }, []);

  // ---- Listen for editor:goToLine from cursor navigation ----------------
  useEffect(() => {
    const handler = (e: Event) => {
      const { lineNumber, column } = (e as CustomEvent).detail;
      const editor = editorRef.current;
      if (!editor) return;
      editor.revealLineInCenter(lineNumber);
      editor.setPosition({ lineNumber, column });
      editor.focus();
    };
    window.addEventListener("editor:goToLine", handler);
    return () => window.removeEventListener("editor:goToLine", handler);
  }, []);

  // ---- Push cursor position on file switch ------------------------------
  useEffect(() => {
    if (activeFilePath) {
      pushCursorPosition(activeFilePath, 1, 1);
    }
  }, [activeFilePath]);

  // ---- Cmd+S save handler ------------------------------------------------
  const saveActiveFile = useCallback(async () => {
    if (!activeFile) return;
    pushLocalHistory(activeFile.path, activeFile.content, "Save");
    const ok = await nativeFs.writeFile(activeFile.path, activeFile.content);
    if (ok) {
      markFileSaved(activeFile.path);
      window.dispatchEvent(
        new CustomEvent("lsp:fileSaved", {
          detail: { filePath: activeFile.path, text: activeFile.content },
        }),
      );
    }
  }, [activeFile, markFileSaved]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        saveActiveFile();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [saveActiveFile]);

  // ---- Monaco onMount callback -------------------------------------------
  const handleEditorMount: OnMount = useCallback((editor, monaco) => {
    editorRef.current = editor;
    editor.focus();

    const pos = editor.getPosition();
    if (pos) {
      useCodeStore.getState().setCursorPosition(pos.lineNumber, pos.column);
    }
    let pushTimer: ReturnType<typeof setTimeout>;
    editor.onDidChangeCursorPosition((e) => {
      useCodeStore.getState().setCursorPosition(e.position.lineNumber, e.position.column);
      clearTimeout(pushTimer);
      pushTimer = setTimeout(() => {
        const afp = useCodeStore.getState().activeFilePath;
        if (afp) pushCursorPosition(afp, e.position.lineNumber, e.position.column);
      }, 500);
    });

    editor.onDidChangeCursorSelection(() => {
      const selection = editor.getSelection();
      if (selection && !selection.isEmpty()) {
        const text = editor.getModel()?.getValueInRange(selection) ?? "";
        const afp = useCodeStore.getState().activeFilePath;
        window.dispatchEvent(
          new CustomEvent("editor:selectionChange", {
            detail: {
              text,
              filePath: afp ?? "",
              startLine: selection.startLineNumber,
              endLine: selection.endLineNumber,
            },
          }),
        );
      } else {
        window.dispatchEvent(
          new CustomEvent("editor:selectionChange", {
            detail: { text: "", filePath: "", startLine: 0, endLine: 0 },
          }),
        );
      }
    });

    editor.addAction({
      id: "inline-edit",
      label: "Inline AI Edit",
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK],
      run: () => setInlineEditVisible(true),
    });

    editor.addAction({
      id: "ask-ai-about-selection",
      label: "Ask DAN about Selection",
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyA],
      precondition: "editorHasSelection",
      run: (ed) => {
        const sel = ed.getSelection();
        if (!sel || sel.isEmpty()) return;
        const text = ed.getModel()?.getValueInRange(sel) ?? "";
        if (!text) return;
        const afp = useCodeStore.getState().activeFilePath ?? "unknown file";
        sendToChat(`Explain this code from ${afp}:\n\`\`\`\n${text}\n\`\`\``);
      },
    });

    editor.addAction({
      id: "peek-definition",
      label: "Peek Definition",
      keybindings: [monaco.KeyMod.Alt | monaco.KeyCode.F12],
      run: async (ed) => {
        const pos = ed.getPosition();
        if (!pos) return;
        const word = ed.getModel()?.getWordAtPosition(pos);
        if (!word) return;
        const state = useCodeStore.getState();

        let defs = await findDefinitionsLsp(
          state.activeFilePath ?? "",
          pos.lineNumber - 1,
          pos.column - 1,
          state.openFiles,
        );

        if (defs.length === 0) {
          defs = findDefinitions(word.word, state.activeFilePath ?? "", state.openFiles);
        }

        if (defs.length > 0) {
          const coords = ed.getScrolledVisiblePosition(pos);
          const domNode = ed.getDomNode();
          if (coords && domNode) {
            const rect = domNode.getBoundingClientRect();
            setPeekPosition({
              top: rect.top + coords.top + 20,
              left: Math.min(rect.left + coords.left, rect.right - 620),
            });
          } else {
            setPeekPosition({ top: 200, left: 200 });
          }
          setPeekDefs(defs);
        }
      },
    });

    registerSnippetProviders(monaco);
    registerColorProvider(monaco);
    registerLinkedEditingProvider(monaco);
    registerEmmetProvider(monaco);

    const debugHoverDisposable = monaco.languages.registerHoverProvider("*", {
      async provideHover(model: monacoEditor.ITextModel, position: { lineNumber: number; column: number }) {
        const debugState = useDebugStore.getState();
        if (debugState.status !== "paused" || debugState.activeFrameId === null) return null;

        const word = model.getWordAtPosition(position);
        if (!word) return null;

        try {
          const result = await nativeDebug.evaluate(word.word, debugState.activeFrameId);
          if (!result?.result) return null;

          const typeStr = result.type ? ` : ${result.type}` : "";
          return {
            range: new monaco.Range(
              position.lineNumber,
              word.startColumn,
              position.lineNumber,
              word.endColumn,
            ),
            contents: [
              { value: `**${word.word}**${typeStr}` },
              { value: `\`\`\`\n${result.result}\n\`\`\`` },
            ],
          };
        } catch {
          return null;
        }
      },
    });

    editor.onMouseDown((e) => {
      const target = e.target;
      if (
        target.type === monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS ||
        target.type === monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN
      ) {
        const line = target.position?.lineNumber;
        if (!line) return;

        if (e.event.rightButton) {
          e.event.preventDefault();
          e.event.stopPropagation();
          setGutterCtx({ x: e.event.posx, y: e.event.posy, line });
          return;
        }

        const afp = useCodeStore.getState().activeFilePath;
        if (afp) {
          useDebugStore.getState().toggleBreakpoint(afp, line);
          const bps = useDebugStore.getState().breakpoints[afp] ?? [];
          nativeDebug.setBreakpoints(afp, bps).catch(() => {});
        }
      }
    });

    editor.onContextMenu((e) => {
      e.event.preventDefault();
      e.event.stopPropagation();
      setEditorCtxMenu({ x: e.event.posx, y: e.event.posy });
    });

    const completionDisposable = registerInlineCompletion();
    const statusDisposable = onCompletionStatusChange(setCompletionStatus);

    return () => {
      completionDisposable.dispose();
      debugHoverDisposable.dispose();
      statusDisposable();
    };
  }, []);

  // ---- Editor onChange ----------------------------------------------------
  const handleChange = useCallback(
    (value: string | undefined) => {
      if (activeFilePath && value !== undefined) {
        updateFileContent(activeFilePath, value);
      }
    },
    [activeFilePath, updateFileContent],
  );

  // ---- Inline git blame decoration (GitLens-style) -----------------------
  useEffect(() => {
    prevDecorationsRef.current = [];
  }, [activeFilePath]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;

    if (blame.length === 0) {
      if (prevDecorationsRef.current.length > 0) {
        prevDecorationsRef.current = editor.deltaDecorations(
          prevDecorationsRef.current,
          [],
        );
      }
      return;
    }

    const blameLine = blame.find((b) => b.lineNumber === cursorLine);
    if (!blameLine || blameLine.hash.startsWith("000000")) {
      prevDecorationsRef.current = editor.deltaDecorations(
        prevDecorationsRef.current,
        [],
      );
      return;
    }

    const text = `  ${blameLine.author}, ${blameLine.date} \u2022 ${blameLine.summary}`;

    prevDecorationsRef.current = editor.deltaDecorations(
      prevDecorationsRef.current,
      [
        {
          range: {
            startLineNumber: cursorLine,
            startColumn: 1,
            endLineNumber: cursorLine,
            endColumn: 1,
          },
          options: {
            after: {
              content: text,
              inlineClassName: "git-blame-inline",
            },
            isWholeLine: true,
          },
        },
      ],
    );
  }, [blame, cursorLine]);

  // ---- Blame tooltip on hover over inline decoration ---------------------
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;

    const domNode = editor.getDomNode();
    if (!domNode) return;

    const onMouseMove = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target?.classList?.contains("git-blame-inline")) {
        setBlameTooltip(null);
        return;
      }
      const blameLine = blame.find((b) => b.lineNumber === cursorLine);
      if (!blameLine || blameLine.hash.startsWith("000000")) {
        setBlameTooltip(null);
        return;
      }
      setBlameTooltip({
        author: blameLine.author,
        email: blameLine.email,
        hash: blameLine.hash.slice(0, 8),
        summary: blameLine.summary,
        dateRel: blameLine.date,
        dateAbs: blameLine.dateAbsolute,
        x: e.clientX,
        y: e.clientY,
      });
    };

    const onMouseLeave = () => setBlameTooltip(null);

    domNode.addEventListener("mousemove", onMouseMove);
    domNode.addEventListener("mouseleave", onMouseLeave);
    return () => {
      domNode.removeEventListener("mousemove", onMouseMove);
      domNode.removeEventListener("mouseleave", onMouseLeave);
    };
  }, [blame, cursorLine]);

  // ---- Horizontal scroll on tab bar via wheel ----------------------------
  useEffect(() => {
    const el = tabBarRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        e.preventDefault();
        el.scrollLeft += e.deltaY;
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // ---- AI action handlers ------------------------------------------------
  const handleExplainCode = useCallback(
    async (code: string, lang: string, pos: { x: number; y: number }) => {
      if (!code.trim()) return;
      setExplainPopup({ explanation: null, loading: true, position: pos });
      try {
        const result = await explainCode(code, lang);
        setExplainPopup({ explanation: result, loading: false, position: pos });
      } catch {
        setExplainPopup({ explanation: "Failed to get explanation.", loading: false, position: pos });
      }
    },
    [],
  );

  const handleGenerateTests = useCallback(
    async (code: string, lang: string, filePath: string) => {
      if (!code.trim()) return;
      setAiActionLoading("tests");
      try {
        const result = await generateTests(code, lang, filePath);
        const testCode = stripFences(result);
        const testPath = deriveTestPath(filePath);
        useCodeStore.getState().openFile(testPath, testCode, lang);
      } catch {
        sendToChat("Failed to generate tests. Please try again.");
      } finally {
        setAiActionLoading(null);
      }
    },
    [],
  );

  const handleGenerateDocs = useCallback(
    async (code: string, lang: string) => {
      if (!code.trim() || !editorRef.current) return;
      setAiActionLoading("docs");
      try {
        const result = await generateDocs(code, lang);
        const documented = stripFences(result);
        const sel = editorRef.current.getSelection();
        if (sel && documented) {
          editorRef.current.executeEdits("ai-docs", [
            { range: sel, text: documented },
          ]);
        }
      } catch {
        sendToChat("Failed to generate docs. Please try again.");
      } finally {
        setAiActionLoading(null);
      }
    },
    [],
  );

  const handleSuggestRefactoring = useCallback(
    async (code: string, lang: string) => {
      if (!code.trim()) return;
      setAiActionLoading("refactor");
      try {
        const suggestions = await suggestRefactoring(code, lang);
        if (suggestions.length > 0) {
          const msg = suggestions
            .map((s, i) => `**${i + 1}. ${s.description}**\n\`\`\`${lang}\n${s.refactored}\n\`\`\``)
            .join("\n\n");
          sendToChat(`Refactoring suggestions:\n\n${msg}`);
        } else {
          sendToChat("No refactoring suggestions found.");
        }
      } catch {
        sendToChat("Failed to get refactoring suggestions.");
      } finally {
        setAiActionLoading(null);
      }
    },
    [],
  );

  // ---- No files open → welcome screen -----------------------------------
  if (openFiles.length === 0) {
    return (
      <div className="flex h-full w-full flex-col">
        <WelcomeScreen />
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col">
      {/* Tab bar */}
      <div
        ref={tabBarRef}
        role="tablist"
        className="flex shrink-0 overflow-x-auto border-b border-[#252526] bg-[#2d2d2d] scrollbar-none"
      >
        {openFiles.map((f, i) => (
          <TabItem
            key={f.path}
            file={f}
            isActive={f.path === activeFilePath}
            onActivate={() => setActiveFile(f.path)}
            onClose={() => closeFile(f.path)}
            onContextMenu={(e) => {
              e.preventDefault();
              setCtxMenu({ x: e.clientX, y: e.clientY, path: f.path });
            }}
            index={i}
            onDragStart={setDragIndex}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(toIndex) => {
              if (dragIndex !== null && dragIndex !== toIndex) {
                reorderFile(dragIndex, toIndex);
              }
              setDragIndex(null);
            }}
          />
        ))}
      </div>

      {/* Breadcrumbs + inline diff toggle */}
      {activeFile && (
        <div className="flex items-center border-b border-[#2d2d2d] bg-[#1e1e1e]">
          <div className="flex-1 min-w-0">
            <Breadcrumbs filePath={activeFile.path} language={activeFile.language} content={activeFile.content} />
          </div>
          <button
            onClick={() => setShowInlineDiff(v => !v)}
            className={`shrink-0 p-1 mr-2 rounded transition-colors ${
              showInlineDiff ? "text-green-400 bg-green-400/10" : "text-gray-500 hover:text-green-300"
            }`}
            title={showInlineDiff ? "Hide inline diff" : "Show inline diff (vs. saved)"}
          >
            <GitCompare size={14} />
          </button>
        </div>
      )}

      {/* Large file warning */}
      {largeFileConfig?.warningMessage && (
        <div className="flex items-center px-3 py-1 bg-yellow-800/20 border-b border-yellow-700/30 text-yellow-300 text-[11px]">
          <AlertTriangle size={12} className="mr-1.5 shrink-0" />
          {largeFileConfig.warningMessage}
        </div>
      )}

      {/* Editor area with drop zone */}
      <div
        className={`relative flex-1 min-h-0 ${fileDragOver ? "ring-2 ring-inset ring-blue-500/60" : ""}`}
        onDragOver={(e) => {
          if (
            e.dataTransfer.types.includes("text/uri-list") ||
            e.dataTransfer.types.includes("Files")
          ) {
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
            setFileDragOver(true);
          }
        }}
        onDragLeave={(e) => {
          if (e.currentTarget === e.target || !e.currentTarget.contains(e.relatedTarget as Node)) {
            setFileDragOver(false);
          }
        }}
        onDrop={async (e) => {
          e.preventDefault();
          setFileDragOver(false);

          if (e.dataTransfer.files.length > 0) {
            for (const file of Array.from(e.dataTransfer.files)) {
              const filePath = (file as unknown as { path: string }).path;
              if (filePath) {
                const content = await nativeFs.readFile(filePath);
                if (content !== null) openFile(filePath, content);
              }
            }
            return;
          }

          const filePath =
            e.dataTransfer.getData("text/uri-list") ||
            e.dataTransfer.getData("text/plain");
          if (filePath) {
            const content = await nativeFs.readFile(filePath);
            if (content !== null) openFile(filePath, content);
          }
        }}
      >
        {fileDragOver && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-blue-500/10 pointer-events-none">
            <div className="rounded-lg border-2 border-dashed border-blue-500/50 px-6 py-4 text-sm text-blue-300">
              Drop to open file
            </div>
          </div>
        )}
        {activeFile ? (
          <Editor
            key={activeFile.path}
            path={activeFile.path}
            language={activeFile.language}
            value={activeFile.content}
            theme={monacoTheme}
            onChange={handleChange}
            onMount={handleEditorMount}
            options={{
              fontSize: settings.fontSize,
              fontFamily: settings.fontFamily,
              minimap: {
                enabled: largeFileConfig?.disableMinimap
                  ? false
                  : settings.minimap,
              },
              lineNumbers: settings.lineNumbers,
              wordWrap: largeFileConfig?.disableWordWrap
                ? "off"
                : settings.wordWrap,
              scrollBeyondLastLine: settings.scrollBeyondLastLine,
              automaticLayout: true,
              tabSize: settings.tabSize,
              renderWhitespace: settings.renderWhitespace,
              bracketPairColorization: {
                enabled: largeFileConfig?.disableBracketPairColorization
                  ? false
                  : settings.bracketPairColorization,
              },
              folding: largeFileConfig?.disableFolding === true ? false : undefined,
              cursorBlinking: settings.cursorBlinking,
              cursorStyle: settings.cursorStyle,
              contextmenu: false,
              padding: { top: 8 },
              stickyScroll: { enabled: settings.stickyScroll },
              guides: {
                indentation: settings.indentGuides,
                bracketPairs: settings.bracketPairGuides,
              },
            }}
          />
        ) : (
          <WelcomeScreen />
        )}
      </div>

      {/* Inline AI edit widget (Cmd+K) */}
      {inlineEditVisible && editorRef.current && (
        <InlineEdit
          editor={editorRef.current}
          onClose={() => setInlineEditVisible(false)}
        />
      )}

      {/* Tab context menu */}
      {ctxMenu && (
        <TabContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          filePath={ctxMenu.path}
          onClose={() => setCtxMenu(null)}
        />
      )}

      {/* Editor right-click context menu */}
      {editorCtxMenu && editorRef.current && (
        <EditorContextMenu
          x={editorCtxMenu.x}
          y={editorCtxMenu.y}
          editor={editorRef.current}
          onClose={() => setEditorCtxMenu(null)}
          onExplain={handleExplainCode}
          onGenerateTests={handleGenerateTests}
          onGenerateDocs={handleGenerateDocs}
          onSuggestRefactoring={handleSuggestRefactoring}
        />
      )}

      {/* Peek definition overlay */}
      {peekDefs && peekDefs.length > 0 && peekPosition && (
        <PeekDefinition
          definitions={peekDefs}
          onClose={() => { setPeekDefs(null); setPeekPosition(null); }}
          onOpenFile={async (uri, line) => {
            const existing = openFiles.find(f => f.path === uri);
            if (existing) {
              setActiveFile(uri);
            } else {
              const content = await nativeFs.readFile(uri);
              if (content !== null) {
                useCodeStore.getState().openFile(uri, content);
              }
            }
            setTimeout(() => {
              window.dispatchEvent(new CustomEvent("editor:goToLine", { detail: { lineNumber: line, column: 1 } }));
            }, 100);
          }}
          style={{ position: "fixed", top: peekPosition.top, left: peekPosition.left }}
        />
      )}

      {/* AI Explain popup */}
      {explainPopup && (
        <ExplainPopup
          explanation={explainPopup.explanation}
          loading={explainPopup.loading}
          position={explainPopup.position}
          onClose={() => setExplainPopup(null)}
        />
      )}

      {/* AI action loading indicator */}
      {aiActionLoading && (
        <div className="fixed bottom-8 right-4 z-50 flex items-center gap-2 bg-[#252526] border border-gray-700 rounded-lg px-3 py-2 shadow-xl text-xs text-gray-300">
          <Loader2 size={12} className="animate-spin text-purple-400" />
          {aiActionLoading === "tests" && "Generating tests…"}
          {aiActionLoading === "docs" && "Generating docs…"}
          {aiActionLoading === "refactor" && "Analyzing refactoring…"}
        </div>
      )}

      {/* Inline completion status */}
      {completionStatus === "loading" && (
        <div className="absolute bottom-1 right-2 z-30 flex items-center gap-1 text-[10px] text-gray-500">
          <Sparkles size={10} className="text-purple-400 animate-pulse" />
        </div>
      )}

      {/* Git blame hover tooltip */}
      {blameTooltip && (
        <div
          className="fixed z-50 rounded-md border border-gray-700 bg-[#252526] px-3 py-2 shadow-xl"
          style={{
            left: Math.min(blameTooltip.x + 8, window.innerWidth - 320),
            top: blameTooltip.y - 100,
            maxWidth: 340,
            pointerEvents: "auto",
          }}
          onMouseLeave={() => setBlameTooltip(null)}
        >
          <div className="mb-1 text-xs font-medium text-gray-200">
            {blameTooltip.author}
            {blameTooltip.email && (
              <span className="ml-1 text-gray-500">&lt;{blameTooltip.email}&gt;</span>
            )}
          </div>
          <div className="mb-1.5 font-mono text-[11px] text-yellow-400">{blameTooltip.hash}</div>
          <div className="mb-1.5 text-xs leading-snug text-gray-300">{blameTooltip.summary}</div>
          <div className="flex items-center gap-2 text-[10px] text-gray-500">
            <span>{blameTooltip.dateRel}</span>
            <span>&middot;</span>
            <span>{blameTooltip.dateAbs}</span>
          </div>
          <div className="mt-2 flex items-center gap-2 border-t border-gray-700 pt-1.5">
            <button
              className="text-[10px] text-blue-400 hover:text-blue-300 hover:underline"
              onClick={() => {
                navigator.clipboard.writeText(blameTooltip.hash);
                setBlameTooltip(null);
              }}
            >
              Copy Hash
            </button>
          </div>
        </div>
      )}

      {/* Gutter right-click context menu for breakpoints */}
      {gutterCtx && (
        <GutterBreakpointMenu
          x={gutterCtx.x}
          y={gutterCtx.y}
          line={gutterCtx.line}
          onClose={() => setGutterCtx(null)}
          onAddConditional={(line, x, y) => {
            setCondBpInput({ line, x, y });
            setGutterCtx(null);
          }}
        />
      )}

      {/* Conditional breakpoint inline input */}
      {condBpInput && (
        <ConditionalBreakpointInput
          line={condBpInput.line}
          x={condBpInput.x}
          y={condBpInput.y}
          onClose={() => setCondBpInput(null)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Gutter breakpoint context menu                                    */
/* ------------------------------------------------------------------ */

function GutterBreakpointMenu({
  x,
  y,
  line,
  onClose,
  onAddConditional,
}: {
  x: number;
  y: number;
  line: number;
  onClose: () => void;
  onAddConditional: (line: number, x: number, y: number) => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const afp = useCodeStore((s) => s.activeFilePath);
  const breakpoints = useDebugStore((s) => s.breakpoints);
  const hasBp = afp ? (breakpoints[afp] ?? []).some((b) => b.line === line) : false;

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-48 rounded-md border border-[#3c3c3c] bg-[#252526] py-1 shadow-lg text-xs text-gray-300"
      style={{ left: x, top: y }}
    >
      {hasBp ? (
        <>
          <button
            className="block w-full px-3 py-1.5 text-left hover:bg-[#094771] hover:text-white"
            onClick={() => {
              if (afp) {
                useDebugStore.getState().removeBreakpoint(afp, line);
                const bps = useDebugStore.getState().breakpoints[afp] ?? [];
                nativeDebug.setBreakpoints(afp, bps).catch(() => {});
              }
              onClose();
            }}
          >
            Remove Breakpoint
          </button>
          <button
            className="block w-full px-3 py-1.5 text-left hover:bg-[#094771] hover:text-white"
            onClick={() => onAddConditional(line, x, y)}
          >
            Edit Condition...
          </button>
        </>
      ) : (
        <>
          <button
            className="block w-full px-3 py-1.5 text-left hover:bg-[#094771] hover:text-white"
            onClick={() => {
              if (afp) {
                useDebugStore.getState().toggleBreakpoint(afp, line);
                const bps = useDebugStore.getState().breakpoints[afp] ?? [];
                nativeDebug.setBreakpoints(afp, bps).catch(() => {});
              }
              onClose();
            }}
          >
            Add Breakpoint
          </button>
          <button
            className="block w-full px-3 py-1.5 text-left hover:bg-[#094771] hover:text-white"
            onClick={() => onAddConditional(line, x, y)}
          >
            Add Conditional Breakpoint...
          </button>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Conditional breakpoint input popup                                */
/* ------------------------------------------------------------------ */

function ConditionalBreakpointInput({
  line,
  x,
  y,
  onClose,
}: {
  line: number;
  x: number;
  y: number;
  onClose: () => void;
}) {
  const [condition, setCondition] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const afp = useCodeStore((s) => s.activeFilePath);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleSubmit = () => {
    if (!afp) return;
    const store = useDebugStore.getState();
    const existing = (store.breakpoints[afp] ?? []).find((b) => b.line === line);
    if (!existing) {
      store.toggleBreakpoint(afp, line);
    }
    store.setBreakpointCondition(afp, line, condition.trim());
    const bps = useDebugStore.getState().breakpoints[afp] ?? [];
    nativeDebug.setBreakpoints(afp, bps).catch(() => {});
    onClose();
  };

  return (
    <div
      className="fixed z-50 flex items-center gap-1 rounded-md border border-[#007acc] bg-[#1e1e1e] px-2 py-1.5 shadow-xl"
      style={{
        left: Math.min(x, window.innerWidth - 320),
        top: y + 4,
        minWidth: 280,
      }}
    >
      <span className="text-[10px] text-yellow-500 shrink-0 font-medium uppercase">
        Condition
      </span>
      <input
        ref={inputRef}
        value={condition}
        onChange={(e) => setCondition(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") handleSubmit();
          if (e.key === "Escape") onClose();
        }}
        placeholder="Break when expression is true"
        className="flex-1 bg-transparent text-[12px] text-gray-200 outline-none placeholder-gray-600 font-mono"
      />
    </div>
  );
}
