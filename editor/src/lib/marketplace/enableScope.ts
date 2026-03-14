type Scope = "global" | "workspace";

interface EnableConfig {
  global: boolean;
  workspaces: Record<string, boolean>;
}

const STORAGE_KEY = "dan-extension-enable-scope";

function loadConfig(): Record<string, EnableConfig> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function isExtensionEnabled(
  extensionId: string,
  workspaceId: string,
): boolean {
  const config = loadConfig();
  const ext = config[extensionId];
  if (!ext) return true;

  if (ext.workspaces[workspaceId] !== undefined)
    return ext.workspaces[workspaceId];

  return ext.global;
}

export function setExtensionEnabled(
  extensionId: string,
  scope: Scope,
  enabled: boolean,
  workspaceId?: string,
) {
  const config = loadConfig();
  if (!config[extensionId])
    config[extensionId] = { global: true, workspaces: {} };

  if (scope === "global") {
    config[extensionId].global = enabled;
  } else if (workspaceId) {
    config[extensionId].workspaces[workspaceId] = enabled;
  }

  localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
}
