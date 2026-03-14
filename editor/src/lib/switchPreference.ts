export type SwitchAction = "always" | "ask" | "never";

interface SwitchPreferences {
  [modeKey: string]: SwitchAction;
}

const STORAGE_KEY = "dan-switch-preferences";

function loadPreferences(): SwitchPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function savePreferences(prefs: SwitchPreferences): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // localStorage unavailable
  }
}

export function getSwitchPreference(mode: string): SwitchAction {
  const prefs = loadPreferences();
  return prefs[mode] ?? "ask";
}

export function setSwitchPreference(mode: string, action: SwitchAction): void {
  const prefs = loadPreferences();
  prefs[mode] = action;
  savePreferences(prefs);
}
