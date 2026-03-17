import { create } from "zustand";

export interface OpenFile {
  path: string;
  content: string;
  language: string;
  dirty: boolean;
  originalContent: string;
}

export interface MultiFileEditEntry {
  filePath: string;
  originalContent: string;
  modifiedContent: string;
  accepted: boolean | null;
}

export interface FileTreeEntry {
  name: string;
  path: string;
  isDirectory: boolean;
  children?: FileTreeEntry[];
}

export interface TerminalInstance {
  id: string;
  title: string;
}

function detectLanguage(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() ?? "";
  const MAP: Record<string, string> = {
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    py: "python",
    rs: "rust",
    go: "go",
    json: "json",
    md: "markdown",
    html: "html",
    css: "css",
    scss: "scss",
    yaml: "yaml",
    yml: "yaml",
    toml: "toml",
    sh: "shell",
    bash: "shell",
    zsh: "shell",
    sql: "sql",
    graphql: "graphql",
    xml: "xml",
    svg: "xml",
    txt: "plaintext",
  };
  return MAP[ext] ?? "plaintext";
}

interface CodeState {
  pinnedRoots: string[];
  fileTree: Record<string, FileTreeEntry[]>;
  expandedDirs: Record<string, boolean>;

  openFiles: OpenFile[];
  activeFilePath: string | null;

  terminals: TerminalInstance[];
  activeTerminalId: string | null;

  showTerminal: boolean;
  showSidebar: boolean;
  activeSidebarPanel: "explorer" | "search" | "git" | "extensions" | "timeline" | "tasks" | "testing" | "outline" | "debug" | "workflow" | "furnace";

  diffFile: { original: string; modified: string; originalPath: string; modifiedPath: string } | null;
  showDiff: boolean;

  multiFileEdits: MultiFileEditEntry[];
  showMultiFileReview: boolean;
  openMultiFileReview: (edits: MultiFileEditEntry[]) => void;
  closeMultiFileReview: () => void;
  acceptMultiFileEdit: (index: number) => void;
  rejectMultiFileEdit: (index: number) => void;
  acceptAllMultiFileEdits: () => void;

  showSettings: boolean;
  setShowSettings: (v: boolean) => void;
  toggleSettings: () => void;

  showKeybindings: boolean;
  setShowKeybindings: (v: boolean) => void;

  zenModeFilePath: string | null;
  setZenModeFilePath: (path: string | null) => void;

  splitFilePath: string | null;
  setSplitFilePath: (path: string | null) => void;

  cursorPosition: { lineNumber: number; column: number };
  setCursorPosition: (line: number, col: number) => void;

  currentBranch: string;
  setCurrentBranch: (branch: string) => void;

  quickOpenVisible: boolean;
  setQuickOpenVisible: (v: boolean) => void;

  commandPaletteVisible: boolean;
  setCommandPaletteVisible: (v: boolean) => void;

  symbolSearchVisible: boolean;
  setSymbolSearchVisible: (v: boolean) => void;

  localHistoryVisible: boolean;
  setLocalHistoryVisible: (v: boolean) => void;

  addPinnedRoot: (rootPath: string) => void;
  removePinnedRoot: (rootPath: string) => void;
  setFileTree: (root: string, entries: FileTreeEntry[]) => void;
  toggleDir: (dirPath: string) => void;
  setDirExpanded: (dirPath: string, expanded: boolean) => void;

  recentlyClosed: Array<{ path: string; content: string; language: string }>;

  recentFiles: string[];
  addRecentFile: (filePath: string) => void;
  clearRecentFiles: () => void;

  agentSuggestedFiles: Array<{ path: string; isNew: boolean; timestamp: number }>;
  addAgentSuggestedFile: (path: string, isNew: boolean) => void;
  clearAgentSuggestedFiles: () => void;

  openFile: (filePath: string, content: string, language?: string) => void;
  closeFile: (filePath: string) => void;
  reorderFile: (fromIndex: number, toIndex: number) => void;
  setActiveFile: (filePath: string) => void;
  updateFileContent: (filePath: string, content: string) => void;
  reloadFileContent: (filePath: string, content: string) => void;
  markFileSaved: (filePath: string) => void;
  reopenClosedFile: () => void;
  saveAllFiles: () => void;
  revertFile: (filePath: string, diskContent: string) => void;

  addTerminal: (id: string, title?: string) => void;
  removeTerminal: (id: string) => void;
  setActiveTerminal: (id: string) => void;

  setShowTerminal: (show: boolean) => void;
  setShowSidebar: (show: boolean) => void;
  toggleTerminal: () => void;
  toggleSidebar: () => void;
  setActiveSidebarPanel: (panel: "explorer" | "search" | "git" | "extensions" | "timeline" | "tasks" | "testing" | "outline" | "debug" | "workflow" | "furnace") => void;

  restoreSession: (session: {
    pinnedRoots: string[];
    showTerminal: boolean;
    showSidebar: boolean;
    activeSidebarPanel: "explorer" | "search" | "git" | "extensions" | "timeline" | "tasks" | "testing" | "outline" | "debug" | "workflow" | "furnace";
    recentFiles?: string[];
  }) => void;

  renameOpenFile: (oldPath: string, newPath: string) => void;

  pendingWriteConfirmation: { filePath: string; content: string } | null;
  setPendingWriteConfirmation: (v: { filePath: string; content: string } | null) => void;
  allowedExternalPaths: Set<string>;
  addAllowedExternalPath: (path: string) => void;

  openDiff: (original: string, modified: string, originalPath: string, modifiedPath: string) => void;
  closeDiff: () => void;
}

export const useCodeStore = create<CodeState>((set, get) => ({
  pinnedRoots: [],
  fileTree: {},
  expandedDirs: {},

  openFiles: [],
  activeFilePath: null,
  recentlyClosed: [],
  recentFiles: [],

  terminals: [],
  activeTerminalId: null,

  showTerminal: true,
  showSidebar: true,
  activeSidebarPanel: "explorer",

  diffFile: null,
  showDiff: false,

  multiFileEdits: [],
  showMultiFileReview: false,
  openMultiFileReview: (edits) =>
    set({ multiFileEdits: edits, showMultiFileReview: true }),
  closeMultiFileReview: () =>
    set({ multiFileEdits: [], showMultiFileReview: false }),
  acceptMultiFileEdit: (index) =>
    set((s) => ({
      multiFileEdits: s.multiFileEdits.map((e, i) =>
        i === index ? { ...e, accepted: true } : e,
      ),
    })),
  rejectMultiFileEdit: (index) =>
    set((s) => ({
      multiFileEdits: s.multiFileEdits.map((e, i) =>
        i === index ? { ...e, accepted: false } : e,
      ),
    })),
  acceptAllMultiFileEdits: () =>
    set((s) => ({
      multiFileEdits: s.multiFileEdits.map((e) =>
        e.accepted === null ? { ...e, accepted: true } : e,
      ),
    })),

  zenModeFilePath: null,
  setZenModeFilePath: (path) => set({ zenModeFilePath: path }),

  splitFilePath: null,
  setSplitFilePath: (path) => set({ splitFilePath: path }),

  cursorPosition: { lineNumber: 1, column: 1 },
  setCursorPosition: (line, col) => set({ cursorPosition: { lineNumber: line, column: col } }),

  currentBranch: "",
  setCurrentBranch: (branch) => set({ currentBranch: branch }),

  showSettings: false,
  setShowSettings: (v) => set({ showSettings: v }),
  toggleSettings: () => set((s) => ({ showSettings: !s.showSettings })),

  showKeybindings: false,
  setShowKeybindings: (v) => set({ showKeybindings: v }),

  quickOpenVisible: false,
  setQuickOpenVisible: (v) => set({ quickOpenVisible: v }),

  commandPaletteVisible: false,
  setCommandPaletteVisible: (v) => set({ commandPaletteVisible: v }),

  symbolSearchVisible: false,
  setSymbolSearchVisible: (v) => set({ symbolSearchVisible: v }),

  localHistoryVisible: false,
  setLocalHistoryVisible: (v) => set({ localHistoryVisible: v }),

  addPinnedRoot: (rootPath) =>
    set((s) => {
      if (s.pinnedRoots.includes(rootPath)) return s;
      return { pinnedRoots: [...s.pinnedRoots, rootPath] };
    }),

  removePinnedRoot: (rootPath) =>
    set((s) => ({
      pinnedRoots: s.pinnedRoots.filter((r) => r !== rootPath),
      fileTree: Object.fromEntries(
        Object.entries(s.fileTree).filter(([k]) => k !== rootPath),
      ),
    })),

  setFileTree: (root, entries) =>
    set((s) => ({
      fileTree: { ...s.fileTree, [root]: entries },
    })),

  toggleDir: (dirPath) =>
    set((s) => ({
      expandedDirs: {
        ...s.expandedDirs,
        [dirPath]: !s.expandedDirs[dirPath],
      },
    })),

  setDirExpanded: (dirPath, expanded) =>
    set((s) => ({
      expandedDirs: { ...s.expandedDirs, [dirPath]: expanded },
    })),

  openFile: (filePath, content, language) => {
    const state = get();
    const recentFiles = [filePath, ...state.recentFiles.filter((p) => p !== filePath)].slice(0, 30);
    const existing = state.openFiles.find((f) => f.path === filePath);
    if (existing) {
      set({ activeFilePath: filePath, recentFiles });
      return;
    }
    const lang = language ?? detectLanguage(filePath);
    set({
      openFiles: [
        ...state.openFiles,
        {
          path: filePath,
          content,
          language: lang,
          dirty: false,
          originalContent: content,
        },
      ],
      activeFilePath: filePath,
      recentFiles,
    });
  },

  closeFile: (filePath) =>
    set((s) => {
      const closedFile = s.openFiles.find((f) => f.path === filePath);
      const idx = s.openFiles.findIndex((f) => f.path === filePath);
      const next = s.openFiles.filter((f) => f.path !== filePath);
      let nextActive = s.activeFilePath;
      if (s.activeFilePath === filePath) {
        nextActive =
          next.length === 0
            ? null
            : (next[Math.min(idx, next.length - 1)]?.path ?? null);
      }
      const recentlyClosed = closedFile
        ? [
            { path: closedFile.path, content: closedFile.content, language: closedFile.language },
            ...s.recentlyClosed,
          ].slice(0, 20)
        : s.recentlyClosed;
      return { openFiles: next, activeFilePath: nextActive, recentlyClosed };
    }),

  reorderFile: (fromIndex, toIndex) =>
    set((s) => {
      const files = [...s.openFiles];
      const [moved] = files.splice(fromIndex, 1);
      files.splice(toIndex, 0, moved);
      return { openFiles: files };
    }),

  setActiveFile: (filePath) => set({ activeFilePath: filePath }),

  updateFileContent: (filePath, content) =>
    set((s) => ({
      openFiles: s.openFiles.map((f) =>
        f.path === filePath
          ? { ...f, content, dirty: content !== f.originalContent }
          : f,
      ),
    })),

  reloadFileContent: (filePath, content) =>
    set((s) => ({
      openFiles: s.openFiles.map((f) =>
        f.path === filePath
          ? { ...f, content, originalContent: content, dirty: false }
          : f,
      ),
    })),

  markFileSaved: (filePath) =>
    set((s) => ({
      openFiles: s.openFiles.map((f) =>
        f.path === filePath
          ? { ...f, dirty: false, originalContent: f.content }
          : f,
      ),
    })),

  reopenClosedFile: () => {
    const state = get();
    if (state.recentlyClosed.length === 0) return;
    const [file, ...rest] = state.recentlyClosed;
    set({ recentlyClosed: rest });
    state.openFile(file.path, file.content, file.language);
  },

  addRecentFile: (filePath) =>
    set((s) => ({
      recentFiles: [filePath, ...s.recentFiles.filter((p) => p !== filePath)].slice(0, 30),
    })),

  clearRecentFiles: () => set({ recentFiles: [] }),

  agentSuggestedFiles: [],
  addAgentSuggestedFile: (path, isNew) =>
    set((s) => {
      const ONE_HOUR = 3600000;
      const now = Date.now();
      const filtered = s.agentSuggestedFiles.filter(
        (f) => now - f.timestamp < ONE_HOUR && f.path !== path,
      );
      return {
        agentSuggestedFiles: [{ path, isNew, timestamp: now }, ...filtered],
      };
    }),
  clearAgentSuggestedFiles: () => set({ agentSuggestedFiles: [] }),

  saveAllFiles: () =>
    set((s) => ({
      openFiles: s.openFiles.map((f) =>
        f.dirty ? { ...f, dirty: false, originalContent: f.content } : f,
      ),
    })),

  revertFile: (filePath, diskContent) =>
    set((s) => ({
      openFiles: s.openFiles.map((f) =>
        f.path === filePath
          ? { ...f, content: diskContent, originalContent: diskContent, dirty: false }
          : f,
      ),
    })),

  addTerminal: (id, title) =>
    set((s) => ({
      terminals: [...s.terminals, { id, title: title ?? `Terminal ${s.terminals.length + 1}` }],
      activeTerminalId: id,
      showTerminal: true,
    })),

  removeTerminal: (id) =>
    set((s) => {
      const next = s.terminals.filter((t) => t.id !== id);
      return {
        terminals: next,
        activeTerminalId:
          s.activeTerminalId === id
            ? (next[next.length - 1]?.id ?? null)
            : s.activeTerminalId,
      };
    }),

  setActiveTerminal: (id) => set({ activeTerminalId: id }),

  setShowTerminal: (show) => set({ showTerminal: show }),
  setShowSidebar: (show) => set({ showSidebar: show }),
  toggleTerminal: () => set((s) => ({ showTerminal: !s.showTerminal })),
  toggleSidebar: () => set((s) => ({ showSidebar: !s.showSidebar })),
  setActiveSidebarPanel: (panel) =>
    set({ activeSidebarPanel: panel, showSidebar: true }),

  restoreSession: (session) =>
    set({
      pinnedRoots: session.pinnedRoots,
      showTerminal: session.showTerminal,
      showSidebar: session.showSidebar,
      activeSidebarPanel: session.activeSidebarPanel,
      recentFiles: session.recentFiles ?? [],
    }),

  renameOpenFile: (oldPath, newPath) =>
    set((s) => ({
      openFiles: s.openFiles.map((f) =>
        f.path === oldPath ? { ...f, path: newPath } : f,
      ),
      activeFilePath: s.activeFilePath === oldPath ? newPath : s.activeFilePath,
    })),

  pendingWriteConfirmation: null,
  setPendingWriteConfirmation: (v) => set({ pendingWriteConfirmation: v }),
  allowedExternalPaths: new Set<string>(),
  addAllowedExternalPath: (path) =>
    set((s) => {
      const next = new Set(s.allowedExternalPaths);
      next.add(path);
      return { allowedExternalPaths: next };
    }),

  openDiff: (original, modified, originalPath, modifiedPath) =>
    set({ diffFile: { original, modified, originalPath, modifiedPath }, showDiff: true }),
  closeDiff: () => set({ diffFile: null, showDiff: false }),
}));
