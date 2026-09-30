import { contextBridge, ipcRenderer, webUtils } from "electron";

// Keep the native surface behind the page matched during theme changes and teardown.
window.addEventListener("DOMContentLoaded", () => {
  if (process.platform === "darwin") {
    document.documentElement.dataset.nativeTitlebar = "mac";
    const caption = (target: EventTarget | null) => {
      if (!(target instanceof Element) || target.closest('button,a,input,select,textarea,summary,dialog,[role="button"],[contenteditable]')) return null;
      return target.closest<HTMLElement>(".dan-native-window .wb-header, .dan-native-window > .dan-workspace-header");
    };
    let pointer: { id: number; element: HTMLElement; x: number; y: number; moved: boolean } | null = null;
    let dragged = false;
    const end = () => {
      if (!pointer) return;
      const active = pointer;
      pointer = null;
      dragged = active.moved;
      if (active.element.hasPointerCapture(active.id)) active.element.releasePointerCapture(active.id);
      ipcRenderer.send("window:titlebar", "end");
    };
    document.addEventListener("pointerdown", (event) => {
      const element = caption(event.target);
      if (!element || event.button !== 0 || event.pointerType !== "mouse") return;
      end();
      dragged = false;
      pointer = { id: event.pointerId, element, x: event.screenX, y: event.screenY, moved: false };
      element.setPointerCapture(event.pointerId);
      ipcRenderer.send("window:titlebar", "begin");
    });
    document.addEventListener("pointermove", (event) => {
      if (!pointer || pointer.id !== event.pointerId) return;
      if (!(event.buttons & 1)) { end(); return; }
      pointer.moved ||= Math.hypot(event.screenX - pointer.x, event.screenY - pointer.y) >= 4;
      ipcRenderer.send("window:titlebar", "move");
    });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"] as const) {
      document.addEventListener(type, (event) => { if (pointer?.id === event.pointerId) end(); });
    }
    window.addEventListener("blur", end);
    document.addEventListener("dblclick", (event) => {
      if (event.button === 0 && !dragged && caption(event.target)) ipcRenderer.send("window:titlebar", "toggle");
    });
  }
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
  attention: {
    show: (target: { thread: string; worker?: string; title: string }) => ipcRenderer.invoke("attention:show", target),
    onOpen: (callback: (target: { thread: string; worker?: string }) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, target: { thread: string; worker?: string }) => callback(target);
      ipcRenderer.on("attention:open", handler);
      return () => ipcRenderer.removeListener("attention:open", handler);
    },
  },
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
