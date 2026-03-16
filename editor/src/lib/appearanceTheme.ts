import type { EditorSettings } from "../store/useSettingsStore";

export type AppearanceTheme = EditorSettings["theme"];
export type MonacoTheme = Exclude<AppearanceTheme, "system">;

function systemPrefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return true;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function resolveMonacoTheme(theme: AppearanceTheme): MonacoTheme {
  if (theme === "system") {
    return systemPrefersDark() ? "vs-dark" : "vs";
  }
  return theme;
}

export function applyAppearanceTheme(theme: AppearanceTheme) {
  if (typeof document === "undefined") return;

  const root = document.documentElement;
  const resolvedTheme = resolveMonacoTheme(theme);
  const dark = resolvedTheme !== "vs";

  root.classList.toggle("dark", dark);
  root.dataset.appearance = theme;
  root.dataset.resolvedAppearance = dark ? "dark" : "light";
  root.style.colorScheme = dark ? "dark" : "light";
}

export function subscribeToSystemAppearance(
  onChange: () => void,
): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => {};
  }

  const media = window.matchMedia("(prefers-color-scheme: dark)");
  if (typeof media.addEventListener === "function") {
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }

  media.addListener(onChange);
  return () => media.removeListener(onChange);
}
