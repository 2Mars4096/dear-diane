import { useCodeStore } from "../store/useCodeStore";
import { useContentStore } from "../store/useContentStore";
import {
  useResearchStore,
  type ResearchNote,
  type ResearchPaper,
} from "../store/useResearchStore";
import { MODE_CONFIGS, type AppMode } from "../store/useAppStore";

const LEGACY_KEY = "dan-editor-session";

function storageKey(workspaceId: string) {
  return `dan-session-${workspaceId}`;
}

export interface EditorSession {
  pinnedRoots: string[];
  openFilePaths: string[];
  activeFilePath: string | null;
  showTerminal: boolean;
  showSidebar: boolean;
  activeSidebarPanel: string;
  recentFiles: string[];
  expandedDirs: Record<string, boolean>;
  currentBranch: string;
  savedAt: number;
}

export interface ResearchSession {
  primaryTab: string;
  contextTab: string;
  secondaryTab: string;
  activePaperId: string | null;
  papers: ResearchPaper[];
  notes: ResearchNote[];
  documentContent: string;
  showPipeline: boolean;
  showSecondary: boolean;
  showContextPanel: boolean;
  activeRailSection: string;
}

export interface WorkspaceSession {
  code: EditorSession;
  research: ResearchSession;
  content: {
    activeProjectRoot: string | null;
    activePagePath: string | null;
    draftsByPath: Record<string, string>;
  };
  lastActiveMode: AppMode;
  savedAt: number;
}

function coerceAppMode(value: unknown): AppMode {
  if (
    typeof value === "string" &&
    MODE_CONFIGS.some((mode) => mode.id === value)
  ) {
    return value as AppMode;
  }
  return "chat";
}

export function saveSession(workspaceId?: string, lastActiveMode: AppMode = "chat"): void {
  const codeState = useCodeStore.getState();
  const researchState = useResearchStore.getState();
  const contentState = useContentStore.getState();

  const session: WorkspaceSession = {
    code: {
      pinnedRoots: codeState.pinnedRoots,
      openFilePaths: codeState.openFiles.map((f) => f.path),
      activeFilePath: codeState.activeFilePath,
      showTerminal: codeState.showTerminal,
      showSidebar: codeState.showSidebar,
      activeSidebarPanel: codeState.activeSidebarPanel,
      recentFiles: codeState.recentFiles,
      expandedDirs: codeState.expandedDirs,
      currentBranch: codeState.currentBranch,
      savedAt: Date.now(),
    },
    research: {
      primaryTab: researchState.primaryTab,
      contextTab: researchState.contextTab,
      secondaryTab: researchState.secondaryTab,
      activePaperId: researchState.activePaperId,
      papers: researchState.papers,
      notes: researchState.notes,
      documentContent: researchState.documentContent,
      showPipeline: researchState.showPipeline,
      showSecondary: researchState.showSecondary,
      showContextPanel: researchState.showContextPanel,
      activeRailSection: researchState.activeRailSection,
    },
    content: {
      activeProjectRoot: contentState.activeProjectRoot,
      activePagePath: contentState.activePagePath,
      draftsByPath: contentState.draftsByPath,
    },
    lastActiveMode,
    savedAt: Date.now(),
  };

  const key = workspaceId ? storageKey(workspaceId) : LEGACY_KEY;
  try {
    localStorage.setItem(key, JSON.stringify(session));
  } catch { /* quota or unavailable */ }
}

export function loadSession(workspaceId?: string): WorkspaceSession | null {
  const key = workspaceId ? storageKey(workspaceId) : LEGACY_KEY;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) {
      if (workspaceId) return loadSession();
      return null;
    }
    const parsed = JSON.parse(raw);
    if (parsed.code) {
      return {
        ...parsed,
        content: parsed.content ?? {
          activeProjectRoot: null,
          activePagePath: null,
          draftsByPath: {},
        },
        lastActiveMode: coerceAppMode(parsed.lastActiveMode),
      } as WorkspaceSession;
    }
    if (Array.isArray(parsed.openFilePaths)) {
      return {
        code: parsed as EditorSession,
        research: {
          primaryTab: "editor",
          contextTab: "references",
          secondaryTab: "code",
          activePaperId: null,
          papers: [],
          notes: [],
          documentContent: "",
          showPipeline: false,
          showSecondary: false,
          showContextPanel: false,
          activeRailSection: "library",
        },
        content: {
          activeProjectRoot: null,
          activePagePath: null,
          draftsByPath: {},
        },
        lastActiveMode: "chat",
        savedAt: parsed.savedAt ?? Date.now(),
      };
    }
    return null;
  } catch {
    return null;
  }
}

export function clearSession(workspaceId?: string): void {
  const key = workspaceId ? storageKey(workspaceId) : LEGACY_KEY;
  try {
    localStorage.removeItem(key);
  } catch { /* ignore */ }
}
