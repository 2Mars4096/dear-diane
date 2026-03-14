import React, { useMemo } from "react";
import { ChevronRight } from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";

// ---------------------------------------------------------------------------
// Symbol extraction for breadcrumb navigation
// ---------------------------------------------------------------------------

interface BreadcrumbSymbol {
  name: string;
  kind: "function" | "class" | "method" | "interface" | "type" | "enum" | "variable" | "module";
  startLine: number;
}

const PATTERNS: Record<string, Array<{ re: RegExp; kind: BreadcrumbSymbol["kind"] }>> = {
  typescript: [
    { re: /^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)/m, kind: "class" },
    { re: /^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)/m, kind: "function" },
    { re: /^\s*(?:export\s+)?interface\s+(\w+)/m, kind: "interface" },
    { re: /^\s*(?:export\s+)?type\s+(\w+)/m, kind: "type" },
    { re: /^\s*(?:export\s+)?enum\s+(\w+)/m, kind: "enum" },
    { re: /^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)/m, kind: "variable" },
    { re: /^\s+(?:async\s+)?(\w+)\s*\(/m, kind: "method" },
  ],
  javascript: [
    { re: /^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)/m, kind: "class" },
    { re: /^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)/m, kind: "function" },
    { re: /^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)/m, kind: "variable" },
    { re: /^\s+(?:async\s+)?(\w+)\s*\(/m, kind: "method" },
  ],
  python: [
    { re: /^class\s+(\w+)/m, kind: "class" },
    { re: /^def\s+(\w+)/m, kind: "function" },
    { re: /^async\s+def\s+(\w+)/m, kind: "function" },
    { re: /^\s+def\s+(\w+)/m, kind: "method" },
  ],
  rust: [
    { re: /^\s*(?:pub\s+)?struct\s+(\w+)/m, kind: "class" },
    { re: /^\s*(?:pub\s+)?enum\s+(\w+)/m, kind: "enum" },
    { re: /^\s*(?:pub\s+)?trait\s+(\w+)/m, kind: "interface" },
    { re: /^\s*(?:pub\s+)?fn\s+(\w+)/m, kind: "function" },
    { re: /^\s*impl(?:<[^>]+>)?\s+(\w+)/m, kind: "module" },
  ],
  go: [
    { re: /^func\s+(\w+)\s*\(/m, kind: "function" },
    { re: /^func\s+\([^)]+\)\s+(\w+)\s*\(/m, kind: "method" },
    { re: /^type\s+(\w+)\s+struct/m, kind: "class" },
    { re: /^type\s+(\w+)\s+interface/m, kind: "interface" },
  ],
};

function extractSymbolsForBreadcrumb(content: string, language: string): BreadcrumbSymbol[] {
  const patterns = PATTERNS[language] ?? PATTERNS.typescript ?? [];
  const lines = content.split("\n");
  const symbols: BreadcrumbSymbol[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    for (const { re, kind } of patterns) {
      const m = re.exec(line);
      if (m?.[1]) {
        symbols.push({ name: m[1], kind, startLine: i + 1 });
        break;
      }
    }
  }
  return symbols;
}

// ---------------------------------------------------------------------------
// Breadcrumb component
// ---------------------------------------------------------------------------

interface BreadcrumbsProps {
  filePath: string;
  language: string;
  content: string;
}

const KIND_COLORS: Record<string, string> = {
  function: "text-yellow-400",
  class: "text-orange-400",
  method: "text-purple-400",
  interface: "text-cyan-400",
  type: "text-cyan-400",
  enum: "text-green-400",
  variable: "text-blue-400",
  module: "text-red-400",
};

export default function Breadcrumbs({ filePath, language, content }: BreadcrumbsProps) {
  const cursorLine = useCodeStore((s) => s.cursorPosition.lineNumber);
  const segments = filePath.replace(/\\/g, "/").split("/").filter(Boolean);

  const symbols = useMemo(
    () => extractSymbolsForBreadcrumb(content, language),
    [content, language],
  );

  const activeSymbol = useMemo(() => {
    if (symbols.length === 0) return null;
    let best: BreadcrumbSymbol | null = null;
    for (const sym of symbols) {
      if (sym.startLine <= cursorLine) best = sym;
      else break;
    }
    return best;
  }, [symbols, cursorLine]);

  const handleSegmentClick = (index: number) => {
    const partialPath = segments.slice(0, index + 1).join("/");
    window.dispatchEvent(
      new CustomEvent("code:revealInExplorer", { detail: { path: "/" + partialPath } }),
    );
  };

  const handleSymbolClick = (sym: BreadcrumbSymbol) => {
    window.dispatchEvent(
      new CustomEvent("editor:goToLine", {
        detail: { lineNumber: sym.startLine, column: 1 },
      }),
    );
  };

  return (
    <div className="flex items-center px-3 py-0.5 text-[11px] text-gray-500 bg-[#1e1e1e] overflow-x-auto whitespace-nowrap scrollbar-none">
      {segments.map((seg, i) => (
        <React.Fragment key={i}>
          {i > 0 && (
            <ChevronRight
              size={10}
              className="mx-0.5 text-gray-700 shrink-0"
            />
          )}
          <button
            className="hover:text-gray-300 truncate max-w-[120px]"
            onClick={() => handleSegmentClick(i)}
          >
            {seg}
          </button>
        </React.Fragment>
      ))}
      {activeSymbol && (
        <>
          <ChevronRight
            size={10}
            className="mx-0.5 text-gray-700 shrink-0"
          />
          <button
            className={`hover:text-gray-300 font-medium ${KIND_COLORS[activeSymbol.kind] ?? "text-blue-400"}`}
            onClick={() => handleSymbolClick(activeSymbol)}
          >
            {activeSymbol.name}
          </button>
        </>
      )}
    </div>
  );
}
