import { nativeFs } from "../../lib/electronBridge";
import {
  type ContentPageSummary,
  type ContentProjectCandidate,
  buildContentPageSummary,
  isContentPagePath,
} from "./contentModeModel";

const HUGO_CONFIG_FILES = [
  "hugo.toml",
  "config.toml",
  "config.yaml",
  "config.yml",
] as const;

const IGNORED_DIRS = new Set([
  ".git",
  ".hg",
  ".svn",
  "node_modules",
  "public",
  "resources",
  "themes",
  ".next",
  "dist",
  "build",
]);

function pathSep(base: string) {
  return base.includes("\\") ? "\\" : "/";
}

function joinPath(base: string, child: string) {
  return `${base.replace(/[\\/]+$/, "")}${pathSep(base)}${child}`;
}

function displayNameFromRoot(rootPath: string) {
  const normalized = rootPath.replace(/[\\/]+$/, "");
  const parts = normalized.split(/[\\/]/).filter(Boolean);
  const last = parts[parts.length - 1] ?? normalized;
  return last.replace(/[-_]/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

async function hasAnyFile(rootPath: string, names: readonly string[]) {
  for (const name of names) {
    if (await nativeFs.exists(joinPath(rootPath, name))) return true;
  }
  return false;
}

async function isHugoProjectRoot(rootPath: string) {
  const hasContentDir = await nativeFs.exists(joinPath(rootPath, "content"));
  if (!hasContentDir) {
    return {
      hasContentDir: false,
      hasHugoConfig: false,
    };
  }
  return {
    hasContentDir: true,
    hasHugoConfig: await hasAnyFile(rootPath, HUGO_CONFIG_FILES),
  };
}

export async function inspectContentProjectRoot(
  rootPath: string,
  source: ContentProjectCandidate["source"] = "manual",
): Promise<ContentProjectCandidate | null> {
  const status = await isHugoProjectRoot(rootPath);
  if (!status.hasContentDir) return null;
  return {
    rootPath,
    name: displayNameFromRoot(rootPath),
    source,
    hasContentDir: status.hasContentDir,
    hasHugoConfig: status.hasHugoConfig,
  };
}

export async function discoverContentProjects(
  pinnedRoots: string[],
): Promise<ContentProjectCandidate[]> {
  const seen = new Set<string>();
  const candidates: ContentProjectCandidate[] = [];

  const maybeAdd = async (
    rootPath: string,
    source: ContentProjectCandidate["source"],
  ) => {
    if (!rootPath || seen.has(rootPath)) return;
    seen.add(rootPath);
    const candidate = await inspectContentProjectRoot(rootPath, source);
    if (!candidate) return;
    candidates.push(candidate);
  };

  for (const root of pinnedRoots) {
    await maybeAdd(root, "pinned");
    const children = await nativeFs.readDir(root);
    if (!children) continue;
    for (const child of children) {
      if (!child.isDirectory || IGNORED_DIRS.has(child.name)) continue;
      await maybeAdd(joinPath(root, child.name), "nested");
    }
  }

  return candidates.sort((a, b) => {
    if (a.source !== b.source) {
      return a.source === "pinned" ? -1 : 1;
    }
    if (a.hasHugoConfig !== b.hasHugoConfig) {
      return a.hasHugoConfig ? -1 : 1;
    }
    return a.name.localeCompare(b.name);
  });
}

async function walkMarkdownFiles(
  rootDir: string,
  visit: (filePath: string) => Promise<void>,
  depth = 0,
): Promise<void> {
  if (depth > 8) return;
  const entries = await nativeFs.readDir(rootDir);
  if (!entries) return;
  for (const entry of entries) {
    const childPath = joinPath(rootDir, entry.name);
    if (entry.isDirectory) {
      if (IGNORED_DIRS.has(entry.name) || entry.name.startsWith(".")) continue;
      await walkMarkdownFiles(childPath, visit, depth + 1);
      continue;
    }
    if (!entry.name.endsWith(".md")) continue;
    await visit(childPath);
  }
}

export async function listContentPages(
  projectRoot: string,
): Promise<ContentPageSummary[]> {
  const contentRoot = joinPath(projectRoot, "content");
  if (!(await nativeFs.exists(contentRoot))) return [];

  const pages: ContentPageSummary[] = [];
  await walkMarkdownFiles(contentRoot, async (filePath) => {
    const rel = filePath
      .replace(/\\/g, "/")
      .slice(contentRoot.replace(/\\/g, "/").length + 1);
    if (!isContentPagePath(rel)) return;
    const content = await nativeFs.readFile(filePath);
    if (content === null) return;
    pages.push(buildContentPageSummary(contentRoot, filePath, content));
  });

  return pages.sort((a, b) => {
    if (a.section !== b.section) return a.section.localeCompare(b.section);
    return a.title.localeCompare(b.title);
  });
}
