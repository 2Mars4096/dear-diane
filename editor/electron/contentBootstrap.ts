import path from "node:path";
import os from "node:os";

interface ResolveContentBootstrapRootsOptions {
  env?: NodeJS.ProcessEnv;
  cwd?: string;
  appPath?: string;
  homedir?: string;
}

function expandHome(value: string, homedir: string) {
  if (value.startsWith("~")) {
    return path.join(homedir, value.slice(1));
  }
  return value;
}

function normalizeCandidate(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return null;
  return path.normalize(trimmed);
}

export function resolveContentBootstrapRoots(
  options: ResolveContentBootstrapRootsOptions = {},
): string[] {
  const env = options.env ?? process.env;
  const cwd = options.cwd ?? process.cwd();
  const appPath = options.appPath ?? cwd;
  const homedir = options.homedir ?? os.homedir();
  const configured = String(env.DAN_DEFAULT_CONTENT_ROOTS ?? "")
    .split(path.delimiter)
    .map((value) => expandHome(value, homedir))
    .map(normalizeCandidate)
    .filter((value): value is string => Boolean(value));

  const localNamedCandidates = [
    path.join("/Volumes/data/Dropbox/Projects", "my-knowledge-base"),
    path.join(homedir, "Dropbox", "Projects", "my-knowledge-base"),
    path.join(homedir, "Projects", "my-knowledge-base"),
  ]
    .map(normalizeCandidate)
    .filter((value): value is string => Boolean(value));

  const heuristicCandidates = [
    path.resolve(cwd, "..", "my-knowledge-base"),
    path.resolve(cwd, "..", "..", "my-knowledge-base"),
    path.resolve(appPath, "..", "my-knowledge-base"),
    path.resolve(appPath, "..", "..", "my-knowledge-base"),
  ]
    .map(normalizeCandidate)
    .filter((value): value is string => Boolean(value));

  return [
    ...new Set([...configured, ...localNamedCandidates, ...heuristicCandidates]),
  ];
}
