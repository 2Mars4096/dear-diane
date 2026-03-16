import { useState, useEffect, useRef, useCallback } from "react";
import Editor from "@monaco-editor/react";
import { X, ChevronLeft, ChevronRight } from "lucide-react";
import { useSettingsStore } from "../../store/useSettingsStore";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";
import { detectLanguageFromPath } from "../../lib/snippets";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PeekDef {
  uri: string;
  range: { startLineNumber: number; endLineNumber: number };
  preview: string;
}

interface PeekDefinitionProps {
  definitions: PeekDef[];
  onClose: () => void;
  onOpenFile: (uri: string, line: number) => void;
  style?: React.CSSProperties;
}

// ---------------------------------------------------------------------------
// Peek definition overlay
// ---------------------------------------------------------------------------

export default function PeekDefinition({
  definitions,
  onClose,
  onOpenFile,
  style,
}: PeekDefinitionProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const theme = useSettingsStore((s) => resolveMonacoTheme(s.theme));
  const activeDef = definitions[activeIndex];

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    },
    [onClose],
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [handleKeyDown]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  if (definitions.length === 0) return null;

  const filename = activeDef?.uri.split("/").pop() ?? "";

  return (
    <div
      ref={containerRef}
      className="absolute z-40 bg-[#252526] border border-blue-500/50 rounded shadow-2xl overflow-hidden"
      style={{ width: 600, maxHeight: 300, ...style }}
    >
      {/* Header */}
      <div className="flex items-center px-2 py-1 border-b border-gray-700 bg-[#1e1e1e] gap-1">
        <span className="text-[10px] text-gray-400 mr-1">
          {definitions.length} definition{definitions.length > 1 ? "s" : ""}
        </span>

        {definitions.length > 1 && (
          <>
            <button
              onClick={() =>
                setActiveIndex((i) =>
                  i > 0 ? i - 1 : definitions.length - 1,
                )
              }
              className="p-0.5 text-gray-500 hover:text-gray-300"
            >
              <ChevronLeft size={12} />
            </button>
            <button
              onClick={() =>
                setActiveIndex((i) =>
                  i < definitions.length - 1 ? i + 1 : 0,
                )
              }
              className="p-0.5 text-gray-500 hover:text-gray-300"
            >
              <ChevronRight size={12} />
            </button>
          </>
        )}

        {definitions.map((def, i) => {
          const name = def.uri.split("/").pop() ?? "";
          return (
            <button
              key={i}
              onClick={() => setActiveIndex(i)}
              className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                i === activeIndex
                  ? "bg-blue-600/30 text-blue-300"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              {name}:{def.range.startLineNumber}
            </button>
          );
        })}

        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => {
              if (activeDef) {
                onOpenFile(activeDef.uri, activeDef.range.startLineNumber);
                onClose();
              }
            }}
            className="px-1.5 py-0.5 text-[10px] text-gray-400 hover:text-white hover:bg-blue-600/30 rounded"
          >
            Open
          </button>
          <button
            onClick={onClose}
            className="p-0.5 text-gray-500 hover:text-gray-300"
          >
            <X size={12} />
          </button>
        </div>
      </div>

      {/* Preview editor */}
      <div className="h-[250px]">
        {activeDef && (
          <Editor
            key={`${activeDef.uri}-${activeIndex}`}
            value={activeDef.preview}
            language={detectLanguageFromPath(activeDef.uri)}
            theme={theme}
            options={{
              readOnly: true,
              minimap: { enabled: false },
              lineNumbers: "on",
              scrollBeyondLastLine: false,
              renderLineHighlight: "line",
              fontSize: 12,
              lineDecorationsWidth: 0,
              overviewRulerLanes: 0,
              scrollbar: { verticalScrollbarSize: 6 },
              contextmenu: false,
              domReadOnly: true,
              padding: { top: 4 },
            }}
          />
        )}
      </div>

      {/* Footer with file path */}
      <div className="px-2 py-0.5 border-t border-gray-700 bg-[#1e1e1e] text-[10px] text-gray-500 truncate">
        {filename} — line {activeDef?.range.startLineNumber}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Search open files for definitions (simple heuristic without LSP)
// ---------------------------------------------------------------------------

interface OpenFileData {
  path: string;
  content: string;
  language: string;
}

export function findDefinitions(
  word: string,
  currentFile: string,
  openFiles: OpenFileData[],
): PeekDef[] {
  const defs: PeekDef[] = [];
  const defPatterns = [
    new RegExp(`(?:function|const|let|var|class|interface|type|enum)\\s+${word}\\b`),
    new RegExp(`(?:def|class)\\s+${word}\\b`),
    new RegExp(`(?:fn|struct|enum|trait|impl|mod)\\s+${word}\\b`),
    new RegExp(`(?:func)\\s+(?:\\([^)]*\\)\\s+)?${word}\\b`),
    new RegExp(`^\\s*${word}\\s*[:=]`, "m"),
  ];

  for (const file of openFiles) {
    const lines = file.content.split("\n");
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const isDefLine = defPatterns.some((p) => p.test(line));
      if (!isDefLine) continue;

      const startLine = Math.max(0, i - 2);
      const endLine = Math.min(lines.length - 1, i + 20);
      const preview = lines.slice(startLine, endLine + 1).join("\n");

      defs.push({
        uri: file.path,
        range: { startLineNumber: i + 1, endLineNumber: endLine + 1 },
        preview,
      });
    }
  }

  if (defs.length > 1) {
    defs.sort((a, b) => {
      if (a.uri === currentFile && b.uri !== currentFile) return 1;
      if (b.uri === currentFile && a.uri !== currentFile) return -1;
      return 0;
    });
  }

  return defs;
}
