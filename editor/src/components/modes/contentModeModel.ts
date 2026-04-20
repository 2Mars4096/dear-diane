export interface ContentFrontmatterSummary {
  raw: string | null;
  body: string;
  title: string | null;
  pageId: string | null;
  date: string | null;
  draft: boolean | null;
  slug: string | null;
  url: string | null;
}

export interface ContentProjectCandidate {
  rootPath: string;
  name: string;
  source: "pinned" | "nested" | "manual";
  hasHugoConfig: boolean;
  hasContentDir: boolean;
}

export interface ContentPageSummary {
  filePath: string;
  relPath: string;
  title: string;
  pageId: string | null;
  section: string;
  slug: string;
  draft: boolean;
  wordCount: number;
  excerpt: string;
  previewPath: string;
}

export interface ContentFolderTreeNode {
  relPath: string;
  name: string;
  label: string;
  folders: ContentFolderTreeNode[];
  pages: ContentPageSummary[];
  pageCount: number;
}

function trimQuotes(value: string) {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function normalizeSlashes(value: string) {
  return value.replace(/\\/g, "/");
}

function formatFolderLabel(segment: string) {
  return segment.replace(/[-_]/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function normalizePreviewPath(value: string) {
  const trimmed = trimQuotes(value).trim();
  if (!trimmed) return "/";
  try {
    if (/^https?:\/\//i.test(trimmed)) {
      const url = new URL(trimmed);
      const normalizedPath = url.pathname || "/";
      return normalizePreviewPath(
        `${normalizedPath}${url.search}${url.hash}`,
      );
    }
  } catch {
    // Fall through to path normalization.
  }

  const [pathWithQuery, hash = ""] = trimmed.split("#", 2);
  const [pathnameRaw, query = ""] = pathWithQuery.split("?", 2);
  const pathname = pathnameRaw.startsWith("/") ? pathnameRaw : `/${pathnameRaw}`;
  const looksLikeFile = /\.[a-z0-9]+$/i.test(pathname);
  const normalizedPath =
    pathname === "/"
      ? pathname
      : looksLikeFile || pathname.endsWith("/")
        ? pathname
        : `${pathname}/`;
  return `${normalizedPath}${query ? `?${query}` : ""}${hash ? `#${hash}` : ""}`;
}

export function extractFrontmatterSummary(content: string): ContentFrontmatterSummary {
  const match = content.match(/^---\s*\n([\s\S]*?)\n---\s*(?:\n|$)/);
  const raw = match ? match[1] : null;
  const body = match ? content.slice(match[0].length) : content;

  const extractValue = (key: string) => {
    if (!raw) return null;
    const lineMatch = raw.match(new RegExp(`^${key}:\\s*(.+)$`, "m"));
    return lineMatch ? trimQuotes(lineMatch[1]) : null;
  };

  const draftValue = extractValue("draft");
  return {
    raw,
    body,
    title: extractValue("title"),
    pageId: extractValue("pageID"),
    date: extractValue("date"),
    slug: extractValue("slug"),
    url: extractValue("url"),
    draft:
      draftValue === null
        ? null
        : /^(true|yes|on)$/i.test(draftValue)
          ? true
          : /^(false|no|off)$/i.test(draftValue)
            ? false
            : null,
  };
}

export function inferSectionFromRelPath(relPath: string) {
  const normalized = normalizeSlashes(relPath).replace(/^\/+/, "");
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length === 0) return "root";
  return segments[0] === "index.md" ? "root" : segments[0];
}

export function inferSlugFromRelPath(relPath: string) {
  const normalized = normalizeSlashes(relPath).replace(/^\/+/, "");
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length === 0) return "index";
  const last = segments[segments.length - 1];
  if (last === "index.md" && segments.length > 1) {
    return segments[segments.length - 2];
  }
  return last.replace(/\.md$/i, "");
}

export function getContentFolderAncestorPaths(relPath: string) {
  const normalized = normalizeSlashes(relPath).replace(/^\/+/, "");
  const segments = normalized.split("/").filter(Boolean);
  const folderSegments = segments.slice(0, -1);
  return folderSegments.map((_segment, index) =>
    folderSegments.slice(0, index + 1).join("/"),
  );
}

function createFolderTreeNode(
  relPath: string,
  name: string,
): ContentFolderTreeNode {
  return {
    relPath,
    name,
    label: name ? formatFolderLabel(name) : "Root",
    folders: [],
    pages: [],
    pageCount: 0,
  };
}

export function buildContentFolderTree(
  pages: ContentPageSummary[],
): ContentFolderTreeNode {
  const root = createFolderTreeNode("", "");

  for (const page of pages) {
    const normalized = normalizeSlashes(page.relPath).replace(/^\/+/, "");
    const segments = normalized.split("/").filter(Boolean);
    const folderSegments = segments.slice(0, -1);

    let current = root;
    let currentPath = "";

    for (const segment of folderSegments) {
      currentPath = currentPath ? `${currentPath}/${segment}` : segment;
      let child = current.folders.find((folder) => folder.name === segment);
      if (!child) {
        child = createFolderTreeNode(currentPath, segment);
        current.folders.push(child);
      }
      current = child;
    }

    current.pages.push(page);
  }

  const finalize = (node: ContentFolderTreeNode): ContentFolderTreeNode => {
    node.pages.sort((a, b) =>
      a.title.localeCompare(b.title) || a.relPath.localeCompare(b.relPath),
    );
    node.folders.sort((a, b) =>
      a.label.localeCompare(b.label) || a.relPath.localeCompare(b.relPath),
    );
    node.folders = node.folders.map((child) => finalize(child));
    node.pageCount =
      node.pages.length +
      node.folders.reduce((sum, child) => sum + child.pageCount, 0);
    return node;
  };

  return finalize(root);
}

export function inferPreviewPathFromRelPath(relPath: string) {
  const normalized = normalizeSlashes(relPath).replace(/^\/+/, "");
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length === 0) return "/";
  const last = segments[segments.length - 1];
  const previewSegments =
    last === "index.md"
      ? segments.slice(0, -1)
      : [...segments.slice(0, -1), last.replace(/\.md$/i, "")];
  return previewSegments.length === 0 ? "/" : `/${previewSegments.join("/")}/`;
}

export function resolvePreviewPath(
  relPath: string,
  frontmatter?: Pick<ContentFrontmatterSummary, "slug" | "url"> | null,
) {
  if (frontmatter?.url) {
    return normalizePreviewPath(frontmatter.url);
  }
  if (frontmatter?.slug) {
    const normalized = normalizeSlashes(relPath).replace(/^\/+/, "");
    const segments = normalized.split("/").filter(Boolean);
    if (segments.length === 0) return normalizePreviewPath(frontmatter.slug);
    const last = segments[segments.length - 1];
    const previewSegments =
      last === "index.md"
        ? [...segments.slice(0, -2), frontmatter.slug]
        : [...segments.slice(0, -1), frontmatter.slug];
    return previewSegments.length === 0
      ? normalizePreviewPath(frontmatter.slug)
      : normalizePreviewPath(`/${previewSegments.join("/")}/`);
  }
  return inferPreviewPathFromRelPath(relPath);
}

export function buildManagedPreviewUrl(
  baseUrl: string | null,
  previewPath: string | null,
  options?: {
    embedded?: boolean;
  },
) {
  if (!baseUrl || !previewPath) return null;
  try {
    const base = new URL(baseUrl);
    const target = new URL(previewPath, base);
    if (target.origin !== base.origin) return null;
    if (options?.embedded) {
      target.searchParams.set("dan_preview", "1");
    }
    return target.toString();
  } catch {
    return null;
  }
}

export function isContentPagePath(relPath: string) {
  const normalized = normalizeSlashes(relPath);
  if (!normalized.endsWith(".md")) return false;
  return !normalized.endsWith("/_index.md") && !normalized.startsWith("_index.md");
}

export function summarizeHeadings(markdown: string) {
  return markdown
    .split(/\r?\n/)
    .map((line) => line.match(/^(#{2,6})\s+(.+?)\s*$/))
    .filter((match): match is RegExpMatchArray => Boolean(match))
    .map((match) => ({
      depth: match[1].length,
      text: match[2],
    }));
}

export function buildContentPageSummary(
  projectRoot: string,
  filePath: string,
  content: string,
): ContentPageSummary {
  const normalizedRoot = normalizeSlashes(projectRoot).replace(/\/+$/, "");
  const normalizedFile = normalizeSlashes(filePath);
  const relPath = normalizedFile.startsWith(`${normalizedRoot}/`)
    ? normalizedFile.slice(normalizedRoot.length + 1)
    : normalizedFile;
  const frontmatter = extractFrontmatterSummary(content);
  const bodyText = frontmatter.body
    .replace(/{{<[\s\S]*?>}}/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  const section = inferSectionFromRelPath(relPath);
  const slug = inferSlugFromRelPath(relPath);
  const title =
    frontmatter.title ||
    slug
      .replace(/[-_]/g, " ")
      .replace(/\b\w/g, (char) => char.toUpperCase());

  return {
    filePath,
    relPath,
    title,
    pageId: frontmatter.pageId,
    section,
    slug,
    draft: frontmatter.draft ?? false,
    wordCount: bodyText ? bodyText.split(/\s+/).length : 0,
    excerpt: bodyText.slice(0, 180),
    previewPath: resolvePreviewPath(relPath, frontmatter),
  };
}
