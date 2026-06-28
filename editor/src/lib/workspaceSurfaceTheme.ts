import type { EditorSettings } from "../store/useSettingsStore";

export type WorkspaceSurfaceTheme = EditorSettings["workspaceSurfaceTheme"];
export type WorkspaceSurfaceTone = EditorSettings["workspaceSurfaceTone"];

export const WORKSPACE_SURFACE_THEME_OPTIONS: Array<{
  value: WorkspaceSurfaceTheme;
  label: string;
}> = [
  { value: "factory-worn", label: "Factory Worn" },
  { value: "industrial", label: "Industrial Steel" },
  { value: "original", label: "Original" },
];

export const WORKSPACE_SURFACE_TONE_OPTIONS: Array<{
  value: WorkspaceSurfaceTone;
  label: string;
}> = [
  { value: "system", label: "System" },
  { value: "day", label: "Day" },
  { value: "night", label: "Night" },
];

export function workspaceSurfaceThemeClassName(
  theme: WorkspaceSurfaceTheme,
  tone: WorkspaceSurfaceTone = "system",
) {
  const toneClassName = `dan-surface-tone-${tone}`;
  if (theme === "factory-worn") {
    return `dan-desktop-surface-theme dan-machine-theme dan-factory-worn-theme ${toneClassName}`;
  }
  if (theme === "industrial") {
    return `dan-desktop-surface-theme dan-machine-theme ${toneClassName}`;
  }
  return "";
}
