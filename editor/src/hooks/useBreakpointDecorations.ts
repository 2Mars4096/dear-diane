import { useEffect, useRef } from "react";
import { useDebugStore } from "../store/useDebugStore";
import { useCodeStore } from "../store/useCodeStore";
import { nativeDebug } from "../lib/electronBridge";
import type * as Monaco from "monaco-editor";

const BP_STYLE_ID = "dan-breakpoint-styles";

function ensureStyles() {
  if (document.getElementById(BP_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = BP_STYLE_ID;
  style.textContent = `
    .dan-bp-normal {
      background: #e51400;
      border-radius: 50%;
      width: 10px !important;
      height: 10px !important;
      margin-left: 4px;
      margin-top: 3px;
      cursor: pointer;
    }
    .dan-bp-conditional {
      background: #f0c000;
      border-radius: 50%;
      width: 10px !important;
      height: 10px !important;
      margin-left: 4px;
      margin-top: 3px;
      cursor: pointer;
    }
    .dan-bp-logpoint {
      background: #007acc;
      width: 10px !important;
      height: 10px !important;
      margin-left: 4px;
      margin-top: 3px;
      clip-path: polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%);
      cursor: pointer;
    }
    .dan-paused-line {
      background: rgba(255, 255, 0, 0.15) !important;
    }
    .dan-paused-glyph {
      background: #ffcc00;
      clip-path: polygon(0% 0%, 60% 0%, 100% 50%, 60% 100%, 0% 100%);
      width: 12px !important;
      height: 14px !important;
      margin-left: 3px;
      margin-top: 1px;
    }
    .dan-inline-value {
      color: #888;
      font-style: italic;
      padding-left: 16px;
    }
  `;
  document.head.appendChild(style);
}

export function useBreakpointDecorations(
  editorRef: React.RefObject<Monaco.editor.IStandaloneCodeEditor | null>,
  monacoRef: React.RefObject<typeof Monaco | null>,
) {
  const decorationsRef = useRef<string[]>([]);

  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const breakpoints = useDebugStore((s) => s.breakpoints);
  const pausedFile = useDebugStore((s) => s.pausedFile);
  const pausedLine = useDebugStore((s) => s.pausedLine);
  const inlineValues = useDebugStore((s) => s.inlineValues);
  const status = useDebugStore((s) => s.status);

  useEffect(() => {
    ensureStyles();
  }, []);

  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco || !activeFilePath) return;

    const fileBps = breakpoints[activeFilePath] ?? [];
    const isPausedHere = status === "paused" && pausedFile === activeFilePath && pausedLine !== null;

    const decorations: Monaco.editor.IModelDeltaDecoration[] = [];

    for (const bp of fileBps) {
      const glyphClass = bp.logMessage
        ? "dan-bp-logpoint"
        : bp.condition
          ? "dan-bp-conditional"
          : "dan-bp-normal";

      decorations.push({
        range: new monaco.Range(bp.line, 1, bp.line, 1),
        options: {
          isWholeLine: false,
          glyphMarginClassName: glyphClass,
          glyphMarginHoverMessage: bp.condition
            ? { value: `Condition: ${bp.condition}` }
            : bp.logMessage
              ? { value: `Log: ${bp.logMessage}` }
              : { value: "Breakpoint" },
          stickiness: monaco.editor.TrackedRangeStickiness.NeverGrowsWhenTypingAtEdges,
        },
      });
    }

    if (isPausedHere) {
      decorations.push({
        range: new monaco.Range(pausedLine!, 1, pausedLine!, 1),
        options: {
          isWholeLine: true,
          className: "dan-paused-line",
          glyphMarginClassName: "dan-paused-glyph",
          stickiness: monaco.editor.TrackedRangeStickiness.NeverGrowsWhenTypingAtEdges,
        },
      });

      for (const iv of inlineValues) {
        if (iv.line === pausedLine) {
          decorations.push({
            range: new monaco.Range(iv.line, Number.MAX_SAFE_INTEGER, iv.line, Number.MAX_SAFE_INTEGER),
            options: {
              after: {
                content: `  ${iv.name} = ${iv.value}`,
                inlineClassName: "dan-inline-value",
              },
              stickiness: monaco.editor.TrackedRangeStickiness.NeverGrowsWhenTypingAtEdges,
            },
          });
        }
      }
    }

    decorationsRef.current = editor.deltaDecorations(
      decorationsRef.current,
      decorations,
    );
  }, [activeFilePath, breakpoints, pausedFile, pausedLine, status, inlineValues, editorRef, monacoRef]);

  // Gutter click handler to toggle breakpoints
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;

    const disposable = editor.onMouseDown((e: Monaco.editor.IEditorMouseEvent) => {
      if (
        e.target.type === 2 /* GLYPH_MARGIN */ &&
        e.target.position
      ) {
        const line = e.target.position.lineNumber;
        const filePath = useCodeStore.getState().activeFilePath;
        if (!filePath) return;

        useDebugStore.getState().toggleBreakpoint(filePath, line);

        const dbgStatus = useDebugStore.getState().status;
        if (dbgStatus === "running" || dbgStatus === "paused") {
          const bps = useDebugStore.getState().breakpoints[filePath] ?? [];
          nativeDebug.setBreakpoints(
            filePath,
            bps.map((b) => ({
              line: b.line,
              condition: b.condition,
              logMessage: b.logMessage,
            })),
          );
        }
      }
    });

    return () => disposable.dispose();
  }, [editorRef]);
}
