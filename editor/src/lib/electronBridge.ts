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
    readDir: (dirPath: string) => Promise<Array<{ name: string; isDirectory: boolean }>>;
    stat: (filePath: string) => Promise<{ size: number; mtime: number; isDirectory: boolean }>;
  };
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

  async readDir(dirPath: string): Promise<Array<{ name: string; isDirectory: boolean }> | null> {
    if (window.electronAPI) {
      return window.electronAPI.fs.readDir(dirPath);
    }
    return null;
  },
};
