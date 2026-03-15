import { useEffect, useRef } from "react";
import { nativeLsp } from "../lib/electronBridge";
import { useCodeStore } from "../store/useCodeStore";
import { useSettingsStore } from "../store/useSettingsStore";

const DEBOUNCE_MS = 100;

const EXT_TO_LANG: Record<string, string> = {
  ".ts": "typescript",
  ".tsx": "typescriptreact",
  ".js": "javascript",
  ".jsx": "javascriptreact",
  ".mjs": "javascript",
  ".cjs": "javascript",
  ".mts": "typescript",
  ".cts": "typescript",
  ".py": "python",
  ".pyi": "python",
  ".json": "json",
  ".jsonc": "jsonc",
  ".css": "css",
  ".scss": "scss",
  ".less": "less",
  ".html": "html",
  ".htm": "html",
};

function lspLanguageId(filePath: string): string {
  const ext = filePath.slice(filePath.lastIndexOf(".")).toLowerCase();
  return EXT_TO_LANG[ext] ?? "plaintext";
}

/**
 * Tracks which files are open in the LSP and sends didOpen/didChange/didSave/didClose.
 * Attaches to the code store reactively — no per-editor wiring required.
 */
export function useLspDocSync() {
  const openFilesRef = useRef<Map<string, { version: number; content: string }>>(new Map());
  const debounceTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  useEffect(() => {
    const tracked = openFilesRef.current;
    const timers = debounceTimers.current;

    const unsubStore = useCodeStore.subscribe((state, prevState) => {
      const currFiles = state.openFiles;
      const prevPaths = new Set(prevState.openFiles.map((f) => f.path));
      const currPaths = new Set(currFiles.map((f) => f.path));

      // Opened files
      for (const file of currFiles) {
        if (!tracked.has(file.path)) {
          const version = 1;
          tracked.set(file.path, { version, content: file.content });
          nativeLsp.didOpen({
            filePath: file.path,
            languageId: lspLanguageId(file.path),
            version,
            text: file.content,
          });
        }
      }

      // Closed files
      for (const prevPath of prevPaths) {
        if (!currPaths.has(prevPath)) {
          tracked.delete(prevPath);
          clearTimerFor(prevPath);
          nativeLsp.didClose({ filePath: prevPath });
        }
      }

      // Content changes (debounced)
      for (const file of currFiles) {
        const entry = tracked.get(file.path);
        if (entry && file.content !== entry.content) {
          clearTimerFor(file.path);
          const timer = setTimeout(() => {
            timers.delete(file.path);
            const e = tracked.get(file.path);
            if (!e) return;
            const newVersion = e.version + 1;
            tracked.set(file.path, { version: newVersion, content: file.content });
            nativeLsp.didChange({
              filePath: file.path,
              version: newVersion,
              changes: [{ text: file.content }],
            });
          }, DEBOUNCE_MS);
          timers.set(file.path, timer);
        }
      }
    });

    function clearTimerFor(path: string) {
      const t = timers.get(path);
      if (t !== undefined) {
        clearTimeout(t);
        timers.delete(path);
      }
    }

    // Listen for saves
    const handleSave = async (e: Event) => {
      const { filePath, text } = (e as CustomEvent).detail ?? {};
      if (filePath) {
        nativeLsp.didSave({ filePath, text });
        const settings = useSettingsStore.getState();
        if (settings.formatOnSave) {
          await requestFormatOnSave(filePath);
        }
        if (settings.codeActionsOnSave) {
          await requestCodeActionsOnSave(filePath);
        }
      }
    };
    window.addEventListener("lsp:fileSaved", handleSave);

    return () => {
      unsubStore();
      window.removeEventListener("lsp:fileSaved", handleSave);
      for (const [, timer] of timers) clearTimeout(timer);
      timers.clear();
      for (const [path] of tracked) {
        nativeLsp.didClose({ filePath: path });
      }
      tracked.clear();
    };
  }, []);
}

async function requestCodeActionsOnSave(filePath: string) {
  const state = useCodeStore.getState();
  const file = state.openFiles.find((f) => f.path === filePath);
  if (!file) return;

  const lineCount = file.content.split("\n").length;
  const lastLineLength = (file.content.split("\n")[lineCount - 1] ?? "").length;

  try {
    const actions = await nativeLsp.codeAction({
      filePath,
      range: {
        start: { line: 0, character: 0 },
        end: { line: lineCount - 1, character: lastLineLength },
      },
      diagnostics: [],
    });

    if (!actions || !Array.isArray(actions) || actions.length === 0) return;

    const sourceActions = actions.filter(
      (a: any) =>
        a.kind === "source.organizeImports" || a.kind === "source.fixAll",
    );

    let content = file.content;
    for (const action of sourceActions) {
      if (!action.edit?.changes && !action.edit?.documentChanges) continue;
      let lines = content.split("\n");

      const edits: Array<{ range: any; newText: string }> = [];
      if (action.edit.changes) {
        for (const textEdits of Object.values<any[]>(action.edit.changes)) {
          edits.push(...textEdits);
        }
      }
      if (action.edit.documentChanges) {
        for (const dc of action.edit.documentChanges) {
          if (dc.edits) edits.push(...dc.edits);
        }
      }

      const sorted = [...edits].sort((a: any, b: any) => {
        if (a.range.start.line !== b.range.start.line)
          return b.range.start.line - a.range.start.line;
        return b.range.start.character - a.range.start.character;
      });

      for (const edit of sorted) {
        const r = edit.range;
        const before = lines[r.start.line]?.slice(0, r.start.character) ?? "";
        const after = lines[r.end.line]?.slice(r.end.character) ?? "";
        const newText = before + edit.newText + after;
        const newLines = newText.split("\n");
        lines.splice(r.start.line, r.end.line - r.start.line + 1, ...newLines);
      }
      content = lines.join("\n");
    }

    if (content !== file.content) {
      state.updateFileContent(filePath, content);
      state.markFileSaved(filePath);
      const { nativeFs } = await import("../lib/electronBridge");
      await nativeFs.writeFile(filePath, content);
    }
  } catch {
    // Code actions not supported or failed silently
  }
}

async function requestFormatOnSave(filePath: string) {
  const settings = useSettingsStore.getState();
  const result = await nativeLsp.formatting({
    filePath,
    tabSize: settings.tabSize,
    insertSpaces: true,
  });
  if (!result || !Array.isArray(result) || result.length === 0) return;

  // Apply formatting edits to the store content
  const state = useCodeStore.getState();
  const file = state.openFiles.find((f) => f.path === filePath);
  if (!file) return;

  let lines = file.content.split("\n");
  const sorted = [...result].sort((a: any, b: any) => {
    if (a.range.start.line !== b.range.start.line) return b.range.start.line - a.range.start.line;
    return b.range.start.character - a.range.start.character;
  });

  for (const edit of sorted) {
    const r = edit.range;
    const startLine = r.start.line;
    const endLine = r.end.line;
    const before = lines[startLine]?.slice(0, r.start.character) ?? "";
    const after = lines[endLine]?.slice(r.end.character) ?? "";
    const newText = before + edit.newText + after;
    const newLines = newText.split("\n");
    lines.splice(startLine, endLine - startLine + 1, ...newLines);
  }

  const formatted = lines.join("\n");
  if (formatted !== file.content) {
    state.updateFileContent(filePath, formatted);
    state.markFileSaved(filePath);
    const { nativeFs } = await import("../lib/electronBridge");
    await nativeFs.writeFile(filePath, formatted);
  }
}
