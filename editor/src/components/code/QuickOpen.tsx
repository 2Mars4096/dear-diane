import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  File,
  FileCode,
  FileText,
  Braces,
  Image,
  Settings,
  Loader2,
  Search,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeFs } from "../../lib/electronBridge";

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const MAX_FILES = 10_000;
const MAX_RESULTS = 20;

const SKIP_DIRS = new Set([
  "node_modules",
  ".git",
  "__pycache__",
  ".DS_Store",
  "dist",
  "build",
  ".next",
  ".cache",
  "venv",
  ".venv",
]);

/* ------------------------------------------------------------------ */
/*  File icons (mirrors FileExplorer)                                  */
/* ------------------------------------------------------------------ */

function extension(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}

function getFileIcon(name: string) {
  const ext = extension(name);
  switch (ext) {
    case "ts":
    case "tsx":
    case "js":
    case "jsx":
      return <FileCode size={16} className="shrink-0 text-blue-400" />;
    case "py":
      return <FileCode size={16} className="shrink-0 text-green-400" />;
    case "json":
      return <Braces size={16} className="shrink-0 text-yellow-400" />;
    case "md":
    case "mdx":
    case "txt":
      return <FileText size={16} className="shrink-0 text-gray-400" />;
    case "png":
    case "jpg":
    case "jpeg":
    case "gif":
    case "svg":
    case "webp":
    case "ico":
      return <Image size={16} className="shrink-0 text-purple-400" />;
    case "env":
    case "toml":
    case "yaml":
    case "yml":
    case "ini":
    case "cfg":
      return <Settings size={16} className="shrink-0 text-gray-400" />;
    default:
      return <File size={16} className="shrink-0 text-gray-500" />;
  }
}

/* ------------------------------------------------------------------ */
/*  Recursive file walker                                              */
/* ------------------------------------------------------------------ */

async function walkDir(
  dir: string,
  files: string[],
  signal: { cancelled: boolean },
): Promise<void> {
  if (signal.cancelled || files.length >= MAX_FILES) return;

  const entries = await nativeFs.readDir(dir);
  if (!entries || signal.cancelled) return;

  const subdirs: string[] = [];

  for (const entry of entries) {
    if (signal.cancelled || files.length >= MAX_FILES) return;
    if (SKIP_DIRS.has(entry.name)) continue;
    if (entry.name.startsWith(".") && entry.isDirectory) continue;

    const fullPath = `${dir}/${entry.name}`;
    if (entry.isDirectory) {
      subdirs.push(fullPath);
    } else {
      files.push(fullPath);
    }
  }

  for (const sub of subdirs) {
    if (signal.cancelled || files.length >= MAX_FILES) return;
    await walkDir(sub, files, signal);
  }
}

/* ------------------------------------------------------------------ */
/*  Fuzzy matcher                                                      */
/* ------------------------------------------------------------------ */

interface FuzzyResult {
  path: string;
  score: number;
  matchIndices: number[];
}

const WORD_SEPARATORS = new Set(["/", ".", "-", "_", " "]);

function fuzzyMatch(pattern: string, text: string): FuzzyResult | null {
  const lowerPattern = pattern.toLowerCase();
  const lowerText = text.toLowerCase();

  const matchIndices: number[] = [];
  let pi = 0;

  for (let ti = 0; ti < lowerText.length && pi < lowerPattern.length; ti++) {
    if (lowerText[ti] === lowerPattern[pi]) {
      matchIndices.push(ti);
      pi++;
    }
  }

  if (pi < lowerPattern.length) return null;

  let score = 0;

  for (let i = 0; i < matchIndices.length; i++) {
    const idx = matchIndices[i];

    if (i > 0 && matchIndices[i] === matchIndices[i - 1] + 1) {
      score += 8;
    }

    if (idx === 0 || WORD_SEPARATORS.has(text[idx - 1])) {
      score += 10;
    }

    // Prefer matches toward the end of the path (filename portion)
    score += idx / text.length;
  }

  // Bonus for shorter paths (less noise)
  score -= text.length * 0.01;

  // Bonus for exact filename match
  const fileName = text.split("/").pop() ?? text;
  if (fileName.toLowerCase().startsWith(lowerPattern)) {
    score += 25;
  }

  return { path: text, score, matchIndices };
}

/* ------------------------------------------------------------------ */
/*  File index cache hook                                              */
/* ------------------------------------------------------------------ */

let cachedRootsKey = "";
let cachedFiles: string[] = [];

function useFileIndex(visible: boolean) {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const [files, setFiles] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!visible) return;

    const rootsKey = pinnedRoots.join("\n");
    if (rootsKey === cachedRootsKey && cachedFiles.length > 0) {
      setFiles(cachedFiles);
      return;
    }

    const signal = { cancelled: false };
    let mounted = true;

    (async () => {
      setLoading(true);
      const allFiles: string[] = [];

      for (const root of pinnedRoots) {
        if (signal.cancelled) break;
        await walkDir(root, allFiles, signal);
      }

      if (mounted && !signal.cancelled) {
        cachedRootsKey = rootsKey;
        cachedFiles = allFiles;
        setFiles(allFiles);
        setLoading(false);
      }
    })();

    return () => {
      signal.cancelled = true;
      mounted = false;
    };
  }, [visible, pinnedRoots]);

  return { files, loading };
}

/* ------------------------------------------------------------------ */
/*  Highlighted text renderer                                          */
/* ------------------------------------------------------------------ */

function HighlightedText({
  text,
  matchIndices,
  className,
}: {
  text: string;
  matchIndices: Set<number>;
  className?: string;
}) {
  const spans: ReactNode[] = [];
  let run = "";
  let runHighlighted = false;

  for (let i = 0; i <= text.length; i++) {
    const isMatch = matchIndices.has(i);
    if (i === text.length || isMatch !== runHighlighted) {
      if (run) {
        spans.push(
          runHighlighted ? (
            <span key={i} className="text-yellow-300 font-medium">
              {run}
            </span>
          ) : (
            <span key={i}>{run}</span>
          ),
        );
      }
      run = text[i] ?? "";
      runHighlighted = isMatch;
    } else {
      run += text[i];
    }
  }

  return <span className={className}>{spans}</span>;
}

/* ------------------------------------------------------------------ */
/*  Relative path helper                                               */
/* ------------------------------------------------------------------ */

function relativeTo(filePath: string, roots: string[]): string {
  for (const root of roots) {
    if (filePath.startsWith(root + "/")) {
      return filePath.slice(root.length + 1);
    }
  }
  return filePath;
}

/* ------------------------------------------------------------------ */
/*  QuickOpen component                                                */
/* ------------------------------------------------------------------ */

export default function QuickOpen({ onClose }: { onClose: () => void }) {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const openFiles = useCodeStore((s) => s.openFiles);
  const openFile = useCodeStore((s) => s.openFile);
  const { files, loading } = useFileIndex(true);

  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);

  const openFilePaths = useMemo(
    () => new Set(openFiles.map((f) => f.path)),
    [openFiles],
  );

  // Focus input on mount
  useEffect(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  // Compute results
  const results = useMemo(() => {
    if (!query.trim()) {
      // Show recently opened files first, then first files from index
      const recent = openFiles.map((f) => f.path);
      const seen = new Set(recent);
      const rest = files.filter((f) => !seen.has(f)).slice(0, MAX_RESULTS - recent.length);
      return [...recent, ...rest].slice(0, MAX_RESULTS).map((path) => ({
        path,
        score: 0,
        matchIndices: [] as number[],
        relPath: relativeTo(path, pinnedRoots),
      }));
    }

    const matches: (FuzzyResult & { relPath: string })[] = [];
    for (const filePath of files) {
      const relPath = relativeTo(filePath, pinnedRoots);
      const result = fuzzyMatch(query, relPath);
      if (result) {
        // Boost recently opened files
        const boost = openFilePaths.has(filePath) ? 15 : 0;
        matches.push({
          ...result,
          score: result.score + boost,
          relPath,
        });
      }
    }

    matches.sort((a, b) => b.score - a.score);
    return matches.slice(0, MAX_RESULTS);
  }, [query, files, openFiles, openFilePaths, pinnedRoots]);

  // Reset selection when results change
  useEffect(() => {
    setSelectedIndex(0);
  }, [results]);

  // Scroll selected item into view
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const item = list.children[selectedIndex] as HTMLElement | undefined;
    item?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  const handleSelect = useCallback(
    async (filePath: string) => {
      onClose();
      const content = await nativeFs.readFile(filePath);
      openFile(filePath, content ?? "");
    },
    [onClose, openFile],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setSelectedIndex((i) => Math.min(i + 1, results.length - 1));
          break;
        case "ArrowUp":
          e.preventDefault();
          setSelectedIndex((i) => Math.max(i - 1, 0));
          break;
        case "Enter":
          e.preventDefault();
          if (results[selectedIndex]) {
            handleSelect(results[selectedIndex].path);
          }
          break;
        case "Escape":
          e.preventDefault();
          onClose();
          break;
      }
    },
    [results, selectedIndex, handleSelect, onClose],
  );

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === backdropRef.current) onClose();
    },
    [onClose],
  );

  return (
    <div
      ref={backdropRef}
      className="fixed inset-0 z-50 bg-black/50"
      onClick={handleBackdropClick}
    >
      <div className="max-w-2xl mx-auto mt-[10vh] rounded-lg shadow-2xl bg-[#252526] border border-[#3c3c3c] overflow-hidden">
        {/* Search input */}
        <div className="flex items-center gap-2 px-4 border-b border-[#3c3c3c]">
          <Search size={16} className="shrink-0 text-gray-500" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search files by name…"
            className="w-full bg-transparent text-white text-sm px-0 py-3 outline-none placeholder:text-gray-600"
            autoComplete="off"
            spellCheck={false}
          />
          {loading && (
            <Loader2 size={16} className="shrink-0 text-gray-500 animate-spin" />
          )}
        </div>

        {/* Results list */}
        <div ref={listRef} className="max-h-[50vh] overflow-y-auto">
          {results.length === 0 && !loading && (
            <div className="px-4 py-6 text-center text-sm text-gray-500">
              {query ? "No matching files" : "No files indexed"}
            </div>
          )}

          {results.length === 0 && loading && (
            <div className="flex items-center justify-center gap-2 px-4 py-6 text-sm text-gray-500">
              <Loader2 size={14} className="animate-spin" />
              Indexing workspace files…
            </div>
          )}

          {results.map((result, i) => {
            const fileName = result.relPath.split("/").pop() ?? result.relPath;
            const dirPath = result.relPath.includes("/")
              ? result.relPath.slice(0, result.relPath.lastIndexOf("/"))
              : "";
            const isOpen = openFilePaths.has(result.path);
            const isSelected = i === selectedIndex;

            // Compute highlight indices relative to the displayed segments
            const matchSet = new Set(result.matchIndices);
            const fileNameStart = result.relPath.length - fileName.length;
            const fileNameIndices = new Set<number>();
            const dirIndices = new Set<number>();

            for (const idx of result.matchIndices) {
              if (idx >= fileNameStart) {
                fileNameIndices.add(idx - fileNameStart);
              } else {
                dirIndices.add(idx);
              }
            }

            return (
              <div
                key={result.path}
                className={`px-4 py-2 flex items-center gap-3 cursor-pointer transition-colors ${
                  isSelected
                    ? "bg-[#094771]"
                    : "hover:bg-[#2a2d2e]"
                }`}
                onClick={() => handleSelect(result.path)}
                onMouseEnter={() => setSelectedIndex(i)}
              >
                {getFileIcon(fileName)}
                <div className="min-w-0 flex-1 flex items-baseline gap-1.5 overflow-hidden">
                  {matchSet.size > 0 ? (
                    <HighlightedText
                      text={fileName}
                      matchIndices={fileNameIndices}
                      className="text-sm text-white font-medium truncate shrink-0"
                    />
                  ) : (
                    <span className="text-sm text-white font-medium truncate shrink-0">
                      {fileName}
                    </span>
                  )}
                  {dirPath && (
                    matchSet.size > 0 ? (
                      <HighlightedText
                        text={dirPath}
                        matchIndices={dirIndices}
                        className="text-xs text-gray-500 truncate"
                      />
                    ) : (
                      <span className="text-xs text-gray-500 truncate">
                        {dirPath}
                      </span>
                    )
                  )}
                </div>
                {isOpen && (
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-blue-400/60" />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
