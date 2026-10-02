import { ipcMain, screen, BrowserWindow } from "electron";

/** Mac custom caption areas do not reliably receive AppKit double-click zoom. */
export function registerWindowControls(getWindow: (sender: Electron.WebContents) => BrowserWindow | null) {
  let drag: { window: BrowserWindow; cursor: Electron.Point; bounds: Electron.Rectangle; moving: boolean } | null = null;
  ipcMain.on("window:titlebar", (event, action: unknown) => {
    const window = getWindow(event.sender);
    if (process.platform !== "darwin" || !window || window.isDestroyed()
      || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) return;
    if (action === "end") { drag = null; return; }
    if (window.isFullScreen()) { drag = null; return; }
    if (action === "toggle") {
      drag = null;
      if (window.isMaximized()) window.unmaximize();
      else window.maximize();
    } else if (action === "begin") {
      drag = { window, cursor: screen.getCursorScreenPoint(), bounds: window.getBounds(), moving: false };
    } else if (action === "move" && drag?.window === window) {
      if (!window.isFocused()) { drag = null; return; }
      const cursor = screen.getCursorScreenPoint();
      const dx = cursor.x - drag.cursor.x, dy = cursor.y - drag.cursor.y;
      if (!drag.moving && Math.hypot(dx, dy) < 4) return;
      if (!drag.moving && window.isMaximized()) {
        const fraction = Math.max(0, Math.min(1, (drag.cursor.x - drag.bounds.x) / drag.bounds.width));
        window.unmaximize();
        const restored = window.getBounds();
        drag.bounds = { ...restored, x: Math.round(drag.cursor.x - restored.width * fraction), y: drag.bounds.y };
      }
      drag.moving = true;
      window.setPosition(Math.round(drag.bounds.x + dx), Math.round(drag.bounds.y + dy));
    }
  });
}
