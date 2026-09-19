import { contextBridge, ipcRenderer, webUtils } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  isElectron: true,
  dialog: {
    openDirectory: () => ipcRenderer.invoke("dialog:openDirectory"),
  },
  fs: {
    droppedDirectory: (file: File) => ipcRenderer.invoke("fs:droppedDirectory", webUtils.getPathForFile(file)),
    readFile: (filePath: string) => ipcRenderer.invoke("fs:readFile", filePath),
    writeFile: (filePath: string, content: string) =>
      ipcRenderer.invoke("fs:writeFile", filePath, content),
    writeTempAttachment: (payload: {
      name?: string;
      mimeType?: string;
      dataUrl: string;
    }) => ipcRenderer.invoke("fs:writeTempAttachment", payload),
  },
  shell: {
    openPath: (filePath: string) => ipcRenderer.invoke("shell:openPath", filePath),
    openExternal: (url: string) => ipcRenderer.invoke("shell:openExternal", url),
  },
  watch: {
    start: (filePath: string) => ipcRenderer.invoke("watch:start", filePath),
    stop: (filePath: string) => ipcRenderer.invoke("watch:stop", filePath),
    onChange: (callback: (filePath: string) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, filePath: string) =>
        callback(filePath);
      ipcRenderer.on("watch:changed", handler);
      return () => ipcRenderer.removeListener("watch:changed", handler);
    },
  },
});
