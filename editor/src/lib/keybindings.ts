export interface Keybinding {
  id: string;
  label: string;
  defaultKey: string;
  category: string;
}

const DEFAULT_KEYBINDINGS: Keybinding[] = [
  { id: "sidebar.toggle", label: "Toggle Sidebar", defaultKey: "Cmd+B", category: "View" },
  { id: "terminal.toggle", label: "Toggle Terminal", defaultKey: "Cmd+`", category: "View" },
  { id: "file.save", label: "Save File", defaultKey: "Cmd+S", category: "File" },
  { id: "file.saveAll", label: "Save All", defaultKey: "Cmd+Alt+S", category: "File" },
  { id: "file.close", label: "Close Tab", defaultKey: "Cmd+W", category: "File" },
  { id: "file.reopenClosed", label: "Reopen Closed", defaultKey: "Cmd+Shift+T", category: "File" },
  { id: "file.quickOpen", label: "Quick Open", defaultKey: "Cmd+P", category: "File" },
  { id: "commandPalette", label: "Command Palette", defaultKey: "Cmd+Shift+P", category: "General" },
  { id: "search.inFiles", label: "Search in Files", defaultKey: "Cmd+Shift+F", category: "Search" },
  { id: "settings.open", label: "Open Settings", defaultKey: "Cmd+,", category: "General" },
  { id: "inlineEdit", label: "Inline AI Edit", defaultKey: "Cmd+K", category: "AI" },
  { id: "nav.goBack", label: "Go Back", defaultKey: "Alt+Left", category: "Navigation" },
  { id: "nav.goForward", label: "Go Forward", defaultKey: "Alt+Right", category: "Navigation" },
];

const STORAGE_KEY = "dan-keybindings";

export function getKeybindings(): Keybinding[] {
  return DEFAULT_KEYBINDINGS;
}

export function getKeybindingForAction(id: string): string | undefined {
  const userOverrides = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
  return userOverrides[id] ?? DEFAULT_KEYBINDINGS.find((k) => k.id === id)?.defaultKey;
}

export function setKeybinding(id: string, key: string): void {
  const userOverrides = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
  userOverrides[id] = key;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(userOverrides));
}

export function resetKeybinding(id: string): void {
  const userOverrides = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
  delete userOverrides[id];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(userOverrides));
}

export function resetAllKeybindings(): void {
  localStorage.removeItem(STORAGE_KEY);
}

export function getUserOverrides(): Record<string, string> {
  return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
}

export function formatKey(key: string): string {
  return key
    .replace(/Cmd/g, "\u2318")
    .replace(/Alt/g, "\u2325")
    .replace(/Shift/g, "\u21E7")
    .replace(/Ctrl/g, "\u2303")
    .replace(/Left/g, "\u2190")
    .replace(/Right/g, "\u2192")
    .replace(/\+/g, "");
}
