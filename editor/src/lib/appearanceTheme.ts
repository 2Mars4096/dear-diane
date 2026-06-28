import type { EditorSettings } from "../store/useSettingsStore";

export type AppearanceTheme = EditorSettings["theme"];
export type MonacoTheme = Exclude<AppearanceTheme, "system">;
export type WorkspaceSurfaceTone = EditorSettings["workspaceSurfaceTone"];

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

export function resolveSurfaceAppearance(
  theme: AppearanceTheme,
  surfaceTone: WorkspaceSurfaceTone = "system",
) {
  if (surfaceTone === "day") return "light";
  if (surfaceTone === "night") return "dark";
  return resolveMonacoTheme(theme) === "vs" ? "light" : "dark";
}

export function applyAppearanceTheme(
  theme: AppearanceTheme,
  surfaceTone: WorkspaceSurfaceTone = "system",
) {
  if (typeof document === "undefined") return;

  const root = document.documentElement;
  const resolvedTheme = resolveMonacoTheme(theme);
  const dark = resolvedTheme !== "vs";

  root.classList.toggle("dark", dark);
  root.dataset.appearance = theme;
  root.dataset.resolvedAppearance = dark ? "dark" : "light";
  root.dataset.surfaceAppearance = surfaceTone;
  root.dataset.resolvedSurfaceAppearance = resolveSurfaceAppearance(theme, surfaceTone);
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
