const ES_IMPORT_RE =
  /(?:import\s+(?:[\w*{}\s,]+\s+from\s+)?['"]([^'"]+)['"]|import\s*\(['"]([^'"]+)['"]\))/g;
const REQUIRE_RE = /require\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
const PY_IMPORT_RE =
  /(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))/g;

const KNOWN_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"];

function dirname(p: string): string {
  const idx = p.lastIndexOf("/");
  return idx >= 0 ? p.slice(0, idx) : ".";
}

function extname(p: string): string {
  const base = p.slice(p.lastIndexOf("/") + 1);
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot) : "";
}

function joinPath(...parts: string[]): string {
  return parts.join("/").replace(/\/+/g, "/");
}

function normalizePath(p: string): string {
  const segments: string[] = [];
  for (const seg of p.split("/")) {
    if (seg === ".." && segments.length > 0 && segments[segments.length - 1] !== "..") {
      segments.pop();
    } else if (seg !== "." && seg !== "") {
      segments.push(seg);
    }
  }
  return (p.startsWith("/") ? "/" : "") + segments.join("/");
}

function isRelative(specifier: string): boolean {
  return specifier.startsWith(".") || specifier.startsWith("/");
}

function resolveRelativePath(specifier: string, fromFile: string): string {
  const dir = dirname(fromFile);
  let resolved = normalizePath(joinPath(dir, specifier));
  if (!extname(resolved)) {
    const fromExt = extname(fromFile);
    if (KNOWN_EXTENSIONS.includes(fromExt)) {
      resolved += fromExt;
    }
  }
  return resolved;
}

function pyModuleToRelative(mod: string, fromFile: string): string | null {
  if (mod.startsWith(".")) {
    const dir = dirname(fromFile);
    const parts = mod.replace(/^\.+/, "").split(".");
    const dots = mod.match(/^\.+/)?.[0].length ?? 1;
    let base = dir;
    for (let i = 1; i < dots; i++) base = dirname(base);
    return normalizePath(joinPath(base, ...parts)) + ".py";
  }
  return null;
}

export function extractImportPaths(
  content: string,
  filePath: string,
): string[] {
  const paths = new Set<string>();

  let m: RegExpExecArray | null;

  ES_IMPORT_RE.lastIndex = 0;
  while ((m = ES_IMPORT_RE.exec(content)) !== null) {
    const specifier = m[1] ?? m[2];
    if (specifier && isRelative(specifier)) {
      paths.add(resolveRelativePath(specifier, filePath));
    }
  }

  REQUIRE_RE.lastIndex = 0;
  while ((m = REQUIRE_RE.exec(content)) !== null) {
    if (m[1] && isRelative(m[1])) {
      paths.add(resolveRelativePath(m[1], filePath));
    }
  }

  PY_IMPORT_RE.lastIndex = 0;
  while ((m = PY_IMPORT_RE.exec(content)) !== null) {
    const mod = m[1] ?? m[2];
    if (mod) {
      const resolved = pyModuleToRelative(mod, filePath);
      if (resolved) paths.add(resolved);
    }
  }

  return Array.from(paths);
}
