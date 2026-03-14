/**
 * Orchestrates loading all extension contributions (themes, grammars,
 * snippets, language configurations) when extensions are installed or
 * on startup.
 *
 * Integrates safe-mode gating, trust checks, and failure recording so
 * that misbehaving extensions are progressively isolated.
 */

import { nativeExtension } from "../electronBridge";
import { loadThemeFromExtension } from "./themeLoader";
import { loadGrammarsFromExtension } from "./grammarLoader";
import { loadSnippetsFromExtension } from "./snippetLoader";
import { loadLanguageConfig } from "./langConfigLoader";
import { iconThemeManager } from "./iconThemeLoader";
import {
  isSafeMode,
  recordExtensionFailure,
  isExtensionFailed,
} from "../marketplace/safeMode";
import { getTrustInfo } from "../marketplace/trustModel";
import { useSettingsStore } from "../../store/useSettingsStore";

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function activateExtension(
  extensionPath: string,
): Promise<void> {
  if (isSafeMode()) return;

  const pkgContent = await nativeExtension.readFile(
    `${extensionPath}/package.json`,
  );
  if (!pkgContent) return;

  let pkg: any;
  try {
    pkg = JSON.parse(pkgContent);
  } catch {
    console.warn(`Invalid package.json in ${extensionPath}`);
    return;
  }

  const contributes = pkg.contributes ?? {};
  const extensionId = `${pkg.publisher ?? "unknown"}.${pkg.name ?? "unknown"}`;

  if (isExtensionFailed(extensionId)) {
    console.warn(`Skipping ${extensionId} — disabled after repeated failures`);
    return;
  }

  const trust = getTrustInfo(extensionId);
  if (trust && !trust.trusted) {
    console.warn(`Skipping ${extensionId} — not trusted`);
    return;
  }

  try {
    if (contributes.themes?.length) {
      for (const theme of contributes.themes) {
        await loadThemeFromExtension(extensionPath, theme.path, extensionId);
      }
    }

    if (contributes.grammars?.length) {
      await loadGrammarsFromExtension(extensionPath, contributes.grammars);
    }

    if (contributes.snippets?.length) {
      await loadSnippetsFromExtension(extensionPath, contributes.snippets);
    }

    if (contributes.languages?.length) {
      await loadLanguageConfig(extensionPath, contributes.languages);
    }

    if (contributes.iconThemes?.length) {
      const activeTheme = useSettingsStore.getState().iconTheme;
      const theme = contributes.iconThemes.find((t: any) => t.id === activeTheme) ?? contributes.iconThemes[0];
      if (theme) {
        await iconThemeManager.loadTheme(extensionPath, theme.path);
      }
    }

    console.log(`Activated extension: ${pkg.displayName ?? pkg.name}`);
  } catch (err) {
    const message =
      err instanceof Error ? err.message : String(err);
    recordExtensionFailure(extensionId, message);
    console.warn(`Extension ${extensionId} failed to activate:`, err);
  }
}

export async function activateAllInstalledExtensions(): Promise<void> {
  if (isSafeMode()) {
    console.warn("Safe mode active — skipping all extension activation");
    return;
  }

  const installed = await nativeExtension.listInstalled();
  for (const item of installed) {
    if (item.enabled) {
      try {
        await activateExtension(item.extensionPath);
      } catch (err) {
        console.warn(`Failed to activate extension ${item.id}:`, err);
      }
    }
  }
}
