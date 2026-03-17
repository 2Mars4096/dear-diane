/**
 * Multi-file AI edit review panel.
 * Shows a list of edited files with per-file diff view and accept/reject.
 */
import { useCallback, useMemo, useState } from "react";
import { DiffEditor } from "@monaco-editor/react";
import { Check, X, Sparkles, ChevronRight } from "lucide-react";
import { useSettingsStore } from "../../store/useSettingsStore";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";

export interface FileEdit {
  filePath: string;
  original: string;
  modified: string;
  accepted: boolean;
}

const EXT_LANG: Record<string, string> = {
  ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
  py: "python", rs: "rust", go: "go", json: "json", md: "markdown",
  html: "html", css: "css", scss: "scss", yaml: "yaml", yml: "yaml",
  toml: "toml", sh: "shell", sql: "sql", xml: "xml", txt: "plaintext",
};

function detectLanguage(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() ?? "";
  return EXT_LANG[ext] ?? "plaintext";
}

function basename(p: string): string {
  return p.split("/").filter(Boolean).pop() ?? p;
}

interface MultiFileEditProps {
  edits: FileEdit[];
  onAccept: (filePath: string) => void;
  onReject: (filePath: string) => void;
  onAcceptAll: () => void;
  onClose: () => void;
  onApplyAllAndTest?: () => void;
}

export default function MultiFileEdit({
  edits,
  onAccept,
  onReject,
  onAcceptAll,
  onClose,
  onApplyAllAndTest,
}: MultiFileEditProps) {
  const [activeFile, setActiveFile] = useState(edits[0]?.filePath ?? "");
  const theme = useSettingsStore((s) => resolveMonacoTheme(s.theme));

  const activeEdit = useMemo(
    () => edits.find((e) => e.filePath === activeFile),
    [edits, activeFile],
  );

  const pendingCount = edits.filter((e) => !e.accepted).length;

  const handleAcceptCurrent = useCallback(() => {
    onAccept(activeFile);
    const nextPending = edits.find((e) => e.filePath !== activeFile && !e.accepted);
    if (nextPending) setActiveFile(nextPending.filePath);
  }, [activeFile, edits, onAccept]);

  const handleRejectCurrent = useCallback(() => {
    onReject(activeFile);
    const nextPending = edits.find((e) => e.filePath !== activeFile && !e.accepted);
    if (nextPending) setActiveFile(nextPending.filePath);
  }, [activeFile, edits, onReject]);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-800 bg-[#252526] shrink-0">
        <span className="text-xs text-purple-400 flex items-center gap-1.5">
          <Sparkles size={14} />
          Multi-File Edit
          <span className="text-gray-500 text-[10px]">
            ({pendingCount} pending of {edits.length})
          </span>
        </span>
        <div className="flex gap-1.5">
          {onApplyAllAndTest && (
            <button
              onClick={onApplyAllAndTest}
              disabled={pendingCount === 0}
              className="px-2 py-0.5 text-[10px] bg-blue-800/30 text-blue-400 rounded hover:bg-blue-800/50 disabled:opacity-40 transition-colors"
            >
              Apply &amp; Test
            </button>
          )}
          <button
            onClick={onAcceptAll}
            disabled={pendingCount === 0}
            className="px-2 py-0.5 text-[10px] bg-green-800/30 text-green-400 rounded hover:bg-green-800/50 disabled:opacity-40 transition-colors"
          >
            Accept All
          </button>
          <button
            onClick={onClose}
            className="px-2 py-0.5 text-[10px] bg-gray-800 text-gray-400 rounded hover:bg-gray-700 transition-colors"
          >
            Dismiss
          </button>
        </div>
      </div>

      {/* File tabs */}
      <div className="flex border-b border-gray-800 overflow-x-auto shrink-0 scrollbar-none bg-[#2d2d2d]">
        {edits.map((edit) => (
          <button
            key={edit.filePath}
            onClick={() => setActiveFile(edit.filePath)}
            className={`px-3 py-1.5 text-[11px] flex items-center gap-1.5 border-b-2 shrink-0 transition-colors ${
              activeFile === edit.filePath
                ? "border-blue-500 text-gray-200 bg-[#1e1e1e]"
                : "border-transparent text-gray-500 hover:text-gray-400"
            } ${edit.accepted ? "opacity-50" : ""}`}
          >
            <ChevronRight size={10} className="text-gray-600" />
            {basename(edit.filePath)}
            {edit.accepted && (
              <Check size={10} className="text-green-500" />
            )}
          </button>
        ))}
      </div>

      {/* Diff view */}
      <div className="flex-1 min-h-0">
        {activeEdit ? (
          <DiffEditor
            original={activeEdit.original}
            modified={activeEdit.modified}
            language={detectLanguage(activeFile)}
            theme={theme}
            options={{
              readOnly: true,
              originalEditable: false,
              renderSideBySide: true,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              automaticLayout: true,
              fontSize: 13,
              fontFamily: "SF Mono, Menlo, Monaco, monospace",
              padding: { top: 8 },
            }}
          />
        ) : (
          <div className="flex items-center justify-center h-full text-gray-500 text-xs">
            Select a file to view changes
          </div>
        )}
      </div>

      {/* Per-file accept/reject */}
      {activeEdit && !activeEdit.accepted && (
        <div className="flex items-center justify-between px-3 py-1.5 border-t border-gray-800 bg-[#252526] shrink-0">
          <span className="text-[10px] text-gray-500 truncate max-w-[50%]">
            {activeFile}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={handleAcceptCurrent}
              className="px-3 py-1 text-xs bg-green-700/30 text-green-400 rounded hover:bg-green-700/50 transition-colors flex items-center gap-1"
            >
              <Check size={12} />
              Accept
            </button>
            <button
              onClick={handleRejectCurrent}
              className="px-3 py-1 text-xs bg-red-700/30 text-red-400 rounded hover:bg-red-700/50 transition-colors flex items-center gap-1"
            >
              <X size={12} />
              Reject
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
