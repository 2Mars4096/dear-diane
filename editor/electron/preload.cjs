const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  isElectron: true,

  dialog: {
    openFile: (options) => ipcRenderer.invoke("dialog:openFile", options),
    openDirectory: () => ipcRenderer.invoke("dialog:openDirectory"),
    saveFile: (options) => ipcRenderer.invoke("dialog:saveFile", options),
  },

  fs: {
    readFile: (filePath) => ipcRenderer.invoke("fs:readFile", filePath),
    writeFile: (filePath, content) => ipcRenderer.invoke("fs:writeFile", filePath, content),
    readDir: (dirPath) => ipcRenderer.invoke("fs:readDir", dirPath),
    stat: (filePath) => ipcRenderer.invoke("fs:stat", filePath),
  },
});
