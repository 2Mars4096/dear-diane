import type { EditorSettings } from "../store/useSettingsStore";

export type WorkspaceSurfaceTheme = EditorSettings["workspaceSurfaceTheme"];

export const WORKSPACE_SURFACE_THEME_OPTIONS: Array<{
  value: WorkspaceSurfaceTheme;
  label: string;
}> = [
  { value: "factory-worn", label: "Factory Worn" },
  { value: "industrial", label: "Industrial Steel" },
  { value: "original", label: "Original" },
];

export function workspaceSurfaceThemeClassName(theme: WorkspaceSurfaceTheme) {
  if (theme === "factory-worn") {
    return "dan-desktop-surface-theme dan-machine-theme dan-factory-worn-theme";
  }
  if (theme === "industrial") {
    return "dan-desktop-surface-theme dan-machine-theme";
  }
  return "";
}
