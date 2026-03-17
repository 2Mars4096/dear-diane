import { useState, useMemo, useCallback, useEffect } from "react";
import {
  FunctionSquare,
  Box,
  Hash,
  Type,
  List,
  Braces,
  Variable,
  Package,
  ChevronRight,
  ChevronDown,
  Filter,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeLsp, isElectron } from "../../lib/electronBridge";

// ---------------------------------------------------------------------------
// Document symbol types
// ---------------------------------------------------------------------------

export type SymbolKind =
  | "class"
  | "function"
  | "method"
  | "variable"
  | "interface"
  | "type"
  | "enum"
  | "property"
  | "constant"
  | "module";

export interface DocumentSymbol {
  name: string;
  kind: SymbolKind;
  detail?: string;
  range: { startLineNumber: number; endLineNumber: number };
  children: DocumentSymbol[];
}

// ---------------------------------------------------------------------------
// Symbol icons
// ---------------------------------------------------------------------------

const SYMBOL_ICONS: Record<SymbolKind, { icon: typeof FunctionSquare; color: string }> = {
  function: { icon: FunctionSquare, color: "text-yellow-400" },
  method: { icon: FunctionSquare, color: "text-purple-400" },
  class: { icon: Box, color: "text-orange-400" },
  interface: { icon: Type, color: "text-cyan-400" },
  type: { icon: Type, color: "text-cyan-400" },
  enum: { icon: List, color: "text-green-400" },
  variable: { icon: Variable, color: "text-blue-400" },
  constant: { icon: Hash, color: "text-blue-300" },
  property: { icon: Braces, color: "text-gray-400" },
  module: { icon: Package, color: "text-red-400" },
};

// ---------------------------------------------------------------------------
// LSP symbol conversion
// ---------------------------------------------------------------------------

const LSP_SYMBOL_KIND_MAP: Record<number, SymbolKind> = {
  2: "module",
  3: "module",
  4: "module",
  5: "class",
  6: "method",
  7: "property",
  8: "property",
  9: "method",
  10: "enum",
  11: "interface",
  12: "function",
  13: "variable",
  14: "constant",
  23: "class",
};

function lspKindToSymbolKind(kind: number): SymbolKind {
  return LSP_SYMBOL_KIND_MAP[kind] ?? "variable";
}

function convertLspSymbol(lspSym: any): DocumentSymbol {
  const range = lspSym.range ?? lspSym.location?.range;
  return {
    name: lspSym.name,
    kind: lspKindToSymbolKind(lspSym.kind),
    detail: lspSym.detail,
    range: {
      startLineNumber: (range?.start?.line ?? 0) + 1,
      endLineNumber: (range?.end?.line ?? 0) + 1,
    },
    children: Array.isArray(lspSym.children)
      ? lspSym.children.map(convertLspSymbol)
      : [],
  };
}

function convertLspSymbols(results: any[]): DocumentSymbol[] {
  return results.map(convertLspSymbol);
}

// ---------------------------------------------------------------------------
// Regex-based symbol extraction (fallback when LSP unavailable)
// ---------------------------------------------------------------------------

export function extractSymbols(
  content: string,
  language: string,
): DocumentSymbol[] {
  const lines = content.split("\n");
  const symbols: DocumentSymbol[] = [];
  let currentClass: DocumentSymbol | null = null;
  let classIndent = -1;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trimStart();
    const indent = line.length - trimmed.length;

    if (currentClass && indent <= classIndent && trimmed.length > 0) {
      currentClass.range.endLineNumber = i;
      symbols.push(currentClass);
      currentClass = null;
      classIndent = -1;
    }

    const sym = matchSymbol(trimmed, language, i + 1);
    if (!sym) continue;

    if (sym.kind === "class" || sym.kind === "interface" || sym.kind === "enum" || sym.kind === "module") {
      if (currentClass) {
        currentClass.range.endLineNumber = i;
        symbols.push(currentClass);
      }
      currentClass = sym;
      classIndent = indent;
    } else if (
      currentClass &&
      indent > classIndent &&
      (sym.kind === "method" || sym.kind === "function" || sym.kind === "property")
    ) {
      if (sym.kind === "function") sym.kind = "method";
      currentClass.children.push(sym);
    } else {
      symbols.push(sym);
    }
  }

  if (currentClass) {
    currentClass.range.endLineNumber = lines.length;
    symbols.push(currentClass);
  }

  return symbols;
}

function matchSymbol(
  line: string,
  language: string,
  lineNumber: number,
): DocumentSymbol | null {
  const patterns = LANG_PATTERNS[language] ?? LANG_PATTERNS.typescript ?? [];
  for (const { re, kind, detailGroup } of patterns) {
    const m = re.exec(line);
    if (m?.[1]) {
      return {
        name: m[1],
        kind,
        detail: detailGroup && m[detailGroup] ? m[detailGroup] : undefined,
        range: { startLineNumber: lineNumber, endLineNumber: lineNumber },
        children: [],
      };
    }
  }
  return null;
}

interface PatternDef {
  re: RegExp;
  kind: SymbolKind;
  detailGroup?: number;
}

const LANG_PATTERNS: Record<string, PatternDef[]> = {
  typescript: [
    { re: /^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)/, kind: "class" },
    { re: /^(?:export\s+)?interface\s+(\w+)/, kind: "interface" },
    { re: /^(?:export\s+)?type\s+(\w+)/, kind: "type" },
    { re: /^(?:export\s+)?enum\s+(\w+)/, kind: "enum" },
    { re: /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)/, kind: "function" },
    { re: /^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(/, kind: "function", detailGroup: 0 },
    { re: /^(?:export\s+)?(?:const|let|var)\s+(\w+)/, kind: "variable" },
    { re: /^(?:async\s+)?(\w+)\s*\(/, kind: "method" },
    { re: /^(?:get|set)\s+(\w+)\s*\(/, kind: "property" },
    { re: /^(?:static\s+)?(?:readonly\s+)?(\w+)\s*[:=]/, kind: "property" },
  ],
  javascript: [
    { re: /^(?:export\s+)?(?:default\s+)?class\s+(\w+)/, kind: "class" },
    { re: /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)/, kind: "function" },
    { re: /^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(/, kind: "function" },
    { re: /^(?:export\s+)?(?:const|let|var)\s+(\w+)/, kind: "variable" },
    { re: /^(?:async\s+)?(\w+)\s*\(/, kind: "method" },
  ],
  python: [
    { re: /^class\s+(\w+)/, kind: "class" },
    { re: /^(?:async\s+)?def\s+(\w+)/, kind: "function" },
    { re: /^(\w+)\s*=/, kind: "variable" },
  ],
  rust: [
    { re: /^(?:pub\s+)?struct\s+(\w+)/, kind: "class" },
    { re: /^(?:pub\s+)?enum\s+(\w+)/, kind: "enum" },
    { re: /^(?:pub\s+)?trait\s+(\w+)/, kind: "interface" },
    { re: /^impl(?:<[^>]+>)?\s+(\w+)/, kind: "module" },
    { re: /^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)/, kind: "function" },
    { re: /^(?:pub\s+)?mod\s+(\w+)/, kind: "module" },
    { re: /^(?:pub\s+)?(?:const|static)\s+(\w+)/, kind: "constant" },
    { re: /^(?:pub\s+)?type\s+(\w+)/, kind: "type" },
  ],
  go: [
    { re: /^type\s+(\w+)\s+struct/, kind: "class" },
    { re: /^type\s+(\w+)\s+interface/, kind: "interface" },
    { re: /^func\s+\([^)]+\)\s+(\w+)\s*\(/, kind: "method" },
    { re: /^func\s+(\w+)\s*\(/, kind: "function" },
    { re: /^type\s+(\w+)/, kind: "type" },
    { re: /^(?:var|const)\s+(\w+)/, kind: "variable" },
  ],
};

// ---------------------------------------------------------------------------
// Symbol tree renderer
// ---------------------------------------------------------------------------

function SymbolNode({
  symbol,
  depth,
  onNavigate,
  activeLineNumber,
}: {
  symbol: DocumentSymbol;
  depth: number;
  onNavigate: (line: number) => void;
  activeLineNumber: number;
}) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = symbol.children.length > 0;
  const iconDef = SYMBOL_ICONS[symbol.kind] ?? SYMBOL_ICONS.variable;
  const Icon = iconDef.icon;

  const isActive =
    activeLineNumber >= symbol.range.startLineNumber &&
    activeLineNumber <= symbol.range.endLineNumber;

  return (
    <div>
      <button
        className={`flex items-center gap-1 w-full px-1 py-0.5 text-left text-[11px] hover:bg-[#2a2d2e] rounded-sm transition-colors ${
          isActive ? "bg-[#094771]/50 text-white" : "text-gray-300"
        }`}
        style={{ paddingLeft: depth * 12 + 4 }}
        onClick={() => onNavigate(symbol.range.startLineNumber)}
      >
        {hasChildren ? (
          <button
            className="shrink-0 p-0"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
          >
            {expanded ? (
              <ChevronDown size={10} className="text-gray-500" />
            ) : (
              <ChevronRight size={10} className="text-gray-500" />
            )}
          </button>
        ) : (
          <span className="w-[10px] shrink-0" />
        )}
        <Icon size={12} className={`shrink-0 ${iconDef.color}`} />
        <span className="truncate">{symbol.name}</span>
        <span className="ml-auto text-[9px] text-gray-600 shrink-0">
          {symbol.range.startLineNumber}
        </span>
      </button>
      {hasChildren && expanded && (
        <div>
          {symbol.children.map((child, i) => (
            <SymbolNode
              key={`${child.name}-${i}`}
              symbol={child}
              depth={depth + 1}
              onNavigate={onNavigate}
              activeLineNumber={activeLineNumber}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Outline view panel
// ---------------------------------------------------------------------------

export default function OutlineView() {
  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const openFiles = useCodeStore((s) => s.openFiles);
  const cursorLine = useCodeStore((s) => s.cursorPosition.lineNumber);
  const [filter, setFilter] = useState("");
  const [sortByPosition, setSortByPosition] = useState(true);

  const activeFile = openFiles.find((f) => f.path === activeFilePath);

  const [lspSymbols, setLspSymbols] = useState<DocumentSymbol[] | null>(null);

  useEffect(() => {
    if (!activeFile || !isElectron()) {
      setLspSymbols(null);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const result = await nativeLsp.documentSymbol({ filePath: activeFile.path });
        if (result && Array.isArray(result) && result.length > 0) {
          setLspSymbols(convertLspSymbols(result));
        } else {
          setLspSymbols(null);
        }
      } catch {
        setLspSymbols(null);
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [activeFile?.path, activeFile?.content]);

  const symbols = useMemo(() => {
    if (lspSymbols) return lspSymbols;
    if (!activeFile) return [];
    return extractSymbols(activeFile.content, activeFile.language);
  }, [lspSymbols, activeFile?.content, activeFile?.language]);

  const filtered = useMemo(() => {
    if (!filter) return symbols;
    const lc = filter.toLowerCase();
    function filterTree(syms: DocumentSymbol[]): DocumentSymbol[] {
      return syms
        .map((s) => {
          const childMatches = filterTree(s.children);
          if (s.name.toLowerCase().includes(lc) || childMatches.length > 0) {
            return { ...s, children: childMatches };
          }
          return null;
        })
        .filter(Boolean) as DocumentSymbol[];
    }
    return filterTree(symbols);
  }, [symbols, filter]);

  const sorted = useMemo(() => {
    if (sortByPosition) return filtered;
    const clone = [...filtered];
    clone.sort((a, b) => a.name.localeCompare(b.name));
    return clone;
  }, [filtered, sortByPosition]);

  const navigateTo = useCallback((lineNumber: number) => {
    window.dispatchEvent(
      new CustomEvent("editor:goToLine", {
        detail: { lineNumber, column: 1 },
      }),
    );
  }, []);

  if (!activeFile) {
    return (
      <div className="h-full flex items-center justify-center text-gray-600 text-xs px-4 text-center">
        No file open
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-gray-900">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between">
        <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
          Outline
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setSortByPosition((v) => !v)}
            className={`p-0.5 rounded text-[10px] ${
              sortByPosition
                ? "text-gray-500"
                : "text-blue-400 bg-blue-400/10"
            }`}
            title={sortByPosition ? "Sort by position" : "Sort alphabetically"}
          >
            A↓
          </button>
        </div>
      </div>

      {/* Filter */}
      <div className="px-2 py-1 border-b border-gray-800">
        <div className="relative">
          <Filter
            size={10}
            className="absolute left-1.5 top-1/2 -translate-y-1/2 text-gray-600"
          />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter symbols..."
            className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-0.5 pl-5 text-[11px] text-gray-200 placeholder-gray-600 focus:border-blue-500/50 focus:outline-none"
          />
        </div>
      </div>

      {/* Symbol tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {sorted.length === 0 ? (
          <div className="px-3 py-4 text-center text-[11px] text-gray-600">
            {filter ? "No matching symbols" : "No symbols found"}
          </div>
        ) : (
          sorted.map((sym, i) => (
            <SymbolNode
              key={`${sym.name}-${i}`}
              symbol={sym}
              depth={0}
              onNavigate={navigateTo}
              activeLineNumber={cursorLine}
            />
          ))
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-1 border-t border-gray-800 text-[10px] text-gray-600">
        {symbols.length} symbol{symbols.length !== 1 ? "s" : ""}
      </div>
    </div>
  );
}
