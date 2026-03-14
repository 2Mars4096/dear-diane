/**
 * Global keyboard shortcut system.
 * Coexists with Code mode's keybindings; uses capture phase so global shortcuts fire first.
 */

export interface GlobalShortcut {
  id: string;
  keys: string; // e.g., "cmd+shift+p", "cmd+,"
  action: () => void;
  description: string;
  preventDefault?: boolean;
}

const registeredShortcuts: GlobalShortcut[] = [];

export function registerGlobalShortcut(shortcut: GlobalShortcut) {
  const existing = registeredShortcuts.findIndex((s) => s.id === shortcut.id);
  if (existing >= 0) registeredShortcuts[existing] = shortcut;
  else registeredShortcuts.push(shortcut);
}

export function unregisterGlobalShortcut(id: string) {
  const idx = registeredShortcuts.findIndex((s) => s.id === id);
  if (idx >= 0) registeredShortcuts.splice(idx, 1);
}

function matchShortcut(e: KeyboardEvent, keys: string): boolean {
  const parts = keys.toLowerCase().split("+");
  const needCmd = parts.includes("cmd") || parts.includes("meta");
  const needCtrl = parts.includes("ctrl");
  const needAlt = parts.includes("alt") || parts.includes("option");
  const needShift = parts.includes("shift");
  const key = parts.filter(
    (p) =>
      !["cmd", "meta", "ctrl", "alt", "option", "shift"].includes(p),
  )[0];

  // On Mac: cmd = metaKey. On Windows/Linux: cmd = ctrlKey.
  const isMac =
    typeof navigator !== "undefined" &&
    /Mac|iPod|iPhone|iPad/.test(navigator.platform);
  const cmdSatisfied = needCmd
    ? isMac
      ? e.metaKey
      : e.ctrlKey
    : true;
  if (!cmdSatisfied) return false;
  // When needCmd && !isMac, we use ctrlKey for cmd, so skip strict needCtrl check.
  if (isMac || !needCmd) {
    if (needCtrl !== e.ctrlKey) return false;
  } else if (needCtrl && !e.ctrlKey) {
    return false; // needCtrl true but ctrlKey false (unusual: cmd+ctrl on Windows)
  }
  if (needAlt !== e.altKey) return false;
  if (needShift !== e.shiftKey) return false;

  const eventKey = e.key.toLowerCase();
  if (key === "`" || key === "backquote") return eventKey === "`";
  if (key === ",") return eventKey === ",";
  return eventKey === key;
}

export function initGlobalShortcuts() {
  const handler = (e: KeyboardEvent) => {
    // Don't intercept if user is typing in an input/textarea (except for special combos)
    const tag = (e.target as HTMLElement)?.tagName;
    const isInput =
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      (e.target as HTMLElement)?.contentEditable === "true";

    for (const shortcut of registeredShortcuts) {
      if (matchShortcut(e, shortcut.keys)) {
        // Allow some shortcuts even in inputs
        if (
          isInput &&
          !shortcut.keys.includes("cmd") &&
          !shortcut.keys.includes("ctrl")
        )
          continue;

        if (shortcut.preventDefault !== false) e.preventDefault();
        shortcut.action();
        return;
      }
    }
  };

  window.addEventListener("keydown", handler, true);
  return () => window.removeEventListener("keydown", handler, true);
}

export function getRegisteredShortcuts(): GlobalShortcut[] {
  return [...registeredShortcuts];
}
