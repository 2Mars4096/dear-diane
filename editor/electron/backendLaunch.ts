import os from "node:os";
import path from "node:path";

export interface BackendLaunchOptions {
  env?: NodeJS.ProcessEnv;
  graphsDir: string;
}

function expandHome(rawPath: string): string {
  if (rawPath === "~") return os.homedir();
  if (rawPath.startsWith(`~${path.sep}`) || rawPath.startsWith("~/")) {
    return path.join(os.homedir(), rawPath.slice(2));
  }
  return rawPath;
}

function normalizePath(rawPath: string): string {
  return path.resolve(expandHome(rawPath));
}

export function resolveBackendWorkspaceRoot(
  options: BackendLaunchOptions,
): string {
  const env = options.env ?? process.env;
  const configured = String(env.DAN_WORKSPACE_ROOT ?? "").trim();
  if (configured) {
    return normalizePath(configured);
  }
  return path.dirname(normalizePath(options.graphsDir));
}

export function buildBackendLaunchEnv(
  options: BackendLaunchOptions,
): NodeJS.ProcessEnv {
  const env = options.env ?? process.env;
  const graphsDir = normalizePath(options.graphsDir);
  const workspaceRoot = resolveBackendWorkspaceRoot({
    env,
    graphsDir,
  });
  return {
    ...env,
    DAN_GRAPHS_DIR: graphsDir,
    DAN_WORKSPACE_ROOT: workspaceRoot,
  };
}
