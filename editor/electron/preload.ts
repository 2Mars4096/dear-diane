import { contextBridge, ipcRenderer, webUtils } from "electron";

// Keep the native surface behind the page matched during theme changes and teardown.
window.addEventListener("DOMContentLoaded", () => {
  let previous = "";
  const syncBackground = () => {
    const color = getComputedStyle(document.documentElement).getPropertyValue("--dan-wb-background").trim();
    if (color !== previous && /^#[\da-f]{6}$/i.test(color)) {
      previous = color;
      ipcRenderer.send("window:background", color);
    }
  };
  new MutationObserver(syncBackground).observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
  syncBackground();
}, { once: true });

contextBridge.exposeInMainWorld("electronAPI", {
  isElectron: true,
  updates: {
    status: () => ipcRenderer.invoke("updates:status"),
    action: (action: string) => ipcRenderer.invoke("updates:action", action),
  },
  dialog: {
    openDirectory: () => ipcRenderer.invoke("dialog:openDirectory"),
  },
  fs: {
    droppedFile: (file: File) => ipcRenderer.invoke("fs:droppedFile", webUtils.getPathForFile(file)),
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
    fileLink: (request: { href: string; root: string; menu?: boolean }) => ipcRenderer.invoke("shell:fileLink", request),
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
