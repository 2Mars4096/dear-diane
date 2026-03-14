import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Hash } from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeFs } from "../../lib/electronBridge";

/* ------------------------------------------------------------------ */
/*  Symbol types & extraction                                          */
/* ------------------------------------------------------------------ */

interface SymbolInfo {
  name: string;
  kind:
    | "function"
    | "class"
    | "method"
    | "variable"
    | "interface"
    | "type"
    | "enum"
    | "const"
    | "import";
  filePath: string;
  line: number;
  containerName?: string;
}

const SYMBOL_PATTERNS: Array<{ pattern: RegExp; kind: SymbolInfo["kind"] }> = [
  // TypeScript / JavaScript
  { pattern: /^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)/gm, kind: "function" },
  { pattern: /^\s*(?:export\s+)?class\s+(\w+)/gm, kind: "class" },
  { pattern: /^\s*(?:export\s+)?interface\s+(\w+)/gm, kind: "interface" },
  { pattern: /^\s*(?:export\s+)?type\s+(\w+)/gm, kind: "type" },
  { pattern: /^\s*(?:export\s+)?enum\s+(\w+)/gm, kind: "enum" },
  { pattern: /^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)/gm, kind: "variable" },
  { pattern: /^\s*(?:public|private|protected)?\s*(?:async\s+)?(\w+)\s*\(/gm, kind: "method" },
  // Python
  { pattern: /^\s*def\s+(\w+)/gm, kind: "function" },
  { pattern: /^\s*class\s+(\w+)/gm, kind: "class" },
  // Rust
  { pattern: /^\s*(?:pub\s+)?fn\s+(\w+)/gm, kind: "function" },
  { pattern: /^\s*(?:pub\s+)?struct\s+(\w+)/gm, kind: "class" },
  { pattern: /^\s*(?:pub\s+)?enum\s+(\w+)/gm, kind: "enum" },
  { pattern: /^\s*(?:pub\s+)?trait\s+(\w+)/gm, kind: "interface" },
  // Go
  { pattern: /^\s*func\s+(?:\(.*?\)\s+)?(\w+)/gm, kind: "function" },
  { pattern: /^\s*type\s+(\w+)\s+struct/gm, kind: "class" },
  { pattern: /^\s*type\s+(\w+)\s+interface/gm, kind: "interface" },
];

function extractSymbols(content: string, filePath: string): SymbolInfo[] {
  const symbols: SymbolInfo[] = [];
  const seen = new Set<string>();

  for (const { pattern, kind } of SYMBOL_PATTERNS) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(content)) !== null) {
      const name = match[1];
      if (!name || name.length < 2) continue;

      const beforeMatch = content.slice(0, match.index);
      const line = beforeMatch.split("\n").length;

      const key = `${kind}:${name}:${filePath}:${line}`;
      if (seen.has(key)) continue;
      seen.add(key);

      symbols.push({ name, kind, filePath, line });
    }
  }

  return symbols;
}

/* ------------------------------------------------------------------ */
/*  Kind styling                                                       */
/* ------------------------------------------------------------------ */

const KIND_COLORS: Record<string, string> = {
  function: "text-blue-400",
  class: "text-yellow-400",
  interface: "text-green-400",
  method: "text-purple-400",
  type: "text-cyan-400",
  enum: "text-orange-400",
  variable: "text-gray-400",
  const: "text-gray-400",
  import: "text-gray-500",
};

const KIND_LABELS: Record<string, string> = {
  function: "fn",
  class: "C",
  interface: "I",
  method: "M",
  type: "T",
  enum: "E",
  variable: "v",
  const: "c",
  import: "im",
};

function KindBadge({ kind }: { kind: string }) {
  return (
    <span
      className={`shrink-0 w-5 h-5 flex items-center justify-center rounded text-[10px] font-bold bg-[#1e1e1e] ${KIND_COLORS[kind] ?? "text-gray-500"}`}
    >
      {KIND_LABELS[kind] ?? "?"}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Fuzzy matcher                                                      */
/* ------------------------------------------------------------------ */

interface FuzzyResult {
  symbol: SymbolInfo;
  score: number;
  matchIndices: number[];
}

function fuzzyMatch(
  pattern: string,
  text: string,
): { score: number; matchIndices: number[] } | null {
  const lp = pattern.toLowerCase();
  const lt = text.toLowerCase();
  const indices: number[] = [];
  let pi = 0;

  for (let ti = 0; ti < lt.length && pi < lp.length; ti++) {
    if (lt[ti] === lp[pi]) {
      indices.push(ti);
      pi++;
    }
  }
  if (pi < lp.length) return null;

  let score = 0;
  for (let i = 0; i < indices.length; i++) {
    const idx = indices[i];
    if (i > 0 && idx === indices[i - 1] + 1) score += 8;
    if (idx === 0) score += 10;
    score += idx / text.length;
  }
  score -= text.length * 0.01;

  return { score, matchIndices: indices };
}

/* ------------------------------------------------------------------ */
/*  Highlighted text                                                   */
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
/*  SymbolSearch component                                             */
/* ------------------------------------------------------------------ */

const MAX_RESULTS = 30;

export default function SymbolSearch({ onClose }: { onClose: () => void }) {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const openFiles = useCodeStore((s) => s.openFiles);
  const openFile = useCodeStore((s) => s.openFile);
  const setActiveFile = useCodeStore((s) => s.setActiveFile);

  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const allSymbols = useMemo(() => {
    const symbols: SymbolInfo[] = [];
    for (const file of openFiles) {
      symbols.push(...extractSymbols(file.content, file.path));
    }
    return symbols;
  }, [openFiles]);

  const results = useMemo((): FuzzyResult[] => {
    const raw = query.replace(/^#\s*/, "");

    if (!raw.trim()) {
      return allSymbols.slice(0, MAX_RESULTS).map((s) => ({
        symbol: s,
        score: 0,
        matchIndices: [],
      }));
    }

    const matches: FuzzyResult[] = [];
    for (const sym of allSymbols) {
      const m = fuzzyMatch(raw, sym.name);
      if (m) matches.push({ symbol: sym, ...m });
    }
    matches.sort((a, b) => b.score - a.score);
    return matches.slice(0, MAX_RESULTS);
  }, [query, allSymbols]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [results]);

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const item = list.children[selectedIndex] as HTMLElement | undefined;
    item?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  const handleSelect = useCallback(
    async (sym: SymbolInfo) => {
      onClose();

      const existing = openFiles.find((f) => f.path === sym.filePath);
      if (existing) {
        setActiveFile(sym.filePath);
      } else {
        const content = await nativeFs.readFile(sym.filePath);
        openFile(sym.filePath, content ?? "");
      }

      requestAnimationFrame(() => {
        window.dispatchEvent(
          new CustomEvent("editor:goToLine", {
            detail: { lineNumber: sym.line, column: 1 },
          }),
        );
      });
    },
    [onClose, openFiles, openFile, setActiveFile],
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
          if (results[selectedIndex]) handleSelect(results[selectedIndex].symbol);
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
          <Hash size={16} className="shrink-0 text-gray-500" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type to search symbols in open files…"
            className="w-full bg-transparent text-white text-sm px-0 py-3 outline-none placeholder:text-gray-600"
            autoComplete="off"
            spellCheck={false}
          />
          <span className="shrink-0 text-[10px] text-gray-600">
            {allSymbols.length} symbols
          </span>
        </div>

        {/* Results list */}
        <div ref={listRef} className="max-h-[50vh] overflow-y-auto">
          {results.length === 0 && (
            <div className="px-4 py-6 text-center text-sm text-gray-500">
              {query ? "No matching symbols" : "No symbols found in open files"}
            </div>
          )}

          {results.map((result, i) => {
            const isSelected = i === selectedIndex;
            const matchSet = new Set(result.matchIndices);
            const relPath = relativeTo(result.symbol.filePath, pinnedRoots);
            const fileName = relPath.split("/").pop() ?? relPath;

            return (
              <div
                key={`${result.symbol.filePath}:${result.symbol.line}:${result.symbol.name}`}
                className={`px-4 py-2 flex items-center gap-3 cursor-pointer transition-colors ${
                  isSelected ? "bg-[#094771]" : "hover:bg-[#2a2d2e]"
                }`}
                onClick={() => handleSelect(result.symbol)}
                onMouseEnter={() => setSelectedIndex(i)}
              >
                <KindBadge kind={result.symbol.kind} />
                <div className="min-w-0 flex-1 flex items-baseline gap-2 overflow-hidden">
                  {matchSet.size > 0 ? (
                    <HighlightedText
                      text={result.symbol.name}
                      matchIndices={matchSet}
                      className="text-sm text-white font-medium shrink-0"
                    />
                  ) : (
                    <span className="text-sm text-white font-medium shrink-0">
                      {result.symbol.name}
                    </span>
                  )}
                  <span className="text-xs text-gray-500 truncate">
                    {fileName}:{result.symbol.line}
                  </span>
                </div>
                <span
                  className={`shrink-0 text-[10px] ${KIND_COLORS[result.symbol.kind] ?? "text-gray-500"}`}
                >
                  {result.symbol.kind}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
