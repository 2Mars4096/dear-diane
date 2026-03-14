import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DiffEditor } from "@monaco-editor/react";
import type { editor as monacoEditor } from "monaco-editor";
import { X, ChevronUp, ChevronDown, ArrowLeftRight, Check } from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import { nativeFs } from "../../lib/electronBridge";

const EXT_LANG: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  rs: "rust",
  go: "go",
  json: "json",
  md: "markdown",
  html: "html",
  css: "css",
  scss: "scss",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  sh: "shell",
  bash: "shell",
  sql: "sql",
  graphql: "graphql",
  xml: "xml",
  svg: "xml",
  txt: "plaintext",
};

function detectLang(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() ?? "";
  return EXT_LANG[ext] ?? "plaintext";
}

function basename(filePath: string): string {
  const parts = filePath.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || filePath;
}

function computeStats(changes: monacoEditor.ILineChange[]): {
  additions: number;
  deletions: number;
} {
  let additions = 0;
  let deletions = 0;
  for (const c of changes) {
    if (c.originalEndLineNumber === 0) {
      additions += c.modifiedEndLineNumber - c.modifiedStartLineNumber + 1;
    } else if (c.modifiedEndLineNumber === 0) {
      deletions += c.originalEndLineNumber - c.originalStartLineNumber + 1;
    } else {
      deletions += c.originalEndLineNumber - c.originalStartLineNumber + 1;
      additions += c.modifiedEndLineNumber - c.modifiedStartLineNumber + 1;
    }
  }
  return { additions, deletions };
}

function stripAISuffix(path: string): string {
  return path.replace(" (AI suggestion)", "");
}

// ---------------------------------------------------------------------------
// DiffView component
// ---------------------------------------------------------------------------

export default function DiffView() {
  const diffFile = useCodeStore((s) => s.diffFile);
  const closeDiff = useCodeStore((s) => s.closeDiff);
  const theme = useSettingsStore((s) => s.theme);

  const diffEditorRef = useRef<monacoEditor.IStandaloneDiffEditor | null>(null);
  const [renderSideBySide, setRenderSideBySide] = useState(true);
  const [changes, setChanges] = useState<monacoEditor.ILineChange[]>([]);
  const [currentChangeIdx, setCurrentChangeIdx] = useState(0);
  const [editedModified, setEditedModified] = useState<string | null>(null);

  const language = useMemo(
    () => detectLang(diffFile?.modifiedPath ?? diffFile?.originalPath ?? ""),
    [diffFile],
  );

  const stats = useMemo(() => computeStats(changes), [changes]);

  // Reset edited state when diffFile changes
  useEffect(() => {
    setEditedModified(null);
  }, [diffFile]);

  const handleMount = useCallback(
    (editor: monacoEditor.IStandaloneDiffEditor) => {
      diffEditorRef.current = editor;

      const syncChanges = () => {
        const lc = editor.getLineChanges();
        if (lc) {
          setChanges(lc);
          setCurrentChangeIdx(0);
        }
      };

      editor.onDidUpdateDiff(syncChanges);
      syncChanges();

      editor.getModifiedEditor().onDidChangeModelContent(() => {
        setEditedModified(editor.getModifiedEditor().getValue());
      });
    },
    [],
  );

  const goToChange = useCallback(
    (idx: number) => {
      const editor = diffEditorRef.current;
      if (!editor || changes.length === 0) return;
      const clamped = Math.max(0, Math.min(idx, changes.length - 1));
      setCurrentChangeIdx(clamped);
      const change = changes[clamped];
      const line =
        change.modifiedStartLineNumber || change.originalStartLineNumber;
      editor.getModifiedEditor().revealLineInCenter(line);
    },
    [changes],
  );

  const goNext = useCallback(() => {
    goToChange(
      currentChangeIdx + 1 >= changes.length ? 0 : currentChangeIdx + 1,
    );
  }, [currentChangeIdx, changes.length, goToChange]);

  const goPrev = useCallback(() => {
    goToChange(
      currentChangeIdx - 1 < 0 ? changes.length - 1 : currentChangeIdx - 1,
    );
  }, [currentChangeIdx, changes.length, goToChange]);

  const handleAcceptAll = useCallback(async () => {
    if (!diffFile) return;
    const content = editedModified ?? diffFile.modified;
    const realPath = stripAISuffix(diffFile.modifiedPath);

    await nativeFs.writeFile(realPath, content);

    const { openFiles, updateFileContent, markFileSaved } =
      useCodeStore.getState();
    if (openFiles.find((f) => f.path === realPath)) {
      updateFileContent(realPath, content);
      markFileSaved(realPath);
    }

    closeDiff();
  }, [diffFile, editedModified, closeDiff]);

  const handleRejectAll = useCallback(() => {
    closeDiff();
  }, [closeDiff]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeDiff();
      } else if (e.altKey && e.key === "ArrowDown") {
        e.preventDefault();
        goNext();
      } else if (e.altKey && e.key === "ArrowUp") {
        e.preventDefault();
        goPrev();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [closeDiff, goNext, goPrev]);

  if (!diffFile) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-[#1e1e1e] text-gray-500 text-sm">
        No diff to display
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 shrink-0 border-b border-[#3c3c3c] bg-[#252526] px-3 py-1.5">
        <div className="flex items-center gap-2 min-w-0 text-xs">
          <ArrowLeftRight size={13} className="text-gray-500 shrink-0" />
          <span
            className="truncate text-gray-300"
            title={diffFile.originalPath}
          >
            {basename(diffFile.originalPath)}
          </span>
          <span className="text-gray-500">↔</span>
          <span
            className="truncate text-gray-300"
            title={diffFile.modifiedPath}
          >
            {basename(diffFile.modifiedPath)}
          </span>

          {changes.length > 0 && (
            <span className="flex items-center gap-1 rounded-full bg-[#3c3c3c] px-2 py-0.5 text-[10px] font-medium text-gray-400">
              <span className="text-green-400">+{stats.additions}</span>
              <span className="text-red-400">-{stats.deletions}</span>
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <button
            className="flex items-center gap-1 px-2 py-0.5 text-[11px] rounded bg-green-600 hover:bg-green-500 text-white"
            onClick={handleAcceptAll}
            title="Accept all changes"
          >
            <Check size={12} />
            Accept All
          </button>
          <button
            className="flex items-center gap-1 px-2 py-0.5 text-[11px] rounded bg-gray-600 hover:bg-gray-500 text-white"
            onClick={handleRejectAll}
            title="Reject all changes"
          >
            <X size={12} />
            Reject All
          </button>

          <div className="mx-1 h-4 border-l border-[#555]" />

          <button
            className={`px-2 py-0.5 text-[11px] ${renderSideBySide ? "text-white underline underline-offset-2" : "text-gray-500 hover:text-gray-300"}`}
            onClick={() => {
              setRenderSideBySide(true);
              diffEditorRef.current?.updateOptions({ renderSideBySide: true });
            }}
          >
            Side by Side
          </button>
          <button
            className={`px-2 py-0.5 text-[11px] ${!renderSideBySide ? "text-white underline underline-offset-2" : "text-gray-500 hover:text-gray-300"}`}
            onClick={() => {
              setRenderSideBySide(false);
              diffEditorRef.current?.updateOptions({ renderSideBySide: false });
            }}
          >
            Inline
          </button>

          <div className="mx-1 h-4 border-l border-[#555]" />

          <button
            className="flex items-center justify-center rounded p-1 hover:bg-[#3c3c3c] text-gray-500 hover:text-gray-300"
            onClick={closeDiff}
            title="Close diff (Esc)"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Change navigation */}
      {changes.length > 0 && (
        <div className="flex items-center gap-2 shrink-0 border-b border-[#3c3c3c] bg-[#1e1e1e] px-3 py-1">
          <button
            className="flex items-center justify-center rounded p-0.5 text-gray-500 hover:text-gray-300 hover:bg-[#3c3c3c]"
            onClick={goPrev}
            title="Previous change (Alt+↑)"
          >
            <ChevronUp size={14} />
          </button>
          <button
            className="flex items-center justify-center rounded p-0.5 text-gray-500 hover:text-gray-300 hover:bg-[#3c3c3c]"
            onClick={goNext}
            title="Next change (Alt+↓)"
          >
            <ChevronDown size={14} />
          </button>
          <span className="text-[11px] text-gray-500">
            Change {currentChangeIdx + 1} of {changes.length}
          </span>
        </div>
      )}

      {/* Diff editor */}
      <div className="relative flex-1 min-h-0">
        <DiffEditor
          original={diffFile.original}
          modified={diffFile.modified}
          language={language}
          theme={theme}
          onMount={handleMount}
          options={{
            fontSize: 13,
            fontFamily: "SF Mono, Menlo, Monaco, monospace",
            minimap: { enabled: false },
            readOnly: false,
            originalEditable: false,
            renderSideBySide,
            scrollBeyondLastLine: false,
            automaticLayout: true,
            renderIndicators: true,
            padding: { top: 8 },
          }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper: open a diff between original content and current file on disk
// ---------------------------------------------------------------------------

export async function openFileDiff(
  filePath: string,
  originalContent: string,
): Promise<void> {
  const current = await nativeFs.readFile(filePath);
  if (current === null) return;
  useCodeStore
    .getState()
    .openDiff(originalContent, current, `${filePath} (original)`, filePath);
}
