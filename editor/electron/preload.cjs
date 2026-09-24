const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  isElectron: true,
  updates: {
    status: () => ipcRenderer.invoke("updates:status"),
    action: (action) => ipcRenderer.invoke("updates:action", action),
  },
  dialog: {
    openDirectory: () => ipcRenderer.invoke("dialog:openDirectory"),
  },
  fs: {
    droppedFile: (file) => ipcRenderer.invoke("fs:droppedFile", webUtils.getPathForFile(file)),
    droppedDirectory: (file) => ipcRenderer.invoke("fs:droppedDirectory", webUtils.getPathForFile(file)),
    readFile: (filePath) => ipcRenderer.invoke("fs:readFile", filePath),
    writeFile: (filePath, content) => ipcRenderer.invoke("fs:writeFile", filePath, content),
    writeTempAttachment: (payload) => ipcRenderer.invoke("fs:writeTempAttachment", payload),
  },
  shell: {
    openPath: (filePath) => ipcRenderer.invoke("shell:openPath", filePath),
    openExternal: (url) => ipcRenderer.invoke("shell:openExternal", url),
  },
  watch: {
    start: (filePath) => ipcRenderer.invoke("watch:start", filePath),
    stop: (filePath) => ipcRenderer.invoke("watch:stop", filePath),
    onChange: (callback) => {
      const handler = (_event, filePath) => callback(filePath);
      ipcRenderer.on("watch:changed", handler);
      return () => ipcRenderer.removeListener("watch:changed", handler);
    },
  },
});
