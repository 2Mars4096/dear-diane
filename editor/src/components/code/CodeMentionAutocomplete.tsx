import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
} from "react";
import {
  FileText,
  Code2,
  FolderOpen,
  Hash,
  Braces,
  Variable,
  Box,
  Layers,
  FileCode,
  FileJson,
  FileType,
} from "lucide-react";
import { nativeFs, nativeLsp } from "../../lib/electronBridge";
import { useCodeStore } from "../../store/useCodeStore";

export type CodeMentionType = "file" | "symbol" | "folder";

interface CodeMentionItem {
  section: CodeMentionType;
  id: string;
  name: string;
  detail?: string;
  symbolKind?: number;
}

interface CodeMentionAutocompleteProps {
  query: string;
  anchorRect: { top: number; left: number } | null;
  onSelect: (mention: { type: CodeMentionType; id: string; name: string }) => void;
  onDismiss: () => void;
}

const SECTION_ORDER: CodeMentionType[] = ["file", "symbol", "folder"];

const SECTION_LABELS: Record<CodeMentionType, string> = {
  file: "Files",
  symbol: "Symbols",
  folder: "Folders",
};

const MAX_PER_SECTION = 10;
const MAX_TOTAL = 20;
const LSP_DEBOUNCE_MS = 300;

const SKIP_DIRS = new Set([
  "node_modules", ".git", "__pycache__", ".next", ".venv",
  "venv", "dist", "build", ".cache", ".idea", ".vscode",
  "coverage", ".DS_Store",
]);

function fuzzyScore(query: string, target: string): number {
  if (!query) return 1;
  const q = query.toLowerCase();
  const t = target.toLowerCase();

  if (t.includes(q)) return 1.0 - (q.length / (t.length + 1)) * 0.1;

  let qi = 0;
  let gaps = 0;
  let lastMatch = -1;
  const matchedIndices: number[] = [];

  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      if (lastMatch >= 0) gaps += ti - lastMatch - 1;
      lastMatch = ti;
      matchedIndices.push(ti);
      qi++;
    }
  }

  if (qi < q.length) return 0;

  const matchRatio = q.length / t.length;
  const gapPenalty = gaps / (t.length + 1);
  const startBonus = matchedIndices[0] === 0 ? 0.1 : 0;
  const fileNameBonus = t.split("/").pop()?.toLowerCase().includes(q) ? 0.2 : 0;

  return Math.max(0, matchRatio - gapPenalty * 0.5 + startBonus + fileNameBonus);
}

function highlightMatch(name: string, query: string) {
  if (!query) return <>{name}</>;
  const q = query.toLowerCase();
  const t = name.toLowerCase();
  const idx = t.indexOf(q);
  if (idx !== -1) {
    return (
      <>
        {name.slice(0, idx)}
        <span className="font-bold text-blue-300">{name.slice(idx, idx + query.length)}</span>
        {name.slice(idx + query.length)}
      </>
    );
  }

  let qi = 0;
  const matched = new Set<number>();
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      matched.add(ti);
      qi++;
    }
  }
  if (qi < q.length) return <>{name}</>;

  const parts: React.ReactElement[] = [];
  let i = 0;
  while (i < name.length) {
    if (matched.has(i)) {
      let end = i;
      while (end < name.length && matched.has(end)) end++;
      parts.push(<span key={i} className="font-bold text-blue-300">{name.slice(i, end)}</span>);
      i = end;
    } else {
      let end = i;
      while (end < name.length && !matched.has(end)) end++;
      parts.push(<span key={i}>{name.slice(i, end)}</span>);
      i = end;
    }
  }
  return <>{parts}</>;
}

function fileIcon(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  switch (ext) {
    case "ts":
    case "tsx":
      return <FileCode size={14} className="text-blue-400 flex-shrink-0" />;
    case "js":
    case "jsx":
      return <FileCode size={14} className="text-yellow-400 flex-shrink-0" />;
    case "py":
      return <FileCode size={14} className="text-green-400 flex-shrink-0" />;
    case "json":
      return <FileJson size={14} className="text-yellow-300 flex-shrink-0" />;
    case "md":
    case "mdx":
      return <FileType size={14} className="text-gray-400 flex-shrink-0" />;
    case "css":
    case "scss":
    case "less":
      return <FileCode size={14} className="text-pink-400 flex-shrink-0" />;
    case "html":
      return <FileCode size={14} className="text-orange-400 flex-shrink-0" />;
    case "rs":
      return <FileCode size={14} className="text-orange-500 flex-shrink-0" />;
    case "go":
      return <FileCode size={14} className="text-cyan-400 flex-shrink-0" />;
    default:
      return <FileText size={14} className="text-gray-400 flex-shrink-0" />;
  }
}

// LSP SymbolKind enum values
function symbolIcon(kind?: number) {
  switch (kind) {
    case 5: // Class
    case 10: // Enum
      return <Box size={14} className="text-amber-400 flex-shrink-0" />;
    case 6: // Method
    case 12: // Function
    case 9: // Constructor
      return <Braces size={14} className="text-purple-400 flex-shrink-0" />;
    case 11: // Interface
      return <Layers size={14} className="text-blue-400 flex-shrink-0" />;
    case 13: // Variable
    case 14: // Constant
      return <Variable size={14} className="text-green-400 flex-shrink-0" />;
    case 2: // Module
    case 3: // Namespace
    case 4: // Package
      return <Code2 size={14} className="text-cyan-400 flex-shrink-0" />;
    case 15: // String
    case 16: // Number
    case 17: // Boolean
      return <Hash size={14} className="text-gray-400 flex-shrink-0" />;
    default:
      return <Code2 size={14} className="text-gray-400 flex-shrink-0" />;
  }
}

const SYMBOL_KIND_LABEL: Record<number, string> = {
  1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
  6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
  11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
  15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
  20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
  25: "Operator", 26: "TypeParameter",
};

async function collectFilesFromRoots(
  roots: string[],
  maxDepth = 2,
): Promise<Array<{ path: string; isDirectory: boolean }>> {
  const results: Array<{ path: string; isDirectory: boolean }> = [];

  async function walk(dir: string, depth: number) {
    if (depth > maxDepth) return;
    const entries = await nativeFs.readDir(dir);
    if (!entries) return;
    for (const entry of entries) {
      if (SKIP_DIRS.has(entry.name)) continue;
      const full = `${dir}/${entry.name}`;
      results.push({ path: full, isDirectory: entry.isDirectory });
      if (entry.isDirectory && depth < maxDepth) {
        await walk(full, depth + 1);
      }
    }
  }

  for (const root of roots) {
    await walk(root, 0);
  }
  return results;
}

function relativePath(fullPath: string, roots: string[]): string {
  for (const root of roots) {
    if (fullPath.startsWith(root + "/")) {
      return fullPath.slice(root.length + 1);
    }
  }
  return fullPath;
}

export default function CodeMentionAutocomplete({
  query,
  anchorRect,
  onSelect,
  onDismiss,
}: CodeMentionAutocompleteProps) {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const openFiles = useCodeStore((s) => s.openFiles);

  const [fileEntries, setFileEntries] = useState<Array<{ path: string; isDirectory: boolean }>>([]);
  const [symbols, setSymbols] = useState<Array<{ name: string; kind: number; location?: string }>>([]);
  const [selectedIdx, setSelectedIdx] = useState(0);

  const listRef = useRef<HTMLDivElement>(null);
  const filesFetched = useRef(false);
  const lspTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLspQuery = useRef<string>("");

  // Fetch file tree from pinned roots (once)
  useEffect(() => {
    if (filesFetched.current || pinnedRoots.length === 0) return;
    filesFetched.current = true;
    collectFilesFromRoots(pinnedRoots, 2).then(setFileEntries).catch(() => {});
  }, [pinnedRoots]);

  // Debounced LSP workspace symbol search
  useEffect(() => {
    if (lspTimerRef.current) clearTimeout(lspTimerRef.current);
    if (!query || query.length < 2) {
      setSymbols([]);
      return;
    }
    lspTimerRef.current = setTimeout(async () => {
      if (query === lastLspQuery.current) return;
      lastLspQuery.current = query;
      try {
        const result = await nativeLsp.workspaceSymbol({ query });
        if (Array.isArray(result)) {
          setSymbols(
            result.slice(0, 30).map((s: any) => ({
              name: s.name,
              kind: s.kind ?? 0,
              location: s.location?.uri?.replace?.("file://", "") ?? "",
            })),
          );
        }
      } catch {
        // LSP may not be running
      }
    }, LSP_DEBOUNCE_MS);

    return () => {
      if (lspTimerRef.current) clearTimeout(lspTimerRef.current);
    };
  }, [query]);

  // Build items: Files
  const fileItems = useMemo((): CodeMentionItem[] => {
    const openFilePaths = new Set(openFiles.map((f) => f.path));
    const allFiles = fileEntries.filter((e) => !e.isDirectory);

    // Include open files first
    const openItems: CodeMentionItem[] = openFiles
      .filter((f) => !query || fuzzyScore(query, f.path) > 0.3)
      .map((f) => ({
        section: "file" as const,
        id: f.path,
        name: relativePath(f.path, pinnedRoots),
        detail: "open",
      }));

    const treeItems: CodeMentionItem[] = allFiles
      .filter((f) => !openFilePaths.has(f.path))
      .map((f) => ({
        section: "file" as const,
        id: f.path,
        name: relativePath(f.path, pinnedRoots),
        score: query ? fuzzyScore(query, relativePath(f.path, pinnedRoots)) : 1,
      }))
      .filter((f) => !query || (f as any).score > 0.3)
      .sort((a, b) => ((b as any).score ?? 1) - ((a as any).score ?? 1));

    const dedup = new Set(openItems.map((i) => i.id));
    const combined = [...openItems];
    for (const item of treeItems) {
      if (!dedup.has(item.id)) {
        dedup.add(item.id);
        combined.push(item);
      }
    }
    return combined.slice(0, MAX_PER_SECTION);
  }, [fileEntries, openFiles, pinnedRoots, query]);

  // Build items: Symbols
  const symbolItems = useMemo((): CodeMentionItem[] => {
    return symbols
      .filter((s) => !query || fuzzyScore(query, s.name) > 0.2)
      .slice(0, MAX_PER_SECTION)
      .map((s) => ({
        section: "symbol" as const,
        id: s.name,
        name: s.name,
        detail: SYMBOL_KIND_LABEL[s.kind] ?? "Symbol",
        symbolKind: s.kind,
      }));
  }, [symbols, query]);

  // Build items: Folders
  const folderItems = useMemo((): CodeMentionItem[] => {
    return fileEntries
      .filter((e) => e.isDirectory)
      .map((e) => ({
        section: "folder" as const,
        id: e.path,
        name: relativePath(e.path, pinnedRoots),
        score: query ? fuzzyScore(query, relativePath(e.path, pinnedRoots)) : 1,
      }))
      .filter((f) => !query || (f as any).score > 0.3)
      .sort((a, b) => ((b as any).score ?? 1) - ((a as any).score ?? 1))
      .slice(0, MAX_PER_SECTION);
  }, [fileEntries, pinnedRoots, query]);

  const flatItems = useMemo(() => {
    const all = [...fileItems, ...symbolItems, ...folderItems];
    return all.slice(0, MAX_TOTAL);
  }, [fileItems, symbolItems, folderItems]);

  useEffect(() => setSelectedIdx(0), [query]);

  const selectItem = useCallback(
    (item: CodeMentionItem) => {
      onSelect({ type: item.section, id: item.id, name: item.name });
    },
    [onSelect],
  );

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onDismiss();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIdx((i) => Math.min(i + 1, flatItems.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIdx((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter" && flatItems.length > 0) {
        e.preventDefault();
        e.stopPropagation();
        selectItem(flatItems[selectedIdx]);
      }
    };

    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [flatItems, selectedIdx, selectItem, onDismiss]);

  // Scroll selected into view
  useEffect(() => {
    const el = listRef.current?.querySelector("[data-selected]");
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedIdx]);

  if (!anchorRect) return null;

  const flipUp = anchorRect.top > window.innerHeight - 340;
  const style: React.CSSProperties = {
    position: "fixed",
    left: anchorRect.left,
    zIndex: 50,
    ...(flipUp
      ? { bottom: window.innerHeight - anchorRect.top + 4 }
      : { top: anchorRect.top + 4 }),
  };

  let runningIdx = 0;

  const renderItem = (item: CodeMentionItem, idx: number) => {
    const selected = idx === selectedIdx;
    return (
      <button
        key={`${item.section}-${item.id}-${idx}`}
        data-selected={selected ? "" : undefined}
        onMouseDown={(e) => {
          e.preventDefault();
          selectItem(item);
        }}
        onMouseEnter={() => setSelectedIdx(idx)}
        className={`flex items-center gap-2 w-full px-3 py-1.5 text-[13px] cursor-pointer text-left transition-colors ${
          selected
            ? "bg-[#094771] text-white"
            : "text-gray-300 hover:bg-[#2a2d2e]"
        }`}
      >
        {item.section === "file" && fileIcon(item.name)}
        {item.section === "symbol" && symbolIcon(item.symbolKind)}
        {item.section === "folder" && <FolderOpen size={14} className="text-amber-400 flex-shrink-0" />}

        <span className="truncate flex-1 font-mono text-xs">
          {highlightMatch(item.name, query)}
        </span>

        {item.detail && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#3c3c3c] text-gray-500 flex-shrink-0">
            {item.detail}
          </span>
        )}
      </button>
    );
  };

  const renderSection = (section: CodeMentionType, items: CodeMentionItem[]) => {
    if (items.length === 0) return null;
    const startIdx = runningIdx;
    runningIdx += items.length;
    return (
      <div key={section}>
        <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider px-3 py-1.5 border-b border-[#3c3c3c]">
          {SECTION_LABELS[section]}
        </div>
        {items.map((item, i) => renderItem(item, startIdx + i))}
      </div>
    );
  };

  const sectionMap: Record<CodeMentionType, CodeMentionItem[]> = {
    file: flatItems.filter((i) => i.section === "file"),
    symbol: flatItems.filter((i) => i.section === "symbol"),
    folder: flatItems.filter((i) => i.section === "folder"),
  };

  return (
    <div
      ref={listRef}
      data-mention-dropdown
      style={style}
      className="bg-[#252526] border border-[#3c3c3c] rounded-lg shadow-xl max-h-[300px] overflow-y-auto w-80"
    >
      {flatItems.length === 0 ? (
        <div className="px-3 py-3 text-[13px] text-gray-500 text-center">
          {query ? "No matches" : "Type to search files, symbols, folders..."}
        </div>
      ) : (
        SECTION_ORDER.map((sec) => renderSection(sec, sectionMap[sec]))
      )}
    </div>
  );
}
