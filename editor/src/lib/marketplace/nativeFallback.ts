const NATIVE_CAPABILITIES = new Map<string, string>([
  ["typescript", "Built-in TypeScript/JavaScript support via typescript-language-server"],
  ["python", "Built-in Python support via pyright"],
  ["json", "Built-in JSON support via vscode-json-languageserver"],
  ["css", "Built-in CSS/SCSS/LESS support"],
  ["html", "Built-in HTML support"],
  ["git", "Built-in Git integration"],
  ["debugging", "Built-in DAP debugging (Node.js, Python)"],
  ["snippets", "Built-in snippets for 7 languages"],
  ["formatting", "Built-in format-on-save via LSP"],
]);

export function hasNativeCapability(capability: string): boolean {
  return NATIVE_CAPABILITIES.has(capability);
}

export function getNativeCapabilityInfo(capability: string): string | null {
  return NATIVE_CAPABILITIES.get(capability) ?? null;
}

const NATIVE_PREFERRED: Record<string, string> = {
  "vscode.typescript-language-features": "TypeScript support is built-in via LSP",
  "vscode.json-language-features": "JSON support is built-in",
  "vscode.css-language-features": "CSS support is built-in",
  "vscode.html-language-features": "HTML support is built-in",
  "ms-python.python": "Python support is built-in via pyright LSP",
  "vscode.git": "Git integration is built-in",
};

export function shouldPreferNative(extensionId: string): {
  prefer: boolean;
  reason: string;
} {
  if (NATIVE_PREFERRED[extensionId]) {
    return { prefer: true, reason: NATIVE_PREFERRED[extensionId] };
  }
  return { prefer: false, reason: "" };
}

export function getAllNativeCapabilities(): Array<{
  id: string;
  description: string;
}> {
  return Array.from(NATIVE_CAPABILITIES.entries()).map(([id, description]) => ({
    id,
    description,
  }));
}
