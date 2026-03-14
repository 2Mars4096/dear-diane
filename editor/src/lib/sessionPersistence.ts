import { useCodeStore } from "../store/useCodeStore";

const STORAGE_KEY = "dan-editor-session";

export interface EditorSession {
  pinnedRoots: string[];
  openFilePaths: string[];
  activeFilePath: string | null;
  showTerminal: boolean;
  showSidebar: boolean;
  activeSidebarPanel: string;
  recentFiles: string[];
  savedAt: number;
}

export function saveSession(): void {
  const state = useCodeStore.getState();
  const session: EditorSession = {
    pinnedRoots: state.pinnedRoots,
    openFilePaths: state.openFiles.map((f) => f.path),
    activeFilePath: state.activeFilePath,
    showTerminal: state.showTerminal,
    showSidebar: state.showSidebar,
    activeSidebarPanel: state.activeSidebarPanel,
    recentFiles: state.recentFiles,
    savedAt: Date.now(),
  };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // localStorage may be full or unavailable — silently skip
  }
}

export function loadSession(): EditorSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as EditorSession;
    if (!Array.isArray(parsed.openFilePaths) || !Array.isArray(parsed.pinnedRoots)) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
