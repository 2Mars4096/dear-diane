/**
 * Copilot-style ghost text inline completion provider.
 * Registers a Monaco InlineCompletionsProvider that calls the DAN server
 * for FIM-style completions, with debouncing and cancellation support.
 */
import * as monaco from "monaco-editor";
import { useSettingsStore } from "../../store/useSettingsStore";

const API_BASE = "/api";

let statusCallback: ((status: "idle" | "loading" | "error") => void) | null = null;

export function onCompletionStatusChange(cb: (status: "idle" | "loading" | "error") => void) {
  statusCallback = cb;
  return () => { statusCallback = null; };
}

function setStatus(s: "idle" | "loading" | "error") {
  statusCallback?.(s);
}

export function registerInlineCompletion(): monaco.IDisposable {
  let debounceTimer: ReturnType<typeof setTimeout>;

  const provider: monaco.languages.InlineCompletionsProvider = {
    provideInlineCompletions: async (model, position, context, token) => {
      const settings = useSettingsStore.getState();
      if (!(settings as Record<string, unknown>).inlineCompletionEnabled) {
        return { items: [] };
      }

      if (context.triggerKind === monaco.languages.InlineCompletionTriggerKind.Automatic) {
        await new Promise<void>((resolve) => {
          clearTimeout(debounceTimer);
          debounceTimer = setTimeout(resolve, 500);
        });
      }

      if (token.isCancellationRequested) return { items: [] };

      const lineContent = model.getLineContent(position.lineNumber);
      const prefix = lineContent.substring(0, position.column - 1);

      if (prefix.trim().length < 3) return { items: [] };

      const startLine = Math.max(1, position.lineNumber - 30);
      const endLine = Math.min(model.getLineCount(), position.lineNumber + 10);
      const beforeCursor = model.getValueInRange({
        startLineNumber: startLine,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column,
      });
      const afterCursor = model.getValueInRange({
        startLineNumber: position.lineNumber,
        startColumn: position.column,
        endLineNumber: endLine,
        endColumn: model.getLineMaxColumn(endLine),
      });

      setStatus("loading");

      try {
        const controller = new AbortController();
        const onCancel = token.onCancellationRequested(() => controller.abort());

        const response = await fetch(`${API_BASE}/chat/editor/complete`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prefix: beforeCursor,
            suffix: afterCursor,
            language: model.getLanguageId(),
            filePath: model.uri.path,
            maxTokens: 100,
          }),
          signal: controller.signal,
        });

        onCancel.dispose();

        if (!response.ok || token.isCancellationRequested) {
          setStatus("idle");
          return { items: [] };
        }

        const data = await response.json();
        const completion = data.completion?.trim();

        setStatus("idle");

        if (!completion) return { items: [] };

        return {
          items: [
            {
              insertText: completion,
              range: new monaco.Range(
                position.lineNumber,
                position.column,
                position.lineNumber,
                position.column,
              ),
            },
          ],
        };
      } catch {
        setStatus("idle");
        return { items: [] };
      }
    },

    freeInlineCompletions: () => {},
  };

  return monaco.languages.registerInlineCompletionsProvider(
    { pattern: "**" },
    provider,
  );
}
