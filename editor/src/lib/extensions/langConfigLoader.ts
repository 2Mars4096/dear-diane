/**
 * Load VS Code language-configuration.json files from extensions and register
 * them with Monaco.
 *
 * Monaco's LanguageConfiguration API closely mirrors the VS Code format, so
 * most fields map 1-to-1. Regex patterns in the JSON are compiled at load time.
 */

import { loader } from "@monaco-editor/react";
import { nativeExtension } from "../electronBridge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface VSCodeLanguageConfig {
  comments?: {
    lineComment?: string;
    blockComment?: [string, string];
  };
  brackets?: [string, string][];
  autoClosingPairs?: Array<{
    open: string;
    close: string;
    notIn?: string[];
  }>;
  surroundingPairs?: [string, string][];
  folding?: {
    markers?: { start: string; end: string };
    offSide?: boolean;
  };
  wordPattern?: string;
  indentationRules?: {
    increaseIndentPattern?: string;
    decreaseIndentPattern?: string;
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function loadLanguageConfig(
  extensionPath: string,
  langDefs: Array<{
    id: string;
    aliases?: string[];
    extensions?: string[];
    configuration?: string;
  }>,
): Promise<void> {
  const monaco = await loader.init();

  for (const lang of langDefs) {
    // Register language if Monaco doesn't know about it yet
    const registered = monaco.languages.getLanguages();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if (!registered.find((l: any) => l.id === lang.id)) {
      monaco.languages.register({
        id: lang.id,
        aliases: lang.aliases,
        extensions: lang.extensions,
      });
    }

    if (!lang.configuration) continue;

    try {
      const content = await nativeExtension.readFile(
        `${extensionPath}/${lang.configuration}`,
      );
      if (!content) continue;

      // VS Code language configs may use JSONC
      const stripped = content.replace(
        /\/\/.*$|\/\*[\s\S]*?\*\//gm,
        "",
      );
      const config: VSCodeLanguageConfig = JSON.parse(stripped);

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const monacoConfig: any = {};

      if (config.comments) {
        monacoConfig.comments = {
          lineComment: config.comments.lineComment,
          blockComment: config.comments.blockComment,
        };
      }

      if (config.brackets) {
        monacoConfig.brackets = config.brackets;
      }

      if (config.autoClosingPairs) {
        monacoConfig.autoClosingPairs = config.autoClosingPairs.map((p) => ({
          open: p.open,
          close: p.close,
          notIn: p.notIn,
        }));
      }

      if (config.surroundingPairs) {
        monacoConfig.surroundingPairs = config.surroundingPairs.map(
          ([open, close]) => ({ open, close }),
        );
      }

      if (config.folding?.markers) {
        monacoConfig.folding = {
          markers: {
            start: new RegExp(config.folding.markers.start),
            end: new RegExp(config.folding.markers.end),
          },
        };
      }

      if (config.indentationRules) {
        monacoConfig.indentRules = {
          increaseIndentPattern: config.indentationRules.increaseIndentPattern
            ? new RegExp(config.indentationRules.increaseIndentPattern)
            : /$/,
          decreaseIndentPattern: config.indentationRules.decreaseIndentPattern
            ? new RegExp(config.indentationRules.decreaseIndentPattern)
            : /$/,
        };
      }

      if (config.wordPattern) {
        try {
          monacoConfig.wordPattern = new RegExp(config.wordPattern);
        } catch {
          // Invalid wordPattern regex — skip
        }
      }

      monaco.languages.setLanguageConfiguration(lang.id, monacoConfig);
    } catch (err) {
      console.warn(`Failed to load language config for ${lang.id}:`, err);
    }
  }
}
