export type ShellPlatform = "win32" | "darwin" | "linux";

type EnvLike = Record<string, string | undefined>;

function _normalizePlatform(rawPlatform?: string): ShellPlatform {
  const value = String(rawPlatform || "").toLowerCase();
  if (value.includes("mac") || value.includes("darwin")) return "darwin";
  if (value.includes("win")) return "win32";
  return "linux";
}

export function detectCurrentPlatform(): ShellPlatform {
  if (typeof navigator === "undefined") return "linux";
  return _normalizePlatform(navigator.platform || navigator.userAgent);
}

export function getDefaultShellPath(
  rawPlatform?: string,
  env: EnvLike = {},
): string {
  const platform = _normalizePlatform(rawPlatform);
  if (platform === "win32") {
    return env.ComSpec || "powershell.exe";
  }
  const shell = (env.SHELL || "").trim();
  if (shell) return shell;
  return platform === "darwin" ? "/bin/zsh" : "/bin/bash";
}

export function getDefaultShellArgs(
  shellPath: string,
  rawPlatform?: string,
): string[] {
  const platform = _normalizePlatform(rawPlatform);
  if (platform === "win32") return [];
  const shellName = shellPath.replace(/\\/g, "/").toLowerCase().split("/").pop() || "";
  if (["zsh", "bash", "sh"].includes(shellName)) {
    return ["-l"];
  }
  return [];
}

export function getBuiltinTerminalProfiles(rawPlatform?: string): Array<{
  id: string;
  name: string;
  shell: string;
  args?: string[];
  isDefault?: boolean;
}> {
  const platform = _normalizePlatform(rawPlatform);
  if (platform === "win32") {
    return [
      { id: "powershell", name: "PowerShell", shell: "powershell.exe", isDefault: true },
      { id: "pwsh", name: "PowerShell 7", shell: "pwsh.exe" },
      { id: "cmd", name: "Command Prompt", shell: "cmd.exe" },
    ];
  }
  if (platform === "darwin") {
    return [
      { id: "zsh", name: "zsh", shell: "/bin/zsh", args: ["-l"], isDefault: true },
      { id: "bash", name: "bash", shell: "/bin/bash", args: ["-l"] },
      { id: "sh", name: "sh", shell: "/bin/sh", args: ["-l"] },
    ];
  }
  return [
    { id: "bash", name: "bash", shell: "/bin/bash", args: ["-l"], isDefault: true },
    { id: "sh", name: "sh", shell: "/bin/sh", args: ["-l"] },
    { id: "zsh", name: "zsh", shell: "/bin/zsh", args: ["-l"] },
  ];
}

