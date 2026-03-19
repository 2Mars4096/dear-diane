import type * as monaco from "monaco-editor";

export type ExtensionLanguageProviderType = "completion" | "hover" | "definition";

export interface ExtensionLanguageProviderRegistration {
  id: string;
  type: ExtensionLanguageProviderType;
  selector: any;
  triggers?: string[];
}

export interface ExtensionLanguageProviderInvocation {
  providerId: string;
  type: ExtensionLanguageProviderType;
  document: {
    uri: string;
    filePath: string;
    languageId: string;
    text: string;
    version?: number;
  };
  position: {
    line: number;
    character: number;
  };
  context?: any;
}

type MonacoLike = typeof import("monaco-editor");
type InvokeProvider = (payload: ExtensionLanguageProviderInvocation) => Promise<any>;

function convertRange(lspRange: any): monaco.IRange {
  return {
    startLineNumber: Number(lspRange?.start?.line ?? 0) + 1,
    startColumn: Number(lspRange?.start?.character ?? 0) + 1,
    endLineNumber: Number(lspRange?.end?.line ?? 0) + 1,
    endColumn: Number(lspRange?.end?.character ?? 0) + 1,
  };
}

function extractMarkdown(contents: any): string {
  if (typeof contents === "string") return contents;
  if (contents?.value) return contents.value;
  if (Array.isArray(contents)) {
    return contents
      .map((item) => extractMarkdown(item))
      .filter(Boolean)
      .join("\n\n");
  }
  return "";
}

export function matchesLanguageSelector(
  selector: any,
  languageId: string,
  uri: string,
): boolean {
  if (!selector) return true;
  if (typeof selector === "string") {
    return selector === "*" || selector === languageId;
  }
  if (Array.isArray(selector)) {
    return selector.some((entry) => matchesLanguageSelector(entry, languageId, uri));
  }
  if (typeof selector === "object") {
    const selectorLanguage = String(selector.language ?? "*");
    const selectorScheme = String(selector.scheme ?? "*");
    if (selectorLanguage !== "*" && selectorLanguage !== languageId) return false;
    if (selectorScheme !== "*" && !uri.startsWith(`${selectorScheme}:`)) return false;
    return true;
  }
  return false;
}

function documentPayload(model: monaco.editor.ITextModel) {
  return {
    uri: model.uri.toString(),
    filePath: model.uri.path,
    languageId: model.getLanguageId(),
    text: model.getValue(),
    version: model.getVersionId(),
  };
}

function createCompletionProvider(
  monacoApi: MonacoLike,
  registration: ExtensionLanguageProviderRegistration,
  invokeProvider: InvokeProvider,
): monaco.IDisposable {
  return monacoApi.languages.registerCompletionItemProvider("*", {
    triggerCharacters: registration.triggers ?? [],
    provideCompletionItems: async (model, position) => {
      if (!matchesLanguageSelector(registration.selector, model.getLanguageId(), model.uri.toString())) {
        return { suggestions: [] };
      }
      const result = await invokeProvider({
        providerId: registration.id,
        type: "completion",
        document: documentPayload(model),
        position: {
          line: position.lineNumber - 1,
          character: position.column - 1,
        },
        context: {
          triggerCharacter: position.column > 1 ? model.getValueInRange({
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: position.column - 1,
            endColumn: position.column,
          }) : undefined,
          triggerKind: 1,
        },
      });
      const items: any[] = Array.isArray(result?.items)
        ? result.items
        : Array.isArray(result)
          ? result
          : [];
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
            : item.label?.label ?? String(item.label ?? ""),
          kind: monacoApi.languages.CompletionItemKind.Text,
          insertText: item.insertText ?? item.textEdit?.newText ?? (
            typeof item.label === "string" ? item.label : item.label?.label ?? ""
          ),
          detail: item.detail,
          documentation: item.documentation?.value ?? item.documentation,
          range: item.textEdit?.range ? convertRange(item.textEdit.range) : defaultRange,
        })),
      };
    },
  });
}

function createHoverProvider(
  monacoApi: MonacoLike,
  registration: ExtensionLanguageProviderRegistration,
  invokeProvider: InvokeProvider,
): monaco.IDisposable {
  return monacoApi.languages.registerHoverProvider("*", {
    provideHover: async (model, position) => {
      if (!matchesLanguageSelector(registration.selector, model.getLanguageId(), model.uri.toString())) {
        return null;
      }
      const result = await invokeProvider({
        providerId: registration.id,
        type: "hover",
        document: documentPayload(model),
        position: {
          line: position.lineNumber - 1,
          character: position.column - 1,
        },
      });
      if (!result) return null;
      return {
        range: result.range ? convertRange(result.range) : undefined,
        contents: [{ value: extractMarkdown(result.contents) }],
      };
    },
  });
}

function createDefinitionProvider(
  monacoApi: MonacoLike,
  registration: ExtensionLanguageProviderRegistration,
  invokeProvider: InvokeProvider,
): monaco.IDisposable {
  return monacoApi.languages.registerDefinitionProvider("*", {
    provideDefinition: async (model, position) => {
      if (!matchesLanguageSelector(registration.selector, model.getLanguageId(), model.uri.toString())) {
        return null;
      }
      const result = await invokeProvider({
        providerId: registration.id,
        type: "definition",
        document: documentPayload(model),
        position: {
          line: position.lineNumber - 1,
          character: position.column - 1,
        },
      });
      if (!result) return null;
      const locations = Array.isArray(result) ? result : [result];
      return locations.map((location: any) => ({
        uri: monacoApi.Uri.parse(location.uri ?? location.targetUri),
        range: convertRange(location.range ?? location.targetRange ?? location.targetSelectionRange),
      }));
    },
  });
}

export function createExtensionLanguageProviderRegistrar(
  monacoApi: MonacoLike,
  invokeProvider: InvokeProvider,
) {
  const disposables = new Map<string, monaco.IDisposable>();

  const registerProvider = (registration: ExtensionLanguageProviderRegistration) => {
    disposables.get(registration.id)?.dispose();
    let disposable: monaco.IDisposable;
    switch (registration.type) {
      case "completion":
        disposable = createCompletionProvider(monacoApi, registration, invokeProvider);
        break;
      case "hover":
        disposable = createHoverProvider(monacoApi, registration, invokeProvider);
        break;
      case "definition":
        disposable = createDefinitionProvider(monacoApi, registration, invokeProvider);
        break;
      default:
        return;
    }
    disposables.set(registration.id, disposable);
  };

  return {
    registerProvider,
    disposeProvider(providerId: string) {
      disposables.get(providerId)?.dispose();
      disposables.delete(providerId);
    },
    handleEvent(event: string, data: any) {
      if (event === "languages:registerProvider" && data?.id) {
        registerProvider(data);
      }
      if (event === "languages:disposeProvider" && data?.id) {
        this.disposeProvider(String(data.id));
      }
    },
    disposeAll() {
      for (const disposable of disposables.values()) {
        disposable.dispose();
      }
      disposables.clear();
    },
  };
}
