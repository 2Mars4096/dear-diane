import { contextBridge, ipcRenderer } from "electron";

const previewBridgePreloadPath = `${__dirname}${
  __dirname.endsWith("/") || __dirname.endsWith("\\") ? "" : "/"
}previewBridgePreload.cjs`;

/**
 * Exposes native APIs to the renderer process through a safe bridge.
 * The renderer accesses these via window.electronAPI.
 * When running as a web app (no Electron), window.electronAPI is undefined
 * and the app falls back to browser APIs.
 */
contextBridge.exposeInMainWorld("electronAPI", {
  isElectron: true,

  dialog: {
    openFile: (options: { filters?: Array<{ name: string; extensions: string[] }>; multiple?: boolean }) =>
      ipcRenderer.invoke("dialog:openFile", options),
    openDirectory: () => ipcRenderer.invoke("dialog:openDirectory"),
    saveFile: (options: { defaultPath?: string; filters?: Array<{ name: string; extensions: string[] }> }) =>
      ipcRenderer.invoke("dialog:saveFile", options),
  },

  fs: {
    readFile: (filePath: string) => ipcRenderer.invoke("fs:readFile", filePath),
    writeFile: (filePath: string, content: string) => ipcRenderer.invoke("fs:writeFile", filePath, content),
    writeTempAttachment: (payload: { name?: string; mimeType?: string; dataUrl: string }) =>
      ipcRenderer.invoke("fs:writeTempAttachment", payload),
    readDir: (dirPath: string) => ipcRenderer.invoke("fs:readDir", dirPath),
    stat: (filePath: string) => ipcRenderer.invoke("fs:stat", filePath),
  },

  shell: {
    openPath: (filePath: string) => ipcRenderer.invoke("shell:openPath", filePath),
    openExternal: (url: string) => ipcRenderer.invoke("shell:openExternal", url),
  },

  contentPreview: {
    bridgePreloadPath: previewBridgePreloadPath,
    getStatus: (projectRoot: string) => ipcRenderer.invoke("contentPreview:getStatus", projectRoot),
    start: (projectRoot: string) => ipcRenderer.invoke("contentPreview:start", projectRoot),
    stop: (projectRoot: string) => ipcRenderer.invoke("contentPreview:stop", projectRoot),
    restart: (projectRoot: string) => ipcRenderer.invoke("contentPreview:restart", projectRoot),
  },

  contentBootstrap: {
    getRoots: () => ipcRenderer.invoke("content:getBootstrapRoots"),
  },
});
