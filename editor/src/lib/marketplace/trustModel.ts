export type Permission =
  | "filesystem:read"
  | "filesystem:write"
  | "network"
  | "subprocess"
  | "debug:attach"
  | "terminal"
  | "clipboard";

export interface TrustInfo {
  extensionId: string;
  requestedPermissions: Permission[];
  grantedPermissions: Permission[];
  trusted: boolean;
  trustedAt?: number;
}

const STORAGE_KEY = "dan-extension-trust";

function loadTrust(): Record<string, TrustInfo> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function saveTrust(trust: Record<string, TrustInfo>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trust));
  } catch {
    /* quota exceeded or localStorage unavailable */
  }
}

export function getTrustInfo(extensionId: string): TrustInfo | null {
  return loadTrust()[extensionId] ?? null;
}

export function grantTrust(extensionId: string, permissions: Permission[]) {
  const trust = loadTrust();
  trust[extensionId] = {
    extensionId,
    requestedPermissions: permissions,
    grantedPermissions: permissions,
    trusted: true,
    trustedAt: Date.now(),
  };
  saveTrust(trust);
}

export function revokeTrust(extensionId: string) {
  const trust = loadTrust();
  delete trust[extensionId];
  saveTrust(trust);
}

export function checkPermission(
  extensionId: string,
  permission: Permission,
): boolean {
  const info = getTrustInfo(extensionId);
  return info?.grantedPermissions.includes(permission) ?? false;
}

/** Best-effort permission inference from a VS Code-style package.json. */
export function inferPermissions(packageJson: any): Permission[] {
  const perms: Permission[] = [];
  const contributes = packageJson?.contributes ?? {};
  const activationEvents: string[] = packageJson?.activationEvents ?? [];

  if (contributes.commands || contributes.menus) perms.push("clipboard");
  if (activationEvents.some((e: string) => e.includes("file")))
    perms.push("filesystem:read");
  if (packageJson?.extensionKind === "workspace") {
    perms.push("filesystem:read", "filesystem:write");
  }

  return [...new Set(perms)];
}
