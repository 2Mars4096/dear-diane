/**
 * Load VS Code color themes from extensions and apply them to Monaco + app.
 *
 * Themes are converted to Monaco's IStandaloneThemeData format, and workbench
 * colors are mapped to CSS custom properties so the shell / panels can pick
 * them up without coupling to Monaco internals.
 */

import { loader } from "@monaco-editor/react";
import { nativeExtension } from "../electronBridge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface VSCodeTheme {
  name: string;
  type: "dark" | "light" | "hcDark" | "hcLight";
  colors: Record<string, string>;
  tokenColors: Array<{
    name?: string;
    scope: string | string[];
    settings: {
      foreground?: string;
      fontStyle?: string;
    };
  }>;
  include?: string;
}

export interface AvailableTheme {
  id: string;
  label: string;
  type: string;
  extensionId: string;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const loadedThemes: AvailableTheme[] = [];

const WORKBENCH_COLOR_MAP: Record<string, string> = {
  "editor.background": "--dan-editor-bg",
  "editor.foreground": "--dan-editor-fg",
  "sideBar.background": "--dan-sidebar-bg",
  "activityBar.background": "--dan-activitybar-bg",
  "statusBar.background": "--dan-statusbar-bg",
  "titleBar.activeBackground": "--dan-titlebar-bg",
  "tab.activeBackground": "--dan-tab-active-bg",
  "tab.inactiveBackground": "--dan-tab-inactive-bg",
  "terminal.background": "--dan-terminal-bg",
  "panel.background": "--dan-panel-bg",
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function loadThemeFromExtension(
  extensionPath: string,
  themePath: string,
  extensionId = "",
): Promise<void> {
  const content = await nativeExtension.readFile(
    `${extensionPath}/${themePath}`,
  );
  if (!content) return;

  let theme: VSCodeTheme;
  try {
    // VS Code theme files may use JSONC (JSON with comments) — strip them
    const stripped = content.replace(
      /\/\/.*$|\/\*[\s\S]*?\*\//gm,
      "",
    );
    theme = JSON.parse(stripped);
  } catch {
    console.warn(`Failed to parse theme at ${themePath}`);
    return;
  }

  const monaco = await loader.init();

  const base = theme.type === "light" ? "vs" : "vs-dark";

  const rules = (theme.tokenColors ?? []).flatMap((tc) => {
    const scopes = Array.isArray(tc.scope) ? tc.scope : [tc.scope];
    return scopes
      .filter((s): s is string => typeof s === "string" && s.length > 0)
      .map((scope) => ({
        token: scope,
        foreground: tc.settings.foreground?.replace("#", ""),
        fontStyle: tc.settings.fontStyle,
      }));
  });

  const themeId = `ext-${theme.name.replace(/\s+/g, "-").toLowerCase()}`;

  monaco.editor.defineTheme(themeId, {
    base,
    inherit: true,
    rules,
    colors: theme.colors ?? {},
  });

  // Apply workbench colors to CSS custom properties
  if (theme.colors) {
    const root = document.documentElement;
    for (const [vsKey, cssVar] of Object.entries(WORKBENCH_COLOR_MAP)) {
      if (theme.colors[vsKey]) {
        root.style.setProperty(cssVar, theme.colors[vsKey]);
      }
    }
  }

  loadedThemes.push({
    id: themeId,
    label: theme.name,
    type: theme.type ?? "dark",
    extensionId,
  });
}

export function getAvailableThemes(): AvailableTheme[] {
  return [...loadedThemes];
}
