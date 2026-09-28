const WORKSPACE_ROOT_ALIASES = new Map([
  [
    "/Volumes/data/Dropbox/Projects/deep-agent-network",
    "/Users/lizhi/Downloads/local_projects/deep-agent-network",
  ],
]);

export function fileName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

export function normalizeRootPath(path: string) {
  const trimmed = path.trim();
  if (trimmed === "/" || /^[a-z]:[\\/]$/i.test(trimmed)) return trimmed;
  const normalized = trimmed.replace(/[\\/]+$/, "");
  return WORKSPACE_ROOT_ALIASES.get(normalized) ?? normalized;
}
