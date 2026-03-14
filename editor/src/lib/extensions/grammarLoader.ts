/**
 * Load TextMate grammars from VS Code extensions and register simplified
 * Monarch tokenizers in Monaco.
 *
 * Full TextMate grammar support requires vscode-textmate + oniguruma WASM,
 * which is heavy. This module converts the most common top-level patterns
 * (match + name) into Monarch rules, giving decent colorization for most
 * extension-contributed languages without the extra weight.
 */

import { loader } from "@monaco-editor/react";
import { nativeExtension } from "../electronBridge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TextMateGrammar {
  scopeName: string;
  patterns: Array<{
    name?: string;
    match?: string;
    begin?: string;
    end?: string;
    captures?: Record<string, { name: string }>;
    patterns?: TextMateGrammar["patterns"];
  }>;
  repository?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Scope → token mapping
// ---------------------------------------------------------------------------

function textMateToMonarchToken(tmScope: string): string {
  if (tmScope.startsWith("comment")) return "comment";
  if (tmScope.startsWith("string")) return "string";
  if (tmScope.startsWith("keyword")) return "keyword";
  if (tmScope.startsWith("constant.numeric")) return "number";
  if (tmScope.startsWith("constant")) return "constant";
  if (tmScope.startsWith("entity.name.function")) return "identifier";
  if (tmScope.startsWith("entity.name.type")) return "type";
  if (tmScope.startsWith("entity.name.tag")) return "tag";
  if (tmScope.startsWith("variable")) return "variable";
  if (tmScope.startsWith("storage")) return "keyword";
  if (tmScope.startsWith("support.function")) return "predefined";
  if (tmScope.startsWith("support.type")) return "type";
  if (tmScope.startsWith("punctuation")) return "delimiter";
  if (tmScope.startsWith("meta.embedded")) return "string";
  return "source";
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function registerTextMateGrammar(
  languageId: string,
  grammar: TextMateGrammar,
): Promise<void> {
  const monaco = await loader.init();

  const registered = monaco.languages.getLanguages();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (!registered.find((l: any) => l.id === languageId)) {
    monaco.languages.register({ id: languageId });
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rules: any[] = [];

  for (const pattern of grammar.patterns ?? []) {
    if (pattern.match && pattern.name) {
      try {
        const tokenType = textMateToMonarchToken(pattern.name);
        rules.push([new RegExp(pattern.match), tokenType]);
      } catch {
        // Invalid regex in grammar — skip
      }
    }
  }

  if (rules.length > 0) {
    monaco.languages.setMonarchTokensProvider(languageId, {
      tokenizer: { root: rules },
    });
  }
}

export async function loadGrammarsFromExtension(
  extensionPath: string,
  grammars: Array<{ language: string; scopeName: string; path: string }>,
): Promise<void> {
  for (const def of grammars) {
    try {
      const content = await nativeExtension.readFile(
        `${extensionPath}/${def.path}`,
      );
      if (!content) continue;

      const grammar: TextMateGrammar = JSON.parse(content);
      await registerTextMateGrammar(def.language, grammar);
    } catch (err) {
      console.warn(`Failed to load grammar for ${def.language}:`, err);
    }
  }
}
