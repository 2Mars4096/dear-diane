import type { BrowserWindow } from "electron";

export const WINDOW_BACKGROUND = "#12100F";

/** Keep native reveal requests behind the first themed document and paint. */
export function prepareWindowAppearance(window: BrowserWindow) {
  let painted = false;
  let loaded = false;
  let requested = true;
  const show = () => {
    if (!requested || !painted || !loaded || window.isDestroyed()) return;
    requested = false;
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  };
  window.once("ready-to-show", () => { painted = true; show(); });
  window.webContents.once("did-finish-load", async () => {
    try {
      const color: unknown = await window.webContents.executeJavaScript(
        'getComputedStyle(document.documentElement).getPropertyValue("--dan-wb-background").trim()',
      );
      if (!window.isDestroyed() && typeof color === "string" && /^#[\da-f]{6}$/i.test(color)) {
        window.setBackgroundColor(color);
      }
    } catch { /* Keep the non-white native fallback if the document is closing. */ }
    loaded = true;
    show();
  });
  return () => { requested = true; show(); };
}
