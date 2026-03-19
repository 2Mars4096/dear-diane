import { spawnSync, type SpawnSyncReturns } from "node:child_process";

type SpawnLike = (
  command: string,
  args?: readonly string[],
  options?: { stdio?: "ignore" },
) => SpawnSyncReturns<Buffer>;

export function commandExists(
  cmd: string,
  options: {
    platform?: NodeJS.Platform;
    spawn?: SpawnLike;
  } = {},
): boolean {
  const platform = options.platform ?? process.platform;
  const run = options.spawn ?? spawnSync;
  const probe = platform === "win32" ? "where" : "which";

  try {
    const result = run(probe, [cmd], { stdio: "ignore" });
    if (result.status === 0) return true;
    if (platform === "win32" && !cmd.toLowerCase().endsWith(".exe")) {
      return run(probe, [`${cmd}.exe`], { stdio: "ignore" }).status === 0;
    }
    return false;
  } catch {
    return false;
  }
}
