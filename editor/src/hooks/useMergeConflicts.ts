import { useEffect, useRef } from "react";
import type { editor as monacoEditor } from "monaco-editor";

export interface MergeConflict {
  startLine: number;
  midLine: number;
  endLine: number;
  currentContent: string;
  incomingContent: string;
}

export function detectConflicts(content: string): MergeConflict[] {
  const lines = content.split("\n");
  const conflicts: MergeConflict[] = [];

  let i = 0;
  while (i < lines.length) {
    if (lines[i].startsWith("<<<<<<<")) {
      const startLine = i + 1;
      let midLine = -1;
      let endLine = -1;

      for (let j = i + 1; j < lines.length; j++) {
        if (lines[j].startsWith("=======")) {
          midLine = j + 1;
        } else if (lines[j].startsWith(">>>>>>>") && midLine !== -1) {
          endLine = j + 1;
          break;
        }
      }

      if (midLine !== -1 && endLine !== -1) {
        conflicts.push({
          startLine,
          midLine,
          endLine,
          currentContent: lines.slice(i + 1, midLine - 1).join("\n"),
          incomingContent: lines.slice(midLine, endLine - 1).join("\n"),
        });
        i = endLine;
        continue;
      }
    }
    i++;
  }

  return conflicts;
}

/**
 * Applies merge conflict decorations to a Monaco editor instance.
 * Highlights current (green) and incoming (blue) regions with
 * an inline action label above each conflict.
 */
export function useMergeConflictDecorations(
  editor: monacoEditor.IStandaloneCodeEditor | null,
  content: string | undefined,
) {
  const decorationIds = useRef<string[]>([]);

  useEffect(() => {
    if (!editor || !content) {
      return;
    }

    const conflicts = detectConflicts(content);

    if (conflicts.length === 0) {
      if (decorationIds.current.length > 0) {
        decorationIds.current = editor.deltaDecorations(
          decorationIds.current,
          [],
        );
      }
      return;
    }

    const decorations: monacoEditor.IModelDeltaDecoration[] = [];

    for (const conflict of conflicts) {
      // Current change header + content (<<<<<<< through =======)
      decorations.push({
        range: {
          startLineNumber: conflict.startLine,
          startColumn: 1,
          endLineNumber: conflict.midLine,
          endColumn: 1,
        },
        options: {
          isWholeLine: true,
          className: "merge-conflict-current",
          overviewRuler: {
            color: "#4caf50",
            position: 4,
          },
        },
      });

      // Incoming change (======= through >>>>>>>)
      decorations.push({
        range: {
          startLineNumber: conflict.midLine,
          startColumn: 1,
          endLineNumber: conflict.endLine,
          endColumn: 1,
        },
        options: {
          isWholeLine: true,
          className: "merge-conflict-incoming",
          overviewRuler: {
            color: "#2196f3",
            position: 4,
          },
        },
      });

      // Inline action label above the conflict marker
      decorations.push({
        range: {
          startLineNumber: conflict.startLine,
          startColumn: 1,
          endLineNumber: conflict.startLine,
          endColumn: 1,
        },
        options: {
          before: {
            content: " Accept Current | Accept Incoming | Accept Both ",
            inlineClassName: "merge-conflict-actions",
          },
        },
      });
    }

    decorationIds.current = editor.deltaDecorations(
      decorationIds.current,
      decorations,
    );

    return () => {
      if (editor.getModel()) {
        decorationIds.current = editor.deltaDecorations(
          decorationIds.current,
          [],
        );
      }
    };
  }, [editor, content]);
}
