import { useEffect } from "react";
import * as monaco from "monaco-editor";
import { nativeLsp } from "../lib/electronBridge";
import { useCodeStore } from "../store/useCodeStore";
import { useSettingsStore } from "../store/useSettingsStore";

// ---------------------------------------------------------------------------
// LSP → Monaco type conversions
// ---------------------------------------------------------------------------

const LSP_COMPLETION_KIND_MAP: Record<number, monaco.languages.CompletionItemKind> = {
  1: monaco.languages.CompletionItemKind.Text,
  2: monaco.languages.CompletionItemKind.Method,
  3: monaco.languages.CompletionItemKind.Function,
  4: monaco.languages.CompletionItemKind.Constructor,
  5: monaco.languages.CompletionItemKind.Field,
  6: monaco.languages.CompletionItemKind.Variable,
  7: monaco.languages.CompletionItemKind.Class,
  8: monaco.languages.CompletionItemKind.Interface,
  9: monaco.languages.CompletionItemKind.Module,
  10: monaco.languages.CompletionItemKind.Property,
  11: monaco.languages.CompletionItemKind.Unit,
  12: monaco.languages.CompletionItemKind.Value,
  13: monaco.languages.CompletionItemKind.Enum,
  14: monaco.languages.CompletionItemKind.Keyword,
  15: monaco.languages.CompletionItemKind.Snippet,
  16: monaco.languages.CompletionItemKind.Color,
  17: monaco.languages.CompletionItemKind.File,
  18: monaco.languages.CompletionItemKind.Reference,
  19: monaco.languages.CompletionItemKind.Folder,
  20: monaco.languages.CompletionItemKind.EnumMember,
  21: monaco.languages.CompletionItemKind.Constant,
  22: monaco.languages.CompletionItemKind.Struct,
  23: monaco.languages.CompletionItemKind.Event,
  24: monaco.languages.CompletionItemKind.Operator,
  25: monaco.languages.CompletionItemKind.TypeParameter,
};

function mapCompletionKind(lspKind?: number): monaco.languages.CompletionItemKind {
  if (lspKind === undefined) return monaco.languages.CompletionItemKind.Text;
  return LSP_COMPLETION_KIND_MAP[lspKind] ?? monaco.languages.CompletionItemKind.Text;
}

const LSP_SYMBOL_KIND_MAP: Record<number, monaco.languages.SymbolKind> = {
  1: monaco.languages.SymbolKind.File,
  2: monaco.languages.SymbolKind.Module,
  3: monaco.languages.SymbolKind.Namespace,
  4: monaco.languages.SymbolKind.Package,
  5: monaco.languages.SymbolKind.Class,
  6: monaco.languages.SymbolKind.Method,
  7: monaco.languages.SymbolKind.Property,
  8: monaco.languages.SymbolKind.Field,
  9: monaco.languages.SymbolKind.Constructor,
  10: monaco.languages.SymbolKind.Enum,
  11: monaco.languages.SymbolKind.Interface,
  12: monaco.languages.SymbolKind.Function,
  13: monaco.languages.SymbolKind.Variable,
  14: monaco.languages.SymbolKind.Constant,
  15: monaco.languages.SymbolKind.String,
  16: monaco.languages.SymbolKind.Number,
  17: monaco.languages.SymbolKind.Boolean,
  18: monaco.languages.SymbolKind.Array,
  19: monaco.languages.SymbolKind.Object,
  20: monaco.languages.SymbolKind.Key,
  21: monaco.languages.SymbolKind.Null,
  22: monaco.languages.SymbolKind.EnumMember,
  23: monaco.languages.SymbolKind.Struct,
  24: monaco.languages.SymbolKind.Event,
  25: monaco.languages.SymbolKind.Operator,
  26: monaco.languages.SymbolKind.TypeParameter,
};

function mapSymbolKind(lspKind: number): monaco.languages.SymbolKind {
  return LSP_SYMBOL_KIND_MAP[lspKind] ?? monaco.languages.SymbolKind.Variable;
}

function convertRange(lspRange: any): monaco.IRange {
  return {
    startLineNumber: lspRange.start.line + 1,
    startColumn: lspRange.start.character + 1,
    endLineNumber: lspRange.end.line + 1,
    endColumn: lspRange.end.character + 1,
  };
}

function mapSeverity(lspSeverity?: number): monaco.MarkerSeverity {
  switch (lspSeverity) {
    case 1: return monaco.MarkerSeverity.Error;
    case 2: return monaco.MarkerSeverity.Warning;
    case 3: return monaco.MarkerSeverity.Info;
    case 4: return monaco.MarkerSeverity.Hint;
    default: return monaco.MarkerSeverity.Info;
  }
}

function extractMarkdown(contents: any): string {
  if (typeof contents === "string") return contents;
  if (contents?.value) return contents.value;
  if (contents?.kind === "markdown") return contents.value ?? "";
  if (Array.isArray(contents)) {
    return contents
      .map((c: any) => {
        if (typeof c === "string") return c;
        if (c?.value) return c.value;
        if (c?.language && c?.value) return `\`\`\`${c.language}\n${c.value}\n\`\`\``;
        return "";
      })
      .join("\n\n");
  }
  if (contents?.language && contents?.value) {
    return `\`\`\`${contents.language}\n${contents.value}\n\`\`\``;
  }
  return "";
}

function flattenSymbols(symbols: any[]): monaco.languages.DocumentSymbol[] {
  const result: monaco.languages.DocumentSymbol[] = [];
  for (const sym of symbols) {
    const range = sym.range ?? sym.location?.range;
    const selRange = sym.selectionRange ?? range;
    if (!range) continue;
    const entry: monaco.languages.DocumentSymbol = {
      name: sym.name,
      detail: sym.detail ?? "",
      kind: mapSymbolKind(sym.kind),
      range: convertRange(range),
      selectionRange: convertRange(selRange),
      tags: sym.tags ?? [],
      children: sym.children ? flattenSymbols(sym.children) : [],
    };
    result.push(entry);
  }
  return result;
}

function convertWorkspaceEdit(lspEdit: any): monaco.languages.WorkspaceEdit {
  const edits: monaco.languages.IWorkspaceTextEdit[] = [];
  if (lspEdit.changes) {
    for (const [uri, textEdits] of Object.entries<any[]>(lspEdit.changes)) {
      for (const te of textEdits) {
        edits.push({
          resource: monaco.Uri.parse(uri),
          textEdit: { range: convertRange(te.range), text: te.newText },
          versionId: undefined,
        });
      }
    }
  }
  if (lspEdit.documentChanges) {
    for (const dc of lspEdit.documentChanges) {
      if (dc.textDocument && dc.edits) {
        const uri = dc.textDocument.uri;
        for (const te of dc.edits) {
          edits.push({
            resource: monaco.Uri.parse(uri),
            textEdit: { range: convertRange(te.range), text: te.newText },
            versionId: dc.textDocument.version ?? undefined,
          });
        }
      }
    }
  }
  return { edits };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useMonacoLsp() {
  useEffect(() => {
    const disposables: monaco.IDisposable[] = [];

    const roots = useCodeStore.getState().pinnedRoots;
    if (roots.length > 0) {
      nativeLsp.start(roots[0]);
    }

    // Completion
    disposables.push(
      monaco.languages.registerCompletionItemProvider("*", {
        triggerCharacters: [".", "/", "<", '"', "'", " ", "@", ":"],
        provideCompletionItems: async (model, position): Promise<monaco.languages.CompletionList> => {
          const result = await nativeLsp.completion({
            filePath: model.uri.path,
            line: position.lineNumber - 1,
            character: position.column - 1,
          });
          if (!result) return { suggestions: [] };

          const items: any[] = result.items ?? result;
          const wordRange = model.getWordUntilPosition(position);
          const defaultRange: monaco.IRange = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: wordRange.startColumn,
            endColumn: wordRange.endColumn,
          };
          return {
            suggestions: items.map((item: any) => ({
              label: typeof item.label === "string"
                ? item.label
                : item.label?.label ?? String(item.label),
              kind: mapCompletionKind(item.kind),
              insertText: item.textEdit?.newText ?? item.insertText ?? (typeof item.label === "string" ? item.label : item.label?.label ?? ""),
              insertTextRules: item.insertTextFormat === 2
                ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet
                : undefined,
              detail: item.detail,
              documentation: item.documentation?.value ?? item.documentation,
              range: item.textEdit?.range ? convertRange(item.textEdit.range) : defaultRange,
              sortText: item.sortText,
              filterText: item.filterText,
              preselect: item.preselect,
              commitCharacters: item.commitCharacters,
            })),
          };
        },
      }),
    );

    // Hover
    disposables.push(
      monaco.languages.registerHoverProvider("*", {
        provideHover: async (model, position) => {
          const result = await nativeLsp.hover({
            filePath: model.uri.path,
            line: position.lineNumber - 1,
            character: position.column - 1,
          });
          if (!result) return null;
          return {
            range: result.range ? convertRange(result.range) : undefined,
            contents: [{ value: extractMarkdown(result.contents) }],
          };
        },
      }),
    );

    // Definition (Cmd+click / F12)
    disposables.push(
      monaco.languages.registerDefinitionProvider("*", {
        provideDefinition: async (model, position) => {
          const result = await nativeLsp.definition({
            filePath: model.uri.path,
            line: position.lineNumber - 1,
            character: position.column - 1,
          });
          if (!result) return null;
          const locations = Array.isArray(result) ? result : [result];
          return locations.map((loc: any) => ({
            uri: monaco.Uri.parse(loc.uri ?? loc.targetUri),
            range: convertRange(loc.range ?? loc.targetRange ?? loc.targetSelectionRange),
          }));
        },
      }),
    );

    // References (Shift+F12)
    disposables.push(
      monaco.languages.registerReferenceProvider("*", {
        provideReferences: async (model, position) => {
          const result = await nativeLsp.references({
            filePath: model.uri.path,
            line: position.lineNumber - 1,
            character: position.column - 1,
          });
          if (!result) return [];
          return result.map((loc: any) => ({
            uri: monaco.Uri.parse(loc.uri),
            range: convertRange(loc.range),
          }));
        },
      }),
    );

    // Document Symbols
    disposables.push(
      monaco.languages.registerDocumentSymbolProvider("*", {
        provideDocumentSymbols: async (model) => {
          const result = await nativeLsp.documentSymbol({ filePath: model.uri.path });
          if (!result) return [];
          return flattenSymbols(result);
        },
      }),
    );

    // Signature Help
    disposables.push(
      monaco.languages.registerSignatureHelpProvider("*", {
        signatureHelpTriggerCharacters: ["(", ","],
        signatureHelpRetriggerCharacters: [","],
        provideSignatureHelp: async (model, position) => {
          const result = await nativeLsp.signatureHelp({
            filePath: model.uri.path,
            line: position.lineNumber - 1,
            character: position.column - 1,
          });
          if (!result) return null;
          return {
            value: {
              signatures: (result.signatures ?? []).map((sig: any) => ({
                label: sig.label,
                documentation: sig.documentation?.value ?? sig.documentation,
                parameters: (sig.parameters ?? []).map((p: any) => ({
                  label: p.label,
                  documentation: p.documentation?.value ?? p.documentation,
                })),
              })),
              activeSignature: result.activeSignature ?? 0,
              activeParameter: result.activeParameter ?? 0,
            },
            dispose: () => {},
          };
        },
      }),
    );

    // Code Actions
    disposables.push(
      monaco.languages.registerCodeActionProvider("*", {
        provideCodeActions: async (model, range, context) => {
          const result = await nativeLsp.codeAction({
            filePath: model.uri.path,
            range: {
              start: { line: range.startLineNumber - 1, character: range.startColumn - 1 },
              end: { line: range.endLineNumber - 1, character: range.endColumn - 1 },
            },
            diagnostics: context.markers.map((m) => ({
              range: {
                start: { line: m.startLineNumber - 1, character: m.startColumn - 1 },
                end: { line: m.endLineNumber - 1, character: m.endColumn - 1 },
              },
              message: m.message,
              severity: m.severity,
              source: m.source,
              code: m.code,
            })),
          });
          if (!result) return { actions: [], dispose: () => {} };
          return {
            actions: result
              .filter((action: any) => action.title)
              .map((action: any) => ({
                title: action.title,
                kind: action.kind,
                diagnostics: [],
                edit: action.edit ? convertWorkspaceEdit(action.edit) : undefined,
                isPreferred: action.isPreferred,
              })),
            dispose: () => {},
          };
        },
      }),
    );

    // Rename
    disposables.push(
      monaco.languages.registerRenameProvider("*", {
        provideRenameEdits: async (model, position, newName) => {
          const result = await nativeLsp.rename({
            filePath: model.uri.path,
            line: position.lineNumber - 1,
            character: position.column - 1,
            newName,
          });
          if (!result) return null;
          return convertWorkspaceEdit(result);
        },
      }),
    );

    // Document Formatting
    disposables.push(
      monaco.languages.registerDocumentFormattingEditProvider("*", {
        provideDocumentFormattingEdits: async (model) => {
          const settings = useSettingsStore.getState();
          const result = await nativeLsp.formatting({
            filePath: model.uri.path,
            tabSize: settings.tabSize,
            insertSpaces: true,
          });
          if (!result) return [];
          return result.map((edit: any) => ({
            range: convertRange(edit.range),
            text: edit.newText,
          }));
        },
      }),
    );

    // Diagnostics listener
    const unsubDiag = nativeLsp.onDiagnostics((data: any) => {
      const uri: string = data.uri;
      const filePath = uri.replace(/^file:\/\//, "");
      const markers: monaco.editor.IMarkerData[] = (data.diagnostics ?? []).map((d: any) => ({
        severity: mapSeverity(d.severity),
        startLineNumber: d.range.start.line + 1,
        startColumn: d.range.start.character + 1,
        endLineNumber: d.range.end.line + 1,
        endColumn: d.range.end.character + 1,
        message: d.message,
        source: d.source,
        code: d.code != null ? String(d.code) : undefined,
        tags: d.tags,
      }));
      const model = monaco.editor.getModels().find((m) => m.uri.path === filePath);
      if (model) {
        monaco.editor.setModelMarkers(model, "lsp", markers);
      }
    });

    return () => {
      disposables.forEach((d) => d.dispose());
      unsubDiag();
    };
  }, []);
}
