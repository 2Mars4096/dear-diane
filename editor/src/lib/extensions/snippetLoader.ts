/**
 * Load VS Code snippet files from extensions and register them as Monaco
 * completion providers.
 *
 * VS Code and Monaco share the same snippet syntax ($1, ${1:placeholder},
 * $TM_FILENAME, etc.) so the body text is passed through without conversion.
 */

import { loader } from "@monaco-editor/react";
import { nativeExtension } from "../electronBridge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface VSCodeSnippet {
  prefix: string | string[];
  body: string | string[];
  description?: string;
  scope?: string;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function loadSnippetsFromExtension(
  extensionPath: string,
  snippetDefs: Array<{ language: string; path: string }>,
): Promise<void> {
  const monaco = await loader.init();

  for (const def of snippetDefs) {
    try {
      const content = await nativeExtension.readFile(
        `${extensionPath}/${def.path}`,
      );
      if (!content) continue;

      const snippets: Record<string, VSCodeSnippet> = JSON.parse(content);

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const completionItems: any[] = Object.entries(snippets).map(
        ([name, snippet]) => {
          const prefix = Array.isArray(snippet.prefix)
            ? snippet.prefix[0]
            : snippet.prefix;
          const body = Array.isArray(snippet.body)
            ? snippet.body.join("\n")
            : snippet.body;

          return {
            label: prefix,
            kind: monaco.languages.CompletionItemKind.Snippet,
            insertText: body,
            insertTextRules:
              monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            detail: name,
            documentation: snippet.description,
            sortText: `1_ext_${prefix}`,
          };
        },
      );

      // Determine target languages from per-snippet scope or definition default
      const languages = new Set<string>();
      for (const [, snippet] of Object.entries(snippets)) {
        if (snippet.scope) {
          snippet.scope.split(",").forEach((s) => languages.add(s.trim()));
        }
      }
      if (languages.size === 0) languages.add(def.language);

      for (const lang of languages) {
        monaco.languages.registerCompletionItemProvider(lang, {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          provideCompletionItems(model: any, position: any) {
            const word = model.getWordUntilPosition(position);
            const range = {
              startLineNumber: position.lineNumber,
              startColumn: word.startColumn,
              endLineNumber: position.lineNumber,
              endColumn: word.endColumn,
            };
            return {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              suggestions: completionItems.map((item: any) => ({
                ...item,
                range,
              })),
            };
          },
        });
      }
    } catch (err) {
      console.warn(`Failed to load snippets from ${def.path}:`, err);
    }
  }
}
