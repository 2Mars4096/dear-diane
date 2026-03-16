import { useEffect, useCallback, useRef } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor as monacoEditor } from "monaco-editor";
import { useCodeStore } from "../../store/useCodeStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";

// ---------------------------------------------------------------------------
// Zen mode: fullscreen distraction-free editor
// ---------------------------------------------------------------------------

interface ZenModeProps {
  filePath: string;
  onExit: () => void;
}

export default function ZenMode({ filePath, onExit }: ZenModeProps) {
  const file = useCodeStore((s) => s.openFiles.find((f) => f.path === filePath));
  const updateFileContent = useCodeStore((s) => s.updateFileContent);
  const theme = useSettingsStore((s) => resolveMonacoTheme(s.theme));
  const fontFamily = useSettingsStore((s) => s.fontFamily);
  const editorRef = useRef<monacoEditor.IStandaloneCodeEditor | null>(null);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onExit();
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [onExit]);

  const handleMount: OnMount = useCallback((editor) => {
    editorRef.current = editor;
    editor.focus();

    editor.addAction({
      id: "zen-exit",
      label: "Exit Zen Mode",
      keybindings: [],
      run: () => onExit(),
    });
  }, [onExit]);

  const handleChange = useCallback(
    (value: string | undefined) => {
      if (value !== undefined) {
        updateFileContent(filePath, value);
      }
    },
    [filePath, updateFileContent],
  );

  if (!file) return null;

  return (
    <div className="fixed inset-0 z-50 bg-[#1e1e1e] flex flex-col">
      {/* Subtle top bar (only visible on hover) */}
      <div className="h-0 group relative">
        <div className="absolute top-0 left-0 right-0 h-8 opacity-0 hover:opacity-100 transition-opacity duration-300 flex items-center justify-center bg-gradient-to-b from-black/40 to-transparent z-10">
          <span className="text-[10px] text-gray-500">
            {file.path.split("/").pop()} — Zen Mode (Esc to exit)
          </span>
        </div>
      </div>

      {/* Centered editor with comfortable margins */}
      <div className="flex-1 min-h-0 flex justify-center">
        <div className="w-full max-w-[900px]">
          <Editor
            key={`zen-${filePath}`}
            path={`zen-${filePath}`}
            language={file.language}
            value={file.content}
            theme={theme}
            onChange={handleChange}
            onMount={handleMount}
            options={{
              wordWrap: "on",
              lineNumbers: "off",
              minimap: { enabled: false },
              fontSize: 16,
              fontFamily,
              lineHeight: 2,
              padding: { top: 60, bottom: 60 },
              renderLineHighlight: "none",
              scrollBeyondLastLine: true,
              cursorBlinking: "smooth",
              cursorSmoothCaretAnimation: "on",
              scrollbar: { vertical: "hidden", horizontal: "hidden" },
              overviewRulerLanes: 0,
              hideCursorInOverviewRuler: true,
              overviewRulerBorder: false,
              glyphMargin: false,
              folding: false,
              lineDecorationsWidth: 0,
              lineNumbersMinChars: 0,
              renderWhitespace: "none",
              contextmenu: false,
              automaticLayout: true,
              stickyScroll: { enabled: false },
              guides: { indentation: false, bracketPairs: false },
            }}
          />
        </div>
      </div>
    </div>
  );
}
