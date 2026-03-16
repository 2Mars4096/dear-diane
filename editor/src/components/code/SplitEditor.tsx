import { useCallback, useRef, useEffect, useState } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor as monacoEditor } from "monaco-editor";
import { X, MoreVertical } from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";
import { nativeFs } from "../../lib/electronBridge";

// ---------------------------------------------------------------------------
// Split editor pane — secondary Monaco instance for side-by-side editing
// ---------------------------------------------------------------------------

interface SplitEditorProps {
  filePath: string;
  onClose: () => void;
}

function basename(p: string): string {
  return p.replace(/\\/g, "/").split("/").pop() ?? p;
}

export default function SplitEditor({ filePath, onClose }: SplitEditorProps) {
  const openFiles = useCodeStore((s) => s.openFiles);
  const updateFileContent = useCodeStore((s) => s.updateFileContent);
  const settings = useSettingsStore();
  const monacoTheme = resolveMonacoTheme(settings.theme);
  const editorRef = useRef<monacoEditor.IStandaloneCodeEditor | null>(null);
  const [externalContent, setExternalContent] = useState<string | null>(null);

  const openFile = openFiles.find((f) => f.path === filePath);
  const content = openFile?.content ?? externalContent ?? "";
  const language = openFile?.language ?? "plaintext";

  useEffect(() => {
    if (!openFile) {
      nativeFs.readFile(filePath).then((c) => {
        if (c !== null) setExternalContent(c);
      });
    }
  }, [filePath, openFile]);

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const otherFiles = openFiles.filter((f) => f.path !== filePath);

  const handleMount: OnMount = useCallback((editor) => {
    editorRef.current = editor;
    editor.focus();
  }, []);

  const handleChange = useCallback(
    (value: string | undefined) => {
      if (value !== undefined && openFile) {
        updateFileContent(filePath, value);
      }
    },
    [filePath, openFile, updateFileContent],
  );

  const handleSwitchFile = (newPath: string) => {
    setDropdownOpen(false);
    useCodeStore.getState().setSplitFilePath(newPath);
  };

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      {/* Tab bar for split pane */}
      <div className="flex items-center px-2 py-1 border-b border-gray-800 bg-[#252526] shrink-0">
        <span className="text-xs text-gray-300 truncate max-w-[200px]">
          {basename(filePath)}
        </span>

        {openFile?.dirty && (
          <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-gray-400 shrink-0" />
        )}

        <div className="ml-auto flex items-center gap-0.5">
          {/* File switcher */}
          <div className="relative">
            <button
              onClick={() => setDropdownOpen((v) => !v)}
              className="p-0.5 text-gray-500 hover:text-gray-300 rounded"
            >
              <MoreVertical size={12} />
            </button>
            {dropdownOpen && otherFiles.length > 0 && (
              <div className="absolute right-0 top-full mt-1 z-50 min-w-[180px] rounded border border-[#3c3c3c] bg-[#252526] py-1 shadow-lg">
                <div className="px-2 py-0.5 text-[10px] text-gray-500 uppercase">
                  Open in split
                </div>
                {otherFiles.map((f) => (
                  <button
                    key={f.path}
                    onClick={() => handleSwitchFile(f.path)}
                    className="block w-full px-2 py-1 text-left text-[11px] text-gray-300 hover:bg-[#094771] hover:text-white truncate"
                  >
                    {basename(f.path)}
                  </button>
                ))}
              </div>
            )}
          </div>

          <button
            onClick={onClose}
            className="p-0.5 text-gray-500 hover:text-gray-300 rounded"
          >
            <X size={12} />
          </button>
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 min-h-0">
        <Editor
          key={filePath}
          path={`split-${filePath}`}
          language={language}
          value={content}
          theme={monacoTheme}
          onChange={handleChange}
          onMount={handleMount}
          options={{
            fontSize: settings.fontSize,
            fontFamily: settings.fontFamily,
            minimap: { enabled: false },
            lineNumbers: settings.lineNumbers,
            wordWrap: settings.wordWrap,
            scrollBeyondLastLine: settings.scrollBeyondLastLine,
            automaticLayout: true,
            tabSize: settings.tabSize,
            renderWhitespace: settings.renderWhitespace,
            bracketPairColorization: {
              enabled: settings.bracketPairColorization,
            },
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
      </div>
    </div>
  );
}
