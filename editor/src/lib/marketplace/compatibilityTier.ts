export type CompatibilityTier = "full" | "partial" | "static" | "unsupported";

export interface TierResult {
  tier: CompatibilityTier;
  supportedFeatures: string[];
  unsupportedFeatures: string[];
  reason: string;
}

export function classifyExtension(packageJson: any): TierResult {
  const contributes = packageJson.contributes ?? {};
  const main = packageJson.main ?? packageJson.browser;

  const supported: string[] = [];
  const unsupported: string[] = [];

  if (contributes.themes) supported.push("themes");
  if (contributes.grammars) supported.push("syntax highlighting");
  if (contributes.snippets) supported.push("snippets");
  if (contributes.languages) supported.push("language support");
  if (contributes.iconThemes) supported.push("icon themes");

  if (!main) {
    if (supported.length > 0) {
      return {
        tier: "static",
        supportedFeatures: supported,
        unsupportedFeatures: [],
        reason: "Static contributions only",
      };
    }
    return {
      tier: "unsupported",
      supportedFeatures: [],
      unsupportedFeatures: ["no contributions"],
      reason: "No contributions found",
    };
  }

  if (contributes.views) unsupported.push("custom views");
  if (contributes.viewsContainers) unsupported.push("view containers");
  if (contributes.debuggers) supported.push("debuggers (via native DAP)");
  if (contributes.taskDefinitions) unsupported.push("task definitions");
  if (contributes.customEditors) unsupported.push("custom editors");
  if (contributes.webviewViews) unsupported.push("webview views");
  if (contributes.notebooks) unsupported.push("notebook renderers");
  if (contributes.terminal) unsupported.push("terminal profiles");
  if (contributes.walkthroughs) unsupported.push("walkthroughs");

  if (contributes.commands) supported.push("commands");
  if (contributes.keybindings) supported.push("keybindings");
  if (contributes.menus) supported.push("menus (partial)");
  if (contributes.configuration) supported.push("settings");

  if (unsupported.length === 0) {
    return {
      tier: "full",
      supportedFeatures: supported,
      unsupportedFeatures: [],
      reason: "All features supported",
    };
  }

  if (supported.length > 0) {
    return {
      tier: "partial",
      supportedFeatures: supported,
      unsupportedFeatures: unsupported,
      reason: `${unsupported.length} unsupported feature${unsupported.length > 1 ? "s" : ""}`,
    };
  }

  return {
    tier: "unsupported",
    supportedFeatures: [],
    unsupportedFeatures: unsupported,
    reason: "Requires unsupported APIs",
  };
}

export function tierBadgeColor(tier: CompatibilityTier): string {
  switch (tier) {
    case "full":
      return "bg-green-700/60 text-green-200";
    case "partial":
      return "bg-yellow-700/60 text-yellow-200";
    case "static":
      return "bg-gray-600/60 text-gray-300";
    case "unsupported":
      return "bg-red-800/60 text-red-300";
  }
}

export function tierLabel(tier: CompatibilityTier): string {
  switch (tier) {
    case "full":
      return "Full";
    case "partial":
      return "Partial";
    case "static":
      return "Static";
    case "unsupported":
      return "Unsupported";
  }
}
