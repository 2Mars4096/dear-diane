/**
 * Abstraction layer over Electron native APIs.
 * When running in Electron, uses IPC bridge (window.electronAPI).
 * When running in browser, falls back to browser APIs.
 */

interface ElectronAPI {
  isElectron: boolean;
  dialog: {
    openFile: (options: { filters?: Array<{ name: string; extensions: string[] }>; multiple?: boolean }) => Promise<string[] | null>;
    openDirectory: () => Promise<string | null>;
    saveFile: (options: { defaultPath?: string; filters?: Array<{ name: string; extensions: string[] }> }) => Promise<string | null>;
  };
  fs: {
    readFile: (filePath: string) => Promise<string>;
    writeFile: (filePath: string, content: string) => Promise<void>;
    writeTempAttachment: (payload: { name?: string; mimeType?: string; dataUrl: string }) => Promise<string>;
    readDir: (dirPath: string) => Promise<Array<{ name: string; isDirectory: boolean }>>;
    stat: (filePath: string) => Promise<{ size: number; mtime: number; isDirectory: boolean }>;
    mkdir: (dirPath: string) => Promise<void>;
    rename: (oldPath: string, newPath: string) => Promise<void>;
    delete: (filePath: string) => Promise<void>;
    exists: (filePath: string) => Promise<boolean>;
    readGitignore: (rootPath: string) => Promise<string | null>;
  };
  shell: {
    openPath: (filePath: string) => Promise<boolean>;
    run: (opts: { command: string; args: string[]; cwd: string }) => Promise<{ stdout: string; stderr: string; code: number | null }>;
  };
  search: {
    ripgrep: (opts: { query: string; cwd: string; glob?: string; caseSensitive?: boolean; maxResults?: number }) => Promise<string>;
    replaceInFile: (filePath: string, replacements: Array<{ lineNumber: number; matchStart: number; matchEnd: number; replacement: string }>) => Promise<{ success: boolean; error?: string }>;
  };
  git: {
    status: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    diff: (cwd: string, filePath?: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    diffStaged: (cwd: string, filePath?: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    log: (cwd: string, maxCount?: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    stage: (cwd: string, filePath: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    unstage: (cwd: string, filePath: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    commit: (cwd: string, message: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    branch: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    fileShow: (cwd: string, ref: string, filePath: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    push: (cwd: string, remote?: string, branch?: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    pull: (cwd: string, remote?: string, branch?: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    branchList: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    checkout: (cwd: string, branchName: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    createBranch: (cwd: string, branchName: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    remoteInfo: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    aheadBehind: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    blame: (cwd: string, filePath: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    stash: (cwd: string, message?: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    stashList: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    stashPop: (cwd: string, index: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    stashApply: (cwd: string, index: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    stashDrop: (cwd: string, index: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    stashShow: (cwd: string, index: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    cherryPick: (cwd: string, hash: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    logGraph: (cwd: string, maxCount?: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    rebaseCommitList: (cwd: string, count: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    rebaseStart: (cwd: string, entries: Array<{ hash: string; action: string; message: string }>) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    rebaseAbort: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    rebaseContinue: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    rebaseStatus: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    conflictFiles: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    conflictContent: (cwd: string, filePath: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    showBase: (cwd: string, filePath: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    showOurs: (cwd: string, filePath: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    showTheirs: (cwd: string, filePath: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    markResolved: (cwd: string, filePath: string, content: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
  };
  lsp: {
    start: (rootPath: string) => Promise<void>;
    didOpen: (params: { filePath: string; languageId: string; version: number; text: string }) => Promise<void>;
    didChange: (params: { filePath: string; version: number; changes: any[] }) => Promise<void>;
    didSave: (params: { filePath: string; text?: string }) => Promise<void>;
    didClose: (params: { filePath: string }) => Promise<void>;
    completion: (params: { filePath: string; line: number; character: number }) => Promise<any>;
    hover: (params: { filePath: string; line: number; character: number }) => Promise<any>;
    definition: (params: { filePath: string; line: number; character: number }) => Promise<any>;
    references: (params: { filePath: string; line: number; character: number }) => Promise<any>;
    documentSymbol: (params: { filePath: string }) => Promise<any>;
    formatting: (params: { filePath: string; tabSize: number; insertSpaces: boolean }) => Promise<any>;
    codeAction: (params: { filePath: string; range: any; diagnostics: any[] }) => Promise<any>;
    rename: (params: { filePath: string; line: number; character: number; newName: string }) => Promise<any>;
    signatureHelp: (params: { filePath: string; line: number; character: number }) => Promise<any>;
    prepareCallHierarchy: (params: { filePath: string; line: number; character: number }) => Promise<any>;
    incomingCalls: (params: { item: any }) => Promise<any>;
    outgoingCalls: (params: { item: any }) => Promise<any>;
    onDiagnostics: (callback: (data: any) => void) => () => void;
    onNotification: (callback: (data: any) => void) => () => void;
    onLog: (callback: (data: { serverId: string; message: string }) => void) => () => void;
  };
  watch: {
    start: (filePath: string) => Promise<void>;
    stop: (filePath: string) => Promise<void>;
    onChange: (callback: (filePath: string) => void) => () => void;
  };
  terminal: {
    create: (options: { cwd?: string; shell?: string; args?: string[]; env?: Record<string, string> }) => Promise<string>;
    write: (id: string, data: string) => Promise<void>;
    resize: (id: string, cols: number, rows: number) => Promise<void>;
    kill: (id: string) => Promise<void>;
    onData: (callback: (id: string, data: string) => void) => () => void;
    onExit: (callback: (id: string, code: number | null) => void) => () => void;
  };
  extension: {
    readFile: (filePath: string) => Promise<string | null>;
    install: (itemId: string, downloadUrl: string) => Promise<{ extensionPath: string }>;
    uninstall: (itemId: string) => Promise<void>;
    listInstalled: () => Promise<any[]>;
    getManifest: (itemId: string) => Promise<any>;
    importVsix: (filePath: string) => Promise<any>;
  };
  extensionHost: {
    start: () => Promise<{ status: string; extensionCount: number }>;
    stop: () => Promise<{ status: string }>;
    status: () => Promise<{ running: boolean }>;
    executeCommand: (commandId: string, args?: any[]) => Promise<{ result?: any; error?: string }>;
    getCommands: () => Promise<string[]>;
    onEvent: (callback: (data: { event: string; data: any }) => void) => () => void;
  };
  github: {
    checkAvailable: (cwd: string) => Promise<boolean>;
    listPRs: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    getPR: (cwd: string, number: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    createPR: (cwd: string, title: string, body: string, base: string, head: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    listIssues: (cwd: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    prDiff: (cwd: string, number: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    prReview: (cwd: string, number: number, action: string, body: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    prMerge: (cwd: string, number: number, method: string) => Promise<{ stdout: string; stderr: string; code: number | null }>;
    prCheckout: (cwd: string, number: number) => Promise<{ stdout: string; stderr: string; code: number | null }>;
  };
  debug: {
    start: (config: any) => Promise<{ success: boolean; error?: string }>;
    stop: () => Promise<void>;
    restart: () => Promise<{ success: boolean; error?: string }>;
    setBreakpoints: (filePath: string, breakpoints: Array<{ line: number; condition?: string; logMessage?: string }>) => Promise<any>;
    continue: (threadId: number) => Promise<any>;
    next: (threadId: number) => Promise<any>;
    stepIn: (threadId: number) => Promise<any>;
    stepOut: (threadId: number) => Promise<any>;
    pause: (threadId: number) => Promise<any>;
    threads: () => Promise<any>;
    stackTrace: (threadId: number) => Promise<any>;
    scopes: (frameId: number) => Promise<any>;
    variables: (variablesReference: number) => Promise<any>;
    evaluate: (expression: string, frameId?: number) => Promise<any>;
    listProcesses: () => Promise<Array<{ pid: number; name: string; port: number | null; type: string }>>;
    attach: (config: any) => Promise<{ success: boolean; error?: string }>;
    onEvent: (callback: (data: { event: string; body: any }) => void) => () => void;
  };
  skills: {
    scan: () => Promise<SkillEntry[]>;
    readSkill: (skillPath: string) => Promise<string | null>;
    importFromUrl: (url: string) => Promise<{ id: string; path: string }>;
    remove: (skillId: string) => Promise<void>;
    toggleEnabled: (skillId: string) => Promise<boolean>;
    createTemplate: (name: string) => Promise<{ id: string; path: string }>;
  };
  mcp: {
    listInstalled: () => Promise<McpServerEntry[]>;
    install: (serverId: string, config: { command: string; args: string[]; env?: Record<string, string> }) => Promise<McpServerEntry>;
    remove: (serverId: string) => Promise<void>;
    getConfig: (serverId: string) => Promise<McpServerEntry | null>;
    start: (serverId: string) => Promise<{ status: string; error?: string }>;
    stop: (serverId: string) => Promise<{ status: string }>;
    status: () => Promise<Array<{ id: string; status: string; enabled: boolean }>>;
    toggleEnabled: (serverId: string) => Promise<boolean>;
  };
  updater: {
    check: () => Promise<UpdateInfo | null>;
    download: () => Promise<boolean>;
    install: () => void;
    onUpdateAvailable: (cb: (data: UpdateInfo) => void) => () => void;
    onUpToDate: (cb: () => void) => () => void;
    onDownloadProgress: (cb: (data: DownloadProgress) => void) => () => void;
    onUpdateDownloaded: (cb: () => void) => () => void;
    onError: (cb: (msg: string) => void) => () => void;
  };
}

export interface SkillEntry {
  id: string;
  name: string;
  description: string;
  author?: string;
  version?: string;
  triggers?: string[];
  path: string;
  modifiedAt: number;
  enabled: boolean;
}

export interface McpServerEntry {
  id: string;
  name: string;
  command: string;
  args: string[];
  env?: Record<string, string>;
  enabled: boolean;
}

export interface UpdateInfo {
  version: string;
  releaseDate?: string;
  releaseNotes?: string;
}

export interface DownloadProgress {
  percent: number;
  bytesPerSecond: number;
  total: number;
  transferred: number;
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}

export const isElectron = (): boolean => !!window.electronAPI?.isElectron;

export const nativeDialog = {
  async openFile(options: { filters?: Array<{ name: string; extensions: string[] }>; multiple?: boolean } = {}): Promise<string[] | null> {
    if (window.electronAPI) {
      return window.electronAPI.dialog.openFile(options);
    }
    // Browser fallback: use <input type="file">
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      if (options.multiple) input.multiple = true;
      if (options.filters?.length) {
        input.accept = options.filters.flatMap((f) => f.extensions.map((e) => `.${e}`)).join(",");
      }
      input.onchange = () => {
        const files = Array.from(input.files || []);
        resolve(files.map((f) => f.name));
      };
      input.click();
    });
  },

  async openDirectory(): Promise<string | null> {
    if (window.electronAPI) {
      return window.electronAPI.dialog.openDirectory();
    }
    return null;
  },

  async saveFile(options: { defaultPath?: string; filters?: Array<{ name: string; extensions: string[] }> } = {}): Promise<string | null> {
    if (window.electronAPI) {
      return window.electronAPI.dialog.saveFile(options);
    }
    return null;
  },
};

export const nativeFs = {
  async readFile(filePath: string): Promise<string | null> {
    if (window.electronAPI) {
      return window.electronAPI.fs.readFile(filePath);
    }
    return null;
  },

  async writeFile(filePath: string, content: string): Promise<boolean> {
    if (window.electronAPI) {
      await window.electronAPI.fs.writeFile(filePath, content);
      return true;
    }
    return false;
  },

  async writeTempAttachment(payload: {
    name?: string;
    mimeType?: string;
    dataUrl: string;
  }): Promise<string | null> {
    if (window.electronAPI) {
      return window.electronAPI.fs.writeTempAttachment(payload);
    }
    return null;
  },

  async readDir(dirPath: string): Promise<Array<{ name: string; isDirectory: boolean }> | null> {
    if (window.electronAPI) {
      return window.electronAPI.fs.readDir(dirPath);
    }
    return null;
  },

  async mkdir(dirPath: string): Promise<boolean> {
    if (window.electronAPI) {
      await window.electronAPI.fs.mkdir(dirPath);
      return true;
    }
    return false;
  },

  async rename(oldPath: string, newPath: string): Promise<boolean> {
    if (window.electronAPI) {
      await window.electronAPI.fs.rename(oldPath, newPath);
      return true;
    }
    return false;
  },

  async delete(filePath: string): Promise<boolean> {
    if (window.electronAPI) {
      await window.electronAPI.fs.delete(filePath);
      return true;
    }
    return false;
  },

  async exists(filePath: string): Promise<boolean> {
    if (window.electronAPI) {
      return window.electronAPI.fs.exists(filePath);
    }
    return false;
  },

  async stat(filePath: string): Promise<{ size: number; mtime: number; isDirectory: boolean } | null> {
    if (window.electronAPI) {
      return window.electronAPI.fs.stat(filePath);
    }
    return null;
  },

  async readGitignore(rootPath: string): Promise<string | null> {
    if (window.electronAPI) {
      return window.electronAPI.fs.readGitignore(rootPath);
    }
    return null;
  },
};

export type GitResult = { stdout: string; stderr: string; code: number | null };

export const nativeSearch = {
  async ripgrep(opts: { query: string; cwd: string; glob?: string; caseSensitive?: boolean; maxResults?: number }): Promise<string> {
    if (window.electronAPI) return window.electronAPI.search.ripgrep(opts);
    return "";
  },
  async replaceInFile(filePath: string, replacements: Array<{ lineNumber: number; matchStart: number; matchEnd: number; replacement: string }>): Promise<{ success: boolean; error?: string }> {
    if (window.electronAPI) return window.electronAPI.search.replaceInFile(filePath, replacements);
    return { success: false, error: "Not in Electron" };
  },
};

export const nativeGit = {
  async status(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.status(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async diff(cwd: string, filePath?: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.diff(cwd, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async diffStaged(cwd: string, filePath?: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.diffStaged(cwd, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async log(cwd: string, maxCount?: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.log(cwd, maxCount);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async stage(cwd: string, filePath: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.stage(cwd, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async unstage(cwd: string, filePath: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.unstage(cwd, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async commit(cwd: string, message: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.commit(cwd, message);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async branch(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.branch(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async fileShow(cwd: string, ref: string, filePath: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.fileShow(cwd, ref, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async push(cwd: string, remote?: string, branch?: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.push(cwd, remote, branch);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async pull(cwd: string, remote?: string, branch?: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.pull(cwd, remote, branch);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async branchList(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.branchList(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async checkout(cwd: string, branchName: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.checkout(cwd, branchName);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async createBranch(cwd: string, branchName: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.createBranch(cwd, branchName);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async remoteInfo(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.remoteInfo(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async aheadBehind(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.aheadBehind(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async blame(cwd: string, filePath: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.blame(cwd, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async stash(cwd: string, message?: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.stash(cwd, message);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async stashList(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.stashList(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async stashPop(cwd: string, index: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.stashPop(cwd, index);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async stashApply(cwd: string, index: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.stashApply(cwd, index);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async stashDrop(cwd: string, index: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.stashDrop(cwd, index);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async stashShow(cwd: string, index: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.stashShow(cwd, index);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async cherryPick(cwd: string, hash: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.cherryPick(cwd, hash);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async logGraph(cwd: string, maxCount?: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.logGraph(cwd, maxCount);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async rebaseCommitList(cwd: string, count: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.rebaseCommitList(cwd, count);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async rebaseStart(cwd: string, entries: Array<{ hash: string; action: string; message: string }>): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.rebaseStart(cwd, entries);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async rebaseAbort(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.rebaseAbort(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async rebaseContinue(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.rebaseContinue(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async rebaseStatus(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.rebaseStatus(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async conflictFiles(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.conflictFiles(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async conflictContent(cwd: string, filePath: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.conflictContent(cwd, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async showBase(cwd: string, filePath: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.showBase(cwd, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async showOurs(cwd: string, filePath: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.showOurs(cwd, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async showTheirs(cwd: string, filePath: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.showTheirs(cwd, filePath);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async markResolved(cwd: string, filePath: string, content: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.git.markResolved(cwd, filePath, content);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
};

export const nativeTerminal = {
  async create(options: { cwd?: string; shell?: string; args?: string[]; env?: Record<string, string> } = {}): Promise<string | null> {
    if (window.electronAPI) {
      return window.electronAPI.terminal.create(options);
    }
    return null;
  },
  async write(id: string, data: string): Promise<void> {
    window.electronAPI?.terminal.write(id, data);
  },
  async resize(id: string, cols: number, rows: number): Promise<void> {
    window.electronAPI?.terminal.resize(id, cols, rows);
  },
  async kill(id: string): Promise<void> {
    window.electronAPI?.terminal.kill(id);
  },
  onData(callback: (id: string, data: string) => void): () => void {
    if (window.electronAPI) {
      return window.electronAPI.terminal.onData(callback);
    }
    return () => {};
  },
  onExit(callback: (id: string, code: number | null) => void): () => void {
    if (window.electronAPI) {
      return window.electronAPI.terminal.onExit(callback);
    }
    return () => {};
  },
};

export const nativeWatch = {
  async start(filePath: string): Promise<void> {
    window.electronAPI?.watch.start(filePath);
  },
  async stop(filePath: string): Promise<void> {
    window.electronAPI?.watch.stop(filePath);
  },
  onChange(callback: (filePath: string) => void): () => void {
    if (window.electronAPI) {
      return window.electronAPI.watch.onChange(callback);
    }
    return () => {};
  },
};

export const nativeLsp = {
  async start(rootPath: string): Promise<void> {
    if (window.electronAPI) return window.electronAPI.lsp.start(rootPath);
  },
  async didOpen(params: { filePath: string; languageId: string; version: number; text: string }): Promise<void> {
    if (window.electronAPI) return window.electronAPI.lsp.didOpen(params);
  },
  async didChange(params: { filePath: string; version: number; changes: any[] }): Promise<void> {
    if (window.electronAPI) return window.electronAPI.lsp.didChange(params);
  },
  async didSave(params: { filePath: string; text?: string }): Promise<void> {
    if (window.electronAPI) return window.electronAPI.lsp.didSave(params);
  },
  async didClose(params: { filePath: string }): Promise<void> {
    if (window.electronAPI) return window.electronAPI.lsp.didClose(params);
  },
  async completion(params: { filePath: string; line: number; character: number }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.completion(params);
    return null;
  },
  async hover(params: { filePath: string; line: number; character: number }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.hover(params);
    return null;
  },
  async definition(params: { filePath: string; line: number; character: number }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.definition(params);
    return null;
  },
  async references(params: { filePath: string; line: number; character: number }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.references(params);
    return null;
  },
  async documentSymbol(params: { filePath: string }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.documentSymbol(params);
    return null;
  },
  async formatting(params: { filePath: string; tabSize: number; insertSpaces: boolean }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.formatting(params);
    return null;
  },
  async codeAction(params: { filePath: string; range: any; diagnostics: any[] }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.codeAction(params);
    return null;
  },
  async rename(params: { filePath: string; line: number; character: number; newName: string }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.rename(params);
    return null;
  },
  async signatureHelp(params: { filePath: string; line: number; character: number }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.signatureHelp(params);
    return null;
  },
  async prepareCallHierarchy(params: { filePath: string; line: number; character: number }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.prepareCallHierarchy(params);
    return null;
  },
  async incomingCalls(params: { item: any }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.incomingCalls(params);
    return null;
  },
  async outgoingCalls(params: { item: any }): Promise<any> {
    if (window.electronAPI) return window.electronAPI.lsp.outgoingCalls(params);
    return null;
  },
  onDiagnostics(callback: (data: any) => void): () => void {
    if (window.electronAPI) return window.electronAPI.lsp.onDiagnostics(callback);
    return () => {};
  },
  onNotification(callback: (data: any) => void): () => void {
    if (window.electronAPI) return window.electronAPI.lsp.onNotification(callback);
    return () => {};
  },
  onLog(callback: (data: { serverId: string; message: string }) => void): () => void {
    if (window.electronAPI) return window.electronAPI.lsp.onLog(callback);
    return () => {};
  },
};

export const nativeDebug = {
  async start(config: any): Promise<{ success: boolean; error?: string }> {
    if (window.electronAPI) return window.electronAPI.debug.start(config);
    return { success: false, error: "Not in Electron" };
  },
  async stop(): Promise<void> {
    if (window.electronAPI) return window.electronAPI.debug.stop();
  },
  async restart(): Promise<{ success: boolean; error?: string }> {
    if (window.electronAPI) return window.electronAPI.debug.restart();
    return { success: false, error: "Not in Electron" };
  },
  async setBreakpoints(filePath: string, breakpoints: Array<{ line: number; condition?: string; logMessage?: string }>): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.setBreakpoints(filePath, breakpoints);
    return { breakpoints: [] };
  },
  async continue_(threadId: number): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.continue(threadId);
    return null;
  },
  async next(threadId: number): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.next(threadId);
    return null;
  },
  async stepIn(threadId: number): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.stepIn(threadId);
    return null;
  },
  async stepOut(threadId: number): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.stepOut(threadId);
    return null;
  },
  async pause(threadId: number): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.pause(threadId);
    return null;
  },
  async threads(): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.threads();
    return { threads: [] };
  },
  async stackTrace(threadId: number): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.stackTrace(threadId);
    return { stackFrames: [] };
  },
  async scopes(frameId: number): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.scopes(frameId);
    return { scopes: [] };
  },
  async variables(variablesReference: number): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.variables(variablesReference);
    return { variables: [] };
  },
  async evaluate(expression: string, frameId?: number): Promise<any> {
    if (window.electronAPI) return window.electronAPI.debug.evaluate(expression, frameId);
    return null;
  },
  async listProcesses(): Promise<Array<{ pid: number; name: string; port: number | null; type: string }>> {
    if (window.electronAPI) return window.electronAPI.debug.listProcesses();
    return [];
  },
  async attach(config: any): Promise<{ success: boolean; error?: string }> {
    if (window.electronAPI) return window.electronAPI.debug.attach(config);
    return { success: false, error: "Not in Electron" };
  },
  onEvent(callback: (data: { event: string; body: any }) => void): () => void {
    if (window.electronAPI) return window.electronAPI.debug.onEvent(callback);
    return () => {};
  },
};

export const nativeExtension = {
  async readFile(filePath: string): Promise<string | null> {
    if (window.electronAPI) return window.electronAPI.extension.readFile(filePath);
    return null;
  },
  async install(itemId: string, downloadUrl: string): Promise<{ extensionPath: string }> {
    if (window.electronAPI) return window.electronAPI.extension.install(itemId, downloadUrl);
    throw new Error("Extension install requires Electron");
  },
  async uninstall(itemId: string): Promise<void> {
    if (window.electronAPI) return window.electronAPI.extension.uninstall(itemId);
  },
  async listInstalled(): Promise<any[]> {
    if (window.electronAPI) return window.electronAPI.extension.listInstalled();
    return [];
  },
  async getManifest(itemId: string): Promise<any> {
    if (window.electronAPI) return window.electronAPI.extension.getManifest(itemId);
    return null;
  },
  async importVsix(filePath: string): Promise<any> {
    if (window.electronAPI) return window.electronAPI.extension.importVsix(filePath);
    return null;
  },
};

export const nativeExtensionHost = {
  async start(): Promise<{ status: string; extensionCount: number }> {
    if (window.electronAPI) return window.electronAPI.extensionHost.start();
    return { status: "not-available", extensionCount: 0 };
  },
  async stop(): Promise<{ status: string }> {
    if (window.electronAPI) return window.electronAPI.extensionHost.stop();
    return { status: "not-available" };
  },
  async status(): Promise<{ running: boolean }> {
    if (window.electronAPI) return window.electronAPI.extensionHost.status();
    return { running: false };
  },
  async executeCommand(commandId: string, args?: any[]): Promise<{ result?: any; error?: string }> {
    if (window.electronAPI) return window.electronAPI.extensionHost.executeCommand(commandId, args);
    return { error: "Not in Electron" };
  },
  async getCommands(): Promise<string[]> {
    if (window.electronAPI) return window.electronAPI.extensionHost.getCommands();
    return [];
  },
  onEvent(callback: (data: { event: string; data: any }) => void): () => void {
    if (window.electronAPI) return window.electronAPI.extensionHost.onEvent(callback);
    return () => {};
  },
};

export const nativeGitHub = {
  async checkAvailable(cwd: string): Promise<boolean> {
    if (window.electronAPI) return window.electronAPI.github.checkAvailable(cwd);
    return false;
  },
  async listPRs(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.github.listPRs(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async getPR(cwd: string, number: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.github.getPR(cwd, number);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async createPR(cwd: string, title: string, body: string, base: string, head: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.github.createPR(cwd, title, body, base, head);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async listIssues(cwd: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.github.listIssues(cwd);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async prDiff(cwd: string, number: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.github.prDiff(cwd, number);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async prReview(cwd: string, number: number, action: string, body: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.github.prReview(cwd, number, action, body);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async prMerge(cwd: string, number: number, method: string): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.github.prMerge(cwd, number, method);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
  async prCheckout(cwd: string, number: number): Promise<GitResult> {
    if (window.electronAPI) return window.electronAPI.github.prCheckout(cwd, number);
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
};

export const nativeShell = {
  async openPath(filePath: string): Promise<boolean> {
    if (window.electronAPI) {
      return window.electronAPI.shell.openPath(filePath);
    }
    try {
      window.open(encodeURI(`file://${filePath}`), "_blank", "noopener,noreferrer");
      return true;
    } catch {
      return false;
    }
  },

  async run(opts: { command: string; args: string[]; cwd: string }): Promise<{ stdout: string; stderr: string; code: number | null }> {
    if (window.electronAPI) {
      return window.electronAPI.shell.run(opts);
    }
    return { stdout: "", stderr: "Not in Electron", code: -1 };
  },
};

export const nativeSkills = {
  async scan(): Promise<SkillEntry[]> {
    if (window.electronAPI) return window.electronAPI.skills.scan();
    return [];
  },
  async readSkill(skillPath: string): Promise<string | null> {
    if (window.electronAPI) return window.electronAPI.skills.readSkill(skillPath);
    return null;
  },
  async importFromUrl(url: string): Promise<{ id: string; path: string }> {
    if (window.electronAPI) return window.electronAPI.skills.importFromUrl(url);
    throw new Error("Skill import requires Electron");
  },
  async remove(skillId: string): Promise<void> {
    if (window.electronAPI) return window.electronAPI.skills.remove(skillId);
  },
  async toggleEnabled(skillId: string): Promise<boolean> {
    if (window.electronAPI) return window.electronAPI.skills.toggleEnabled(skillId);
    return false;
  },
  async createTemplate(name: string): Promise<{ id: string; path: string }> {
    if (window.electronAPI) return window.electronAPI.skills.createTemplate(name);
    throw new Error("Skill creation requires Electron");
  },
};

export const nativeMcp = {
  async listInstalled(): Promise<McpServerEntry[]> {
    if (window.electronAPI) return window.electronAPI.mcp.listInstalled();
    return [];
  },
  async install(serverId: string, config: { command: string; args: string[]; env?: Record<string, string> }): Promise<McpServerEntry> {
    if (window.electronAPI) return window.electronAPI.mcp.install(serverId, config);
    throw new Error("MCP install requires Electron");
  },
  async remove(serverId: string): Promise<void> {
    if (window.electronAPI) return window.electronAPI.mcp.remove(serverId);
  },
  async getConfig(serverId: string): Promise<McpServerEntry | null> {
    if (window.electronAPI) return window.electronAPI.mcp.getConfig(serverId);
    return null;
  },
  async start(serverId: string): Promise<{ status: string; error?: string }> {
    if (window.electronAPI) return window.electronAPI.mcp.start(serverId);
    return { status: "error", error: "Not in Electron" };
  },
  async stop(serverId: string): Promise<{ status: string }> {
    if (window.electronAPI) return window.electronAPI.mcp.stop(serverId);
    return { status: "not-running" };
  },
  async status(): Promise<Array<{ id: string; status: string; enabled: boolean }>> {
    if (window.electronAPI) return window.electronAPI.mcp.status();
    return [];
  },
  async toggleEnabled(serverId: string): Promise<boolean> {
    if (window.electronAPI) return window.electronAPI.mcp.toggleEnabled(serverId);
    return false;
  },
};

export const nativeUpdater = {
  async check(): Promise<UpdateInfo | null> {
    if (window.electronAPI?.updater) return window.electronAPI.updater.check();
    return null;
  },
  async download(): Promise<boolean> {
    if (window.electronAPI?.updater) return window.electronAPI.updater.download();
    return false;
  },
  install(): void {
    window.electronAPI?.updater?.install();
  },
  onUpdateAvailable(cb: (data: UpdateInfo) => void): () => void {
    if (window.electronAPI?.updater) return window.electronAPI.updater.onUpdateAvailable(cb);
    return () => {};
  },
  onUpToDate(cb: () => void): () => void {
    if (window.electronAPI?.updater) return window.electronAPI.updater.onUpToDate(cb);
    return () => {};
  },
  onDownloadProgress(cb: (data: DownloadProgress) => void): () => void {
    if (window.electronAPI?.updater) return window.electronAPI.updater.onDownloadProgress(cb);
    return () => {};
  },
  onUpdateDownloaded(cb: () => void): () => void {
    if (window.electronAPI?.updater) return window.electronAPI.updater.onUpdateDownloaded(cb);
    return () => {};
  },
  onError(cb: (msg: string) => void): () => void {
    if (window.electronAPI?.updater) return window.electronAPI.updater.onError(cb);
    return () => {};
  },
};
