import { describe, expect, it, vi } from "vitest";

import { createExtensionLanguageProviderRegistrar } from "../extensionLanguageProviders";

function createFakeMonaco() {
  const completionProviders: any[] = [];
  return {
    completionProviders,
    monaco: {
      languages: {
        CompletionItemKind: { Text: 1 },
        registerCompletionItemProvider: vi.fn((_selector: string, provider: any) => {
          completionProviders.push(provider);
          return { dispose: vi.fn() };
        }),
        registerHoverProvider: vi.fn(() => ({ dispose: vi.fn() })),
        registerDefinitionProvider: vi.fn(() => ({ dispose: vi.fn() })),
      },
      Uri: {
        parse: (value: string) => ({
          toString: () => value,
          path: value.replace(/^file:\/\//, ""),
        }),
      },
    },
  };
}

describe("extension language providers", () => {
  it("round-trips completion provider registrations back into Monaco", async () => {
    const fake = createFakeMonaco();
    const invokeProvider = vi.fn(async () => ({
      items: [
        {
          label: "console",
          insertText: "console",
          detail: "Built-in object",
        },
      ],
    }));
    const registrar = createExtensionLanguageProviderRegistrar(
      fake.monaco as any,
      invokeProvider,
    );

    registrar.handleEvent("languages:registerProvider", {
      id: "completion-1",
      type: "completion",
      selector: "typescript",
      triggers: ["."],
    });

    expect(fake.monaco.languages.registerCompletionItemProvider).toHaveBeenCalledTimes(1);

    const provider = fake.completionProviders[0];
    const result = await provider.provideCompletionItems(
      {
        uri: {
          toString: () => "file:///workspace/example.ts",
          path: "/workspace/example.ts",
        },
        getLanguageId: () => "typescript",
        getValue: () => "con",
        getVersionId: () => 1,
        getWordUntilPosition: () => ({ startColumn: 1, endColumn: 4 }),
        getValueInRange: () => ".",
      },
      { lineNumber: 1, column: 4 },
    );

    expect(invokeProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        providerId: "completion-1",
        type: "completion",
        document: expect.objectContaining({
          languageId: "typescript",
          filePath: "/workspace/example.ts",
        }),
      }),
    );
    expect(result.suggestions).toEqual([
      expect.objectContaining({
        label: "console",
        insertText: "console",
        detail: "Built-in object",
      }),
    ]);
  });
});
