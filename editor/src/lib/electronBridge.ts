/**
 * Narrow native bridge used by the Work/Notes desktop surface.
 * Browser builds keep safe no-op fallbacks for every operation.
 */

export interface DesktopUpdateState {
  github?: { status: string; login: string; code: string; message: string };
  localBuildAvailable?: boolean;
  phase: string; currentVersion: string; version: string; source: string;
  message: string; percent: number; releaseConfigured: boolean; localSupported: boolean;
}

interface ElectronAPI {
  attention?: {
    show: (target: { thread: string; worker?: string; title: string }) => Promise<void>;
    onOpen: (callback: (target: { thread: string; worker?: string }) => void) => () => void;
  };
  updates?: {
    status: () => Promise<DesktopUpdateState>;
    action: (action: "check" | "choose" | "install-local" | "download" | "install" | "github-sign-in" | "github-cancel" | "github-status") => Promise<DesktopUpdateState>;
  };
  isElectron: boolean;
  dialog: {
    openDirectory: () => Promise<string | null>;
  };
  fs: {
    droppedFile?: (file: File) => Promise<string | null>;
    droppedDirectory?: (file: File) => Promise<string | null>;
    readFile: (filePath: string) => Promise<string | null>;
    writeFile: (filePath: string, content: string) => Promise<void>;
    writeTempAttachment: (payload: {
      name?: string;
      mimeType?: string;
      dataUrl: string;
    }) => Promise<string>;
  };
  shell: {
    fileLink?: (request: { href: string; root: string; menu?: boolean; resolveOnly?: boolean }) => Promise<{ ok: boolean; error?: string; path?: string }>;
    openPath: (filePath: string) => Promise<boolean>;
    openExternal: (url: string) => Promise<boolean>;
  };
  watch: {
    start: (filePath: string) => Promise<void>;
    stop: (filePath: string) => Promise<void>;
    onChange: (callback: (filePath: string) => void) => () => void;
  };
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}

export const isElectron = (): boolean => Boolean(window.electronAPI?.isElectron);

export const nativeDialog = {
  async openDirectory(): Promise<string | null> {
    return window.electronAPI?.dialog.openDirectory() ?? null;
  },
};

export const nativeFs = {
  async droppedFile(file: File): Promise<string | null> {
    return window.electronAPI?.fs.droppedFile?.(file) ?? null;
  },
  async droppedDirectory(file: File): Promise<string | null> {
    return window.electronAPI?.fs.droppedDirectory?.(file) ?? null;
  },
  async readFile(filePath: string): Promise<string | null> {
    return window.electronAPI?.fs.readFile(filePath) ?? null;
  },

  async writeFile(filePath: string, content: string): Promise<boolean> {
    if (!window.electronAPI) return false;
    await window.electronAPI.fs.writeFile(filePath, content);
    return true;
  },

  async writeTempAttachment(payload: {
    name?: string;
    mimeType?: string;
    dataUrl: string;
  }): Promise<string | null> {
    return window.electronAPI?.fs.writeTempAttachment(payload) ?? null;
  },
};

export const nativeWatch = {
  async start(filePath: string): Promise<void> {
    await window.electronAPI?.watch.start(filePath);
  },

  async stop(filePath: string): Promise<void> {
    await window.electronAPI?.watch.stop(filePath);
  },

  onChange(callback: (filePath: string) => void): () => void {
    return window.electronAPI?.watch.onChange(callback) ?? (() => {});
  },
};

export const nativeShell = {
  async openPath(filePath: string): Promise<boolean> {
    if (window.electronAPI) return window.electronAPI.shell.openPath(filePath);
    try {
      window.open(encodeURI(`file://${filePath}`), "_blank", "noopener,noreferrer");
      return true;
    } catch {
      return false;
    }
  },

  async openExternal(url: string): Promise<boolean> {
    if (window.electronAPI) return window.electronAPI.shell.openExternal(url);
    try {
      window.open(url, "_blank", "noopener,noreferrer");
      return true;
    } catch {
      return false;
    }
  },
};
