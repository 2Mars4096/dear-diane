import { useEffect, useRef } from "react";
import type { editor as monacoNs, IRange } from "monaco-editor";

interface DiffDecoration {
  range: IRange;
  type: "added" | "removed" | "modified";
}

/**
 * Simple set-difference diff: lines present in modified but not original
 * are marked "added". This intentionally avoids a full LCS for performance
 * on large files — trade-off is that reordered lines show as new.
 */
export function computeInlineDiffs(original: string, modified: string): DiffDecoration[] {
  const origLines = original.split("\n");
  const modLines = modified.split("\n");
  const decorations: DiffDecoration[] = [];

  const origCounts = new Map<string, number>();
  for (const l of origLines) {
    const t = l.trim();
    origCounts.set(t, (origCounts.get(t) ?? 0) + 1);
  }

  const usedCounts = new Map<string, number>();
  for (let i = 0; i < modLines.length; i++) {
    const trimmed = modLines[i].trim();
    if (!trimmed) continue;
    const available = (origCounts.get(trimmed) ?? 0) - (usedCounts.get(trimmed) ?? 0);
    if (available > 0) {
      usedCounts.set(trimmed, (usedCounts.get(trimmed) ?? 0) + 1);
    } else {
      decorations.push({
        range: {
          startLineNumber: i + 1,
          startColumn: 1,
          endLineNumber: i + 1,
          endColumn: modLines[i].length + 1,
        },
        type: "added",
      });
    }
  }

  return decorations;
}

export function useInlineDiffDecorations(
  editorRef: React.MutableRefObject<monacoNs.IStandaloneCodeEditor | null>,
  enabled: boolean,
  original: string | null,
  modified: string | null,
) {
  const decorationsRef = useRef<string[]>([]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || !enabled || !original || !modified) {
      if (editor && decorationsRef.current.length > 0) {
        decorationsRef.current = editor.deltaDecorations(decorationsRef.current, []);
      }
      return;
    }

    if (original === modified) {
      if (decorationsRef.current.length > 0) {
        decorationsRef.current = editor.deltaDecorations(decorationsRef.current, []);
      }
      return;
    }

    const diffs = computeInlineDiffs(original, modified);

    const monacoDecorations: monacoNs.IModelDeltaDecoration[] = diffs.map(d => ({
      range: d.range,
      options: {
        isWholeLine: true,
        className:
          d.type === "added"
            ? "inline-diff-added"
            : d.type === "removed"
              ? "inline-diff-removed"
              : "inline-diff-modified",
        glyphMarginClassName:
          d.type === "added" ? "inline-diff-glyph-added" : "inline-diff-glyph-removed",
      },
    }));

    decorationsRef.current = editor.deltaDecorations(decorationsRef.current, monacoDecorations);

    return () => {
      if (editor) {
        try {
          decorationsRef.current = editor.deltaDecorations(decorationsRef.current, []);
        } catch {
          // editor may have been disposed
        }
      }
    };
  }, [enabled, original, modified, editorRef]);
}
