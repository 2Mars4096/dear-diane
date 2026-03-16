import {
  useState,
  useRef,
  useEffect,
  useCallback,
  type KeyboardEvent,
} from "react";
import { Check, X, Loader2, Undo2, History } from "lucide-react";
import type { editor as monacoEditor, IDisposable, IRange } from "monaco-editor";
import { useCodeStore } from "../../store/useCodeStore";
import { startEditorChat, streamEditorChatResponse } from "../../lib/editorChat";
const MAX_HISTORY = 5;

let promptHistory: string[] = [];
try {
  const stored = localStorage.getItem("dan-inline-edit-history");
  if (stored) promptHistory = JSON.parse(stored);
} catch { /* ignore */ }

function saveHistory(prompt: string) {
  promptHistory = [prompt, ...promptHistory.filter((p) => p !== prompt)].slice(0, MAX_HISTORY);
  try {
    localStorage.setItem("dan-inline-edit-history", JSON.stringify(promptHistory));
  } catch { /* ignore */ }
}

function buildEditPrompt(
  instruction: string,
  selectedCode: string,
  language: string,
  fileName: string,
  beforeContext: string,
  afterContext: string,
  functionContext: string,
): string {
  const parts = [
    `You are editing ${language} code in file "${fileName}".`,
    "Return ONLY the replacement code, no explanation, no markdown fences.",
  ];
  if (functionContext) {
    parts.push("", "Enclosing function/class:", "```", functionContext, "```");
  }
  parts.push(
    "", "Context before:", "```", beforeContext, "```",
    "", "Selected code to modify:", "```", selectedCode, "```",
    "", "Context after:", "```", afterContext, "```",
    "", `Instruction: ${instruction}`,
    "", "Return ONLY the replacement code:",
  );
  return parts.join("\n");
}

function stripCodeFences(text: string): string {
  let s = text.trim();
  const fenceStart = /^```[\w]*\n?/;
  const fenceEnd = /\n?```\s*$/;
  if (fenceStart.test(s) && fenceEnd.test(s)) {
    s = s.replace(fenceStart, "").replace(fenceEnd, "");
  }
  return s;
}

interface SelectionInfo {
  text: string;
  range: IRange;
  startLine: number;
  endLine: number;
}

function getSelectionInfo(
  editor: monacoEditor.IStandaloneCodeEditor,
): SelectionInfo | null {
  const model = editor.getModel();
  if (!model) return null;

  const selection = editor.getSelection();
  if (!selection) return null;

  let range: IRange;
  let text: string;

  if (selection.isEmpty()) {
    const line = selection.startLineNumber;
    text = model.getLineContent(line);
    range = {
      startLineNumber: line,
      startColumn: 1,
      endLineNumber: line,
      endColumn: model.getLineMaxColumn(line),
    };
  } else {
    range = {
      startLineNumber: selection.startLineNumber,
      startColumn: selection.startColumn,
      endLineNumber: selection.endLineNumber,
      endColumn: selection.endColumn,
    };
    text = model.getValueInRange(range);
  }

  return {
    text,
    range,
    startLine: range.startLineNumber,
    endLine: range.endLineNumber,
  };
}

function getSurroundingContext(
  editor: monacoEditor.IStandaloneCodeEditor,
  startLine: number,
  endLine: number,
  contextLines = 20,
): { before: string; after: string } {
  const model = editor.getModel();
  if (!model) return { before: "", after: "" };

  const totalLines = model.getLineCount();
  const beforeStart = Math.max(1, startLine - contextLines);
  const afterEnd = Math.min(totalLines, endLine + contextLines);

  const beforeLines: string[] = [];
  for (let i = beforeStart; i < startLine; i++) {
    beforeLines.push(model.getLineContent(i));
  }

  const afterLines: string[] = [];
  for (let i = endLine + 1; i <= afterEnd; i++) {
    afterLines.push(model.getLineContent(i));
  }

  return { before: beforeLines.join("\n"), after: afterLines.join("\n") };
}

function getFunctionContext(
  editor: monacoEditor.IStandaloneCodeEditor,
  startLine: number,
): string {
  const model = editor.getModel();
  if (!model) return "";

  // Walk backwards to find a function/class/method declaration
  const funcPattern = /^\s*(export\s+)?(async\s+)?(function|class|const|let|var|def|fn|pub\s+fn)\s+\w+/;
  const arrowPattern = /^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s+)?\(/;
  const methodPattern = /^\s*(async\s+)?\w+\s*\([^)]*\)\s*\{/;

  for (let line = startLine - 1; line >= Math.max(1, startLine - 50); line--) {
    const content = model.getLineContent(line);
    if (funcPattern.test(content) || arrowPattern.test(content) || methodPattern.test(content)) {
      return content.trim();
    }
  }
  return "";
}

type Phase = "prompt" | "loading" | "preview";

interface Props {
  editor: monacoEditor.IStandaloneCodeEditor;
  onClose: () => void;
}

export default function InlineEdit({ editor, onClose }: Props) {
  const [phase, setPhase] = useState<Phase>("prompt");
  const [prompt, setPrompt] = useState("");
  const [streamedResult, setStreamedResult] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [streamTokens, setStreamTokens] = useState(0);

  const selectionRef = useRef<SelectionInfo | null>(null);
  const originalTextRef = useRef<string>("");
  const decorationsRef = useRef<string[]>([]);
  const widgetRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const editAppliedRef = useRef(false);

  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const openFiles = useCodeStore((s) => s.openFiles);
  const activeFile = openFiles.find((f) => f.path === activeFilePath);
  const language = activeFile?.language ?? "plaintext";
  const fileName = activeFilePath
    ? activeFilePath.split("/").pop() ?? "untitled"
    : "untitled";

  // Capture selection on mount and position widget
  useEffect(() => {
    const info = getSelectionInfo(editor);
    if (!info) {
      onClose();
      return;
    }
    selectionRef.current = info;
    originalTextRef.current = info.text;

    requestAnimationFrame(() => inputRef.current?.focus());
  }, [editor, onClose]);

  // Position the widget at the top of the selection
  useEffect(() => {
    const info = selectionRef.current;
    if (!info || !widgetRef.current) return;

    const updatePosition = () => {
      const pos = editor.getScrolledVisiblePosition({
        lineNumber: info.startLine,
        column: 1,
      });
      const domNode = editor.getDomNode();
      if (!pos || !domNode || !widgetRef.current) return;

      const editorRect = domNode.getBoundingClientRect();
      const widgetHeight = widgetRef.current.offsetHeight || 44;

      let top = editorRect.top + pos.top - widgetHeight - 4;
      let left = editorRect.left + pos.left;

      if (top < editorRect.top) top = editorRect.top + pos.top + pos.height + 4;
      left = Math.max(editorRect.left, Math.min(left, editorRect.right - 480));
      top = Math.max(0, top);

      widgetRef.current.style.top = `${top}px`;
      widgetRef.current.style.left = `${left}px`;
    };

    updatePosition();
    const disposables: IDisposable[] = [
      editor.onDidScrollChange(updatePosition),
      editor.onDidLayoutChange(updatePosition),
    ];

    return () => disposables.forEach((d) => d.dispose());
  }, [editor, phase]);

  // Highlight the selected range while widget is open
  useEffect(() => {
    const info = selectionRef.current;
    if (!info) return;

    if (phase === "prompt") {
      decorationsRef.current = editor.deltaDecorations(
        decorationsRef.current,
        [{
          range: info.range,
          options: {
            className: "inline-edit-selection-highlight",
            isWholeLine: false,
          },
        }],
      );
    } else if (phase === "loading") {
      decorationsRef.current = editor.deltaDecorations(
        decorationsRef.current,
        [{
          range: info.range,
          options: {
            className: "inline-edit-loading-highlight",
            isWholeLine: true,
          },
        }],
      );
    }

    return () => {
      if (phase !== "preview") {
        decorationsRef.current = editor.deltaDecorations(
          decorationsRef.current,
          [],
        );
      }
    };
  }, [editor, phase]);

  // Apply streaming diff decorations during loading
  useEffect(() => {
    if (phase !== "loading" || !streamedResult) return;

    const info = selectionRef.current;
    if (!info) return;

    const originalLines = originalTextRef.current.split("\n");
    const newLines = stripCodeFences(streamedResult).split("\n");

    const decorations: monacoEditor.IModelDeltaDecoration[] = [];
    const maxLines = Math.max(originalLines.length, newLines.length);

    for (let i = 0; i < maxLines; i++) {
      const lineNum = info.startLine + i;
      if (i < newLines.length && i < originalLines.length) {
        if (newLines[i] !== originalLines[i]) {
          decorations.push({
            range: { startLineNumber: lineNum, startColumn: 1, endLineNumber: lineNum, endColumn: 1 },
            options: {
              isWholeLine: true,
              className: "inline-edit-changed-line",
              minimap: { position: 1, color: "#fbbf2455" },
            },
          });
        }
      } else if (i >= originalLines.length) {
        decorations.push({
          range: { startLineNumber: lineNum, startColumn: 1, endLineNumber: lineNum, endColumn: 1 },
          options: {
            isWholeLine: true,
            className: "inline-edit-added-highlight",
            minimap: { position: 1, color: "#22c55e55" },
          },
        });
      }
    }

    decorationsRef.current = editor.deltaDecorations(
      decorationsRef.current,
      decorations,
    );
  }, [streamedResult, phase, editor]);

  // Submit the edit request
  const handleSubmit = useCallback(async () => {
    if (!prompt.trim()) return;
    const info = selectionRef.current;
    if (!info) return;

    saveHistory(prompt.trim());
    setPhase("loading");
    setError(null);
    setStreamedResult("");
    setStreamTokens(0);

    const { before, after } = getSurroundingContext(editor, info.startLine, info.endLine);
    const funcCtx = getFunctionContext(editor, info.startLine);
    const fullPrompt = buildEditPrompt(
      prompt.trim(),
      info.text,
      language,
      fileName,
      before,
      after,
      funcCtx,
    );

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const { response } = await startEditorChat({
        message: fullPrompt,
        mode: "ask",
        scope: "inline-edit",
      });

      let accumulated = "";
      let tokenCount = 0;

      const ws = streamEditorChatResponse(response, {
        onProgress: (content) => {
          if (controller.signal.aborted) return;
          accumulated = content;
          tokenCount += 1;
          setStreamedResult(accumulated);
          setStreamTokens(tokenCount);
        },
        onComplete: (content) => {
          if (controller.signal.aborted) return;
          accumulated = content;
          const cleaned = stripCodeFences(accumulated);
          setStreamedResult(cleaned);
          applyPreview(cleaned);
          abortRef.current = null;
        },
        onError: (message) => {
          if (controller.signal.aborted) return;
          setError(message || "Connection lost");
          setPhase("prompt");
          abortRef.current = null;
        },
        onCloseWithoutTerminalEvent: () => {
          if (controller.signal.aborted) return;
          const cleaned = stripCodeFences(accumulated);
          if (cleaned.trim()) {
            setStreamedResult(cleaned);
            applyPreview(cleaned);
          } else {
            setError("Connection lost");
            setPhase("prompt");
          }
          abortRef.current = null;
        },
      });

      if (ws) {
        controller.signal.addEventListener(
          "abort",
          () => {
            ws.close();
            abortRef.current = null;
          },
          { once: true },
        );
      }
    } catch {
      setError("Failed to connect to server");
      setPhase("prompt");
      abortRef.current = null;
    }
  }, [prompt, editor, language, fileName]);

  // Apply the AI result as a preview edit
  const applyPreview = useCallback(
    (newText: string) => {
      const info = selectionRef.current;
      if (!info || !newText.trim()) {
        setPhase("prompt");
        return;
      }

      const model = editor.getModel();
      if (!model) return;

      editor.executeEdits("inline-edit", [
        { range: info.range, text: newText },
      ]);
      editAppliedRef.current = true;

      // Highlight with line-level diff decorations
      const originalLines = originalTextRef.current.split("\n");
      const newLines = newText.split("\n");
      const decorations: monacoEditor.IModelDeltaDecoration[] = [];

      for (let i = 0; i < newLines.length; i++) {
        const lineNum = info.range.startLineNumber + i;
        if (i >= originalLines.length || newLines[i] !== originalLines[i]) {
          decorations.push({
            range: {
              startLineNumber: lineNum,
              startColumn: 1,
              endLineNumber: lineNum,
              endColumn: 1,
            },
            options: {
              isWholeLine: true,
              className: "inline-edit-added-highlight",
              glyphMarginClassName: "inline-edit-glyph-added",
            },
          });
        }
      }

      decorationsRef.current = editor.deltaDecorations(
        decorationsRef.current,
        decorations,
      );

      setPhase("preview");
    },
    [editor],
  );

  // Accept: keep the edit, clean up
  const handleAccept = useCallback(() => {
    decorationsRef.current = editor.deltaDecorations(decorationsRef.current, []);
    editAppliedRef.current = false;

    const model = editor.getModel();
    if (model && activeFilePath) {
      useCodeStore.getState().updateFileContent(activeFilePath, model.getValue());
    }

    onClose();
  }, [editor, activeFilePath, onClose]);

  // Reject: undo the edit, clean up
  const handleReject = useCallback(() => {
    decorationsRef.current = editor.deltaDecorations(decorationsRef.current, []);
    if (editAppliedRef.current) {
      editor.trigger("inline-edit", "undo", null);
      editAppliedRef.current = false;
    }
    onClose();
  }, [editor, onClose]);

  // Escape key and Cmd+Enter accept
  useEffect(() => {
    const handleKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        if (phase === "preview") {
          handleReject();
        } else {
          if (abortRef.current) abortRef.current.abort();
          decorationsRef.current = editor.deltaDecorations(decorationsRef.current, []);
          onClose();
        }
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && phase === "preview") {
        e.preventDefault();
        e.stopPropagation();
        handleAccept();
      }
    };
    window.addEventListener("keydown", handleKey, true);
    return () => window.removeEventListener("keydown", handleKey, true);
  }, [phase, editor, onClose, handleReject, handleAccept]);

  // Cleanup decorations on unmount
  useEffect(() => {
    return () => {
      // eslint-disable-next-line react-hooks/exhaustive-deps
      editor.deltaDecorations(decorationsRef.current, []);
    };
  }, [editor]);

  const handleInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
    if (e.key === "ArrowUp" && promptHistory.length > 0) {
      e.preventDefault();
      const nextIdx = Math.min(historyIndex + 1, promptHistory.length - 1);
      setHistoryIndex(nextIdx);
      setPrompt(promptHistory[nextIdx]);
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (historyIndex <= 0) {
        setHistoryIndex(-1);
        setPrompt("");
      } else {
        const nextIdx = historyIndex - 1;
        setHistoryIndex(nextIdx);
        setPrompt(promptHistory[nextIdx]);
      }
    }
    e.stopPropagation();
  };

  const isMultiLine = selectionRef.current
    ? selectionRef.current.endLine - selectionRef.current.startLine > 0
    : false;

  return (
    <div
      ref={widgetRef}
      className="fixed z-50 inline-edit-widget"
      style={{ minWidth: 460 }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {phase === "prompt" && (
        <div className="bg-[#1e1e1e] border border-[#3c3c3c] rounded-lg shadow-2xl p-2 space-y-1.5">
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 text-[10px] text-gray-500 shrink-0 px-1">
              <kbd className="inline-flex h-4 min-w-4 items-center justify-center rounded border border-gray-600 bg-gray-800 px-1 text-[9px] font-medium text-gray-400">
                ⌘K
              </kbd>
            </div>
            <input
              ref={inputRef}
              value={prompt}
              onChange={(e) => {
                setPrompt(e.target.value);
                setHistoryIndex(-1);
              }}
              onKeyDown={handleInputKeyDown}
              placeholder="Describe the edit... (Enter to submit, ↑↓ history)"
              className="flex-1 bg-[#2d2d2d] text-white text-sm px-3 py-1.5 rounded border border-[#4c4c4c] focus:border-blue-500 outline-none placeholder:text-gray-500"
              autoFocus
            />
            {promptHistory.length > 0 && (
              <button
                onClick={() => {
                  const nextIdx = historyIndex < 0 ? 0 : (historyIndex + 1) % promptHistory.length;
                  setHistoryIndex(nextIdx);
                  setPrompt(promptHistory[nextIdx]);
                }}
                className="p-1 text-gray-500 hover:text-gray-300 rounded"
                title="Prompt history"
              >
                <History size={12} />
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 px-1">
            {isMultiLine && (
              <span className="text-[9px] text-blue-400/70">
                {selectionRef.current!.endLine - selectionRef.current!.startLine + 1} lines selected
              </span>
            )}
            <span className="text-[9px] text-gray-600 ml-auto">
              {fileName} • {language}
            </span>
            {error && (
              <span className="text-red-400 text-[10px] truncate max-w-32">
                {error}
              </span>
            )}
          </div>
        </div>
      )}

      {phase === "loading" && (
        <div className="bg-[#1e1e1e] border border-blue-500/40 rounded-lg shadow-2xl p-2 inline-edit-loading-border">
          <div className="flex items-center gap-2">
            <Loader2 size={14} className="animate-spin text-blue-400 shrink-0" />
            <span className="text-gray-400 text-sm flex-1">
              Generating...
              {streamTokens > 0 && (
                <span className="text-gray-600 ml-1 text-[10px]">
                  ({streamTokens} tokens)
                </span>
              )}
            </span>
            <button
              onClick={() => {
                abortRef.current?.abort();
                setPhase("prompt");
              }}
              className="text-gray-500 hover:text-gray-300 p-1 rounded transition-colors"
              title="Cancel"
            >
              <X size={14} />
            </button>
          </div>
          {streamedResult && (
            <pre className="mt-1.5 px-1 text-[10px] text-gray-500 max-h-[60px] overflow-y-auto font-mono whitespace-pre-wrap">
              {stripCodeFences(streamedResult).slice(-200)}
            </pre>
          )}
        </div>
      )}

      {phase === "preview" && (
        <div className="bg-[#1e1e1e] border border-green-500/30 rounded-lg shadow-2xl p-2">
          <div className="flex items-center gap-2">
            <span className="text-gray-400 text-xs px-1 shrink-0">
              Review changes
            </span>
            <span className="text-[9px] text-gray-600">
              ⌘↵ accept • Esc reject
            </span>
            <div className="flex items-center gap-1.5 ml-auto">
              <button
                onClick={handleAccept}
                className="inline-flex items-center gap-1 bg-green-600 hover:bg-green-500 text-white px-3 py-1 rounded text-xs font-medium transition-colors"
                title="Accept (⌘+Enter)"
              >
                <Check size={12} />
                Accept
              </button>
              <button
                onClick={handleReject}
                className="inline-flex items-center gap-1 bg-gray-700 hover:bg-gray-600 text-white px-3 py-1 rounded text-xs font-medium transition-colors"
                title="Reject (Escape)"
              >
                <Undo2 size={12} />
                Reject
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
