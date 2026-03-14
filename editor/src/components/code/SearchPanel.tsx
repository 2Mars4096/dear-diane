import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Search,
  CaseSensitive,
  WholeWord,
  Regex,
  ChevronDown,
  ChevronRight,
  FileCode,
  FileText,
  File,
  Braces,
  Image,
  Settings,
  Loader2,
  X,
  Replace,
  FolderOpen,
  TextSelect,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeSearch, nativeFs } from "../../lib/electronBridge";

// ─── Types ──────────────────────────────────────────────────────────────

interface SearchMatch {
  filePath: string;
  lineNumber: number;
  lineText: string;
  matchStart: number;
  matchEnd: number;
}

interface SearchFileGroup {
  filePath: string;
  relativePath: string;
  matches: SearchMatch[];
}

// ─── Ripgrep JSON Parser ────────────────────────────────────────────────

function parseRipgrepJson(raw: string, pinnedRoots: string[]): SearchFileGroup[] {
  const groups = new Map<string, SearchFileGroup>();
  const lines = raw.split("\n").filter(Boolean);

  for (const line of lines) {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(line);
    } catch {
      continue;
    }

    if (parsed.type !== "match") continue;

    const data = parsed.data as {
      path: { text: string };
      lines: { text: string };
      line_number: number;
      submatches: Array<{ start: number; end: number }>;
    };

    const filePath = data.path.text;
    const lineText = data.lines.text.replace(/\n$/, "");

    for (const sub of data.submatches) {
      if (!groups.has(filePath)) {
        let relativePath = filePath;
        for (const root of pinnedRoots) {
          if (filePath.startsWith(root)) {
            relativePath = filePath.slice(root.length).replace(/^\//, "");
            break;
          }
        }
        groups.set(filePath, { filePath, relativePath, matches: [] });
      }

      groups.get(filePath)!.matches.push({
        filePath,
        lineNumber: data.line_number,
        lineText,
        matchStart: sub.start,
        matchEnd: sub.end,
      });
    }
  }

  return Array.from(groups.values()).sort((a, b) =>
    a.relativePath.localeCompare(b.relativePath),
  );
}

// ─── Utilities ──────────────────────────────────────────────────────────

const MAX_DISPLAYED = 200;

function extension(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}

function filename(path: string): string {
  return path.split("/").pop() ?? path;
}

function getFileIcon(name: string) {
  const ext = extension(name);
  switch (ext) {
    case "ts": case "tsx": case "js": case "jsx":
      return <FileCode size={14} className="shrink-0 text-blue-400" />;
    case "py":
      return <FileCode size={14} className="shrink-0 text-green-400" />;
    case "json":
      return <Braces size={14} className="shrink-0 text-yellow-400" />;
    case "md": case "mdx": case "txt":
      return <FileText size={14} className="shrink-0 text-gray-400" />;
    case "png": case "jpg": case "jpeg": case "gif": case "svg": case "webp":
      return <Image size={14} className="shrink-0 text-purple-400" />;
    case "env": case "toml": case "yaml": case "yml":
      return <Settings size={14} className="shrink-0 text-gray-400" />;
    default:
      return <File size={14} className="shrink-0 text-gray-500" />;
  }
}

interface SelectionSearchResult {
  line: number;
  column: number;
  matchLength: number;
  text: string;
}

function searchInSelectionText(
  query: string,
  text: string,
  startLine: number,
  options: { caseSensitive: boolean; wholeWord: boolean; regex: boolean },
): SelectionSearchResult[] {
  const lines = text.split("\n");
  const results: SelectionSearchResult[] = [];

  let pattern: RegExp;
  try {
    let patternStr = options.regex
      ? query
      : query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (options.wholeWord) patternStr = `\\b${patternStr}\\b`;
    pattern = new RegExp(patternStr, options.caseSensitive ? "g" : "gi");
  } catch {
    return [];
  }

  for (let i = 0; i < lines.length; i++) {
    let match;
    while ((match = pattern.exec(lines[i])) !== null) {
      results.push({
        line: startLine + i,
        column: match.index,
        matchLength: match[0].length,
        text: lines[i],
      });
    }
  }

  return results;
}

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

// ─── Toggle Button ──────────────────────────────────────────────────────

function ToggleButton({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      className={`flex h-[22px] w-[26px] items-center justify-center rounded-[3px] text-xs transition ${
        active
          ? "bg-blue-600 text-white"
          : "bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200"
      }`}
      onClick={onClick}
      title={title}
    >
      {children}
    </button>
  );
}

// ─── Highlighted Line ───────────────────────────────────────────────────

function HighlightedLine({ text, start, end }: { text: string; start: number; end: number }) {
  const clampedStart = Math.max(0, Math.min(start, text.length));
  const clampedEnd = Math.max(clampedStart, Math.min(end, text.length));

  const before = text.slice(0, clampedStart);
  const match = text.slice(clampedStart, clampedEnd);
  const after = text.slice(clampedEnd);

  return (
    <span className="whitespace-pre font-mono text-[12px] leading-tight">
      <span className="text-gray-300">{before}</span>
      <span className="rounded-sm bg-yellow-500/30 text-yellow-200">{match}</span>
      <span className="text-gray-300">{after}</span>
    </span>
  );
}

// ─── File Group ─────────────────────────────────────────────────────────

function FileGroup({
  group,
  onMatchClick,
  showReplace,
  onReplaceMatch,
  onReplaceFile,
}: {
  group: SearchFileGroup;
  onMatchClick: (match: SearchMatch) => void;
  showReplace: boolean;
  onReplaceMatch?: (match: SearchMatch) => void;
  onReplaceFile?: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="mb-0.5">
      <div
        className="group flex w-full cursor-pointer items-center gap-1.5 px-2 py-[3px] text-left hover:bg-gray-800/60"
        onClick={() => setCollapsed(!collapsed)}
      >
        {collapsed ? (
          <ChevronRight size={12} className="shrink-0 text-gray-500" />
        ) : (
          <ChevronDown size={12} className="shrink-0 text-gray-500" />
        )}
        {getFileIcon(filename(group.filePath))}
        <span className="min-w-0 flex-1 truncate text-xs text-gray-300">
          {filename(group.filePath)}
        </span>
        <span className="text-[10px] text-gray-500">{group.relativePath}</span>
        {showReplace && onReplaceFile && (
          <button
            className="shrink-0 rounded p-0.5 opacity-0 hover:bg-gray-700 group-hover:opacity-100"
            onClick={(e) => { e.stopPropagation(); onReplaceFile(); }}
            title="Replace all in this file"
          >
            <Replace size={12} className="text-gray-400" />
          </button>
        )}
        <span className="ml-1 shrink-0 rounded-full bg-gray-700 px-1.5 py-[1px] text-[10px] text-gray-400">
          {group.matches.length}
        </span>
      </div>

      {!collapsed &&
        group.matches.map((match, i) => (
          <div
            key={`${match.lineNumber}-${match.matchStart}-${i}`}
            className="group/match flex w-full items-start gap-1.5 px-2 py-[2px] pl-7 text-left hover:bg-gray-800/60"
          >
            <button
              className="flex min-w-0 flex-1 items-start gap-1.5 text-left"
              onClick={() => onMatchClick(match)}
            >
              <span className="w-8 shrink-0 select-none text-right font-mono text-[11px] text-gray-500">
                {match.lineNumber}
              </span>
              <span className="min-w-0 overflow-hidden text-ellipsis">
                <HighlightedLine
                  text={match.lineText}
                  start={match.matchStart}
                  end={match.matchEnd}
                />
              </span>
            </button>
            {showReplace && onReplaceMatch && (
              <button
                className="shrink-0 rounded p-0.5 opacity-0 hover:bg-gray-700 group-hover/match:opacity-100"
                onClick={(e) => { e.stopPropagation(); onReplaceMatch(match); }}
                title="Replace this match"
              >
                <Replace size={11} className="text-gray-400" />
              </button>
            )}
          </div>
        ))}
    </div>
  );
}

// ─── Main Component ─────────────────────────────────────────────────────

export default function SearchPanel() {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const openFile = useCodeStore((s) => s.openFile);
  const openFiles = useCodeStore((s) => s.openFiles);
  const reloadFileContent = useCodeStore((s) => s.reloadFileContent);

  const [query, setQuery] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [showReplace, setShowReplace] = useState(false);
  const [includeGlob, setIncludeGlob] = useState("");
  const [excludeGlob, setExcludeGlob] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);
  const [useRegex, setUseRegex] = useState(false);

  const [searchInSelection, setSearchInSelection] = useState(false);
  const [selectionText, setSelectionText] = useState<string | null>(null);
  const [selectionFile, setSelectionFile] = useState<string | null>(null);
  const [selectionRange, setSelectionRange] = useState<{ start: number; end: number } | null>(null);

  const [results, setResults] = useState<SearchFileGroup[]>([]);
  const [selectionResults, setSelectionResults] = useState<SelectionSearchResult[]>([]);
  const [totalMatches, setTotalMatches] = useState(0);
  const [searching, setSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [replaceMessage, setReplaceMessage] = useState<string | null>(null);

  const debouncedQuery = useDebouncedValue(query, 300);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef(0);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ text: string; filePath: string; startLine: number; endLine: number }>).detail;
      if (detail.text) {
        setSelectionText(detail.text);
        setSelectionFile(detail.filePath);
        setSelectionRange({ start: detail.startLine, end: detail.endLine });
      } else {
        setSelectionText(null);
        setSelectionFile(null);
        setSelectionRange(null);
      }
    };
    window.addEventListener("editor:selectionChange", handler);
    return () => window.removeEventListener("editor:selectionChange", handler);
  }, []);

  const runSearch = useCallback(
    async (q: string) => {
      if (!q.trim() || pinnedRoots.length === 0) {
        setResults([]);
        setTotalMatches(0);
        if (q.trim()) setHasSearched(true);
        return;
      }

      const searchId = ++abortRef.current;
      setSearching(true);
      setHasSearched(true);

      let searchQuery = q;
      if (wholeWord && !useRegex) {
        searchQuery = `\\b${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`;
      }

      const allGroups: SearchFileGroup[] = [];
      let total = 0;

      for (const root of pinnedRoots) {
        if (searchId !== abortRef.current) return;

        let glob: string | undefined;
        const globParts: string[] = [];
        if (includeGlob.trim()) {
          globParts.push(
            ...includeGlob.split(",").map((g) => g.trim()).filter(Boolean),
          );
        }
        if (globParts.length === 1) glob = globParts[0];
        else if (globParts.length > 1) glob = `{${globParts.join(",")}}`;

        try {
          const raw = await nativeSearch.ripgrep({
            query: searchQuery,
            cwd: root,
            glob,
            caseSensitive,
            maxResults: MAX_DISPLAYED,
          });

          if (searchId !== abortRef.current) return;
          const groups = parseRipgrepJson(raw, pinnedRoots);
          allGroups.push(...groups);
          for (const g of groups) total += g.matches.length;
        } catch {
          // search error for this root — skip
        }
      }

      if (searchId !== abortRef.current) return;

      let capped = allGroups;
      let capTotal = total;
      if (total > MAX_DISPLAYED) {
        let count = 0;
        const trimmed: SearchFileGroup[] = [];
        for (const g of allGroups) {
          if (count >= MAX_DISPLAYED) break;
          const remaining = MAX_DISPLAYED - count;
          if (g.matches.length <= remaining) {
            trimmed.push(g);
            count += g.matches.length;
          } else {
            trimmed.push({ ...g, matches: g.matches.slice(0, remaining) });
            count = MAX_DISPLAYED;
          }
        }
        capped = trimmed;
        capTotal = total;
      }

      setResults(capped);
      setTotalMatches(capTotal);
      setSearching(false);
    },
    [pinnedRoots, caseSensitive, wholeWord, useRegex, includeGlob],
  );

  useEffect(() => {
    if (searchInSelection && selectionText && selectionRange) {
      if (!debouncedQuery.trim()) {
        setSelectionResults([]);
        setTotalMatches(0);
        return;
      }
      setHasSearched(true);
      const hits = searchInSelectionText(debouncedQuery, selectionText, selectionRange.start, {
        caseSensitive,
        wholeWord,
        regex: useRegex,
      });
      setSelectionResults(hits);
      setTotalMatches(hits.length);
      setResults([]);
    } else {
      setSelectionResults([]);
      runSearch(debouncedQuery);
    }
  }, [debouncedQuery, runSearch, searchInSelection, selectionText, selectionRange, caseSensitive, wholeWord, useRegex]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      abortRef.current++;
      runSearch(query);
    }
  };

  const handleMatchClick = useCallback(
    async (match: SearchMatch) => {
      const content = await nativeFs.readFile(match.filePath);
      openFile(match.filePath, content ?? "");
    },
    [openFile],
  );

  const clearSearch = () => {
    setQuery("");
    setResults([]);
    setTotalMatches(0);
    setHasSearched(false);
    searchInputRef.current?.focus();
  };

  const reloadOpenFile = useCallback(async (filePath: string) => {
    if (openFiles.some((f) => f.path === filePath)) {
      const content = await nativeFs.readFile(filePath);
      if (content !== null) reloadFileContent(filePath, content);
    }
  }, [openFiles, reloadFileContent]);

  const showTemporaryMessage = (msg: string) => {
    setReplaceMessage(msg);
    setTimeout(() => setReplaceMessage(null), 3000);
  };

  const handleReplaceSingle = useCallback(async (match: SearchMatch, group: SearchFileGroup) => {
    const result = await nativeSearch.replaceInFile(match.filePath, [{
      lineNumber: match.lineNumber,
      matchStart: match.matchStart,
      matchEnd: match.matchEnd,
      replacement: replaceText,
    }]);

    if (result.success) {
      setResults((prev) => prev.map((g) => {
        if (g.filePath !== group.filePath) return g;
        const newMatches = g.matches.filter((m) => m !== match);
        return newMatches.length === 0 ? null : { ...g, matches: newMatches };
      }).filter(Boolean) as SearchFileGroup[]);
      setTotalMatches((prev) => prev - 1);
      await reloadOpenFile(match.filePath);
    }
  }, [replaceText, reloadOpenFile]);

  const handleReplaceFile = useCallback(async (group: SearchFileGroup) => {
    const replacements = group.matches.map((m) => ({
      lineNumber: m.lineNumber,
      matchStart: m.matchStart,
      matchEnd: m.matchEnd,
      replacement: replaceText,
    }));

    const result = await nativeSearch.replaceInFile(group.filePath, replacements);
    if (result.success) {
      const count = group.matches.length;
      setResults((prev) => prev.filter((g) => g.filePath !== group.filePath));
      setTotalMatches((prev) => prev - count);
      await reloadOpenFile(group.filePath);
      showTemporaryMessage(`Replaced ${count} match${count !== 1 ? "es" : ""} in ${filename(group.filePath)}`);
    }
  }, [replaceText, reloadOpenFile]);

  const handleReplaceAll = useCallback(async () => {
    if (results.length === 0) return;
    setReplacing(true);

    let totalReplaced = 0;
    let filesReplaced = 0;

    for (const group of results) {
      const replacements = group.matches.map((m) => ({
        lineNumber: m.lineNumber,
        matchStart: m.matchStart,
        matchEnd: m.matchEnd,
        replacement: replaceText,
      }));

      const result = await nativeSearch.replaceInFile(group.filePath, replacements);
      if (result.success) {
        totalReplaced += group.matches.length;
        filesReplaced++;
        await reloadOpenFile(group.filePath);
      }
    }

    setResults([]);
    setTotalMatches(0);
    setReplacing(false);
    showTemporaryMessage(`Replaced ${totalReplaced} match${totalReplaced !== 1 ? "es" : ""} in ${filesReplaced} file${filesReplaced !== 1 ? "s" : ""}`);
  }, [results, replaceText, reloadOpenFile]);

  useEffect(() => {
    searchInputRef.current?.focus();
  }, []);

  // ─── No pinned roots ───────────────────────────────────────────────
  if (pinnedRoots.length === 0) {
    return (
      <div className="flex h-full w-full flex-col bg-gray-900 text-gray-300">
        <div className="flex items-center border-b border-gray-800 px-3 py-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">
            Search
          </span>
        </div>
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center">
          <FolderOpen size={32} className="text-gray-600" />
          <p className="text-sm text-gray-400">Open a folder first</p>
          <p className="text-xs text-gray-500">
            Use the Explorer to add a folder to your workspace.
          </p>
        </div>
      </div>
    );
  }

  const displayedCount = results.reduce((s, g) => s + g.matches.length, 0);
  const fileCount = results.length;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-gray-900 text-gray-300">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-800 px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">
          Search
        </span>
        <div className="flex items-center gap-1">
          <button
            className={`rounded p-1 transition ${
              showReplace
                ? "bg-gray-700 text-gray-200"
                : "text-gray-500 hover:bg-gray-700/60 hover:text-gray-300"
            }`}
            title="Toggle Replace"
            onClick={() => setShowReplace(!showReplace)}
          >
            <Replace size={14} />
          </button>
        </div>
      </div>

      {/* Search Controls */}
      <div className="flex flex-col gap-1.5 border-b border-gray-800 px-3 py-2">
        {/* Query row */}
        <div className="flex items-center gap-1.5">
          <div className="relative flex flex-1 items-center">
            <Search size={13} className="pointer-events-none absolute left-2 text-gray-500" />
            <input
              ref={searchInputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Search"
              className="h-[26px] w-full rounded border border-gray-700 bg-gray-800 pl-7 pr-7 text-xs text-white placeholder:text-gray-500 focus:border-blue-500/60 focus:outline-none"
              spellCheck={false}
            />
            {query && (
              <button
                className="absolute right-1.5 text-gray-500 hover:text-gray-300"
                onClick={clearSearch}
              >
                <X size={13} />
              </button>
            )}
          </div>
          {hasSearched && !searching && totalMatches > 0 && (
            <span className="shrink-0 rounded-full bg-gray-700 px-1.5 py-[1px] text-[10px] text-gray-400">
              {totalMatches > MAX_DISPLAYED ? `${MAX_DISPLAYED}+` : totalMatches}
            </span>
          )}
        </div>

        {/* Toggles */}
        <div className="flex items-center gap-1">
          <ToggleButton
            active={caseSensitive}
            onClick={() => setCaseSensitive(!caseSensitive)}
            title="Match Case"
          >
            <CaseSensitive size={14} />
          </ToggleButton>
          <ToggleButton
            active={wholeWord}
            onClick={() => setWholeWord(!wholeWord)}
            title="Match Whole Word"
          >
            <WholeWord size={14} />
          </ToggleButton>
          <ToggleButton
            active={useRegex}
            onClick={() => setUseRegex(!useRegex)}
            title="Use Regular Expression"
          >
            <Regex size={14} />
          </ToggleButton>
          <div className="w-px h-4 bg-gray-700 mx-0.5" />
          <button
            onClick={() => setSearchInSelection((v) => !v)}
            className={`flex h-[22px] w-[26px] items-center justify-center rounded-[3px] text-xs transition ${
              searchInSelection
                ? "bg-blue-600 text-white"
                : selectionText
                  ? "bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200"
                  : "bg-gray-700 text-gray-600 cursor-not-allowed"
            }`}
            title={selectionText ? "Search in Selection" : "Search in Selection (select text first)"}
            disabled={!selectionText && !searchInSelection}
          >
            <TextSelect size={14} />
          </button>
        </div>

        {searchInSelection && selectionText && (
          <div className="flex items-center gap-1.5 text-[10px]">
            <span className="rounded bg-blue-600/20 text-blue-400 px-1.5 py-0.5">
              Searching in selection
            </span>
            {selectionFile && (
              <span className="text-gray-500 truncate">
                {filename(selectionFile)} L{selectionRange?.start}–{selectionRange?.end}
              </span>
            )}
          </div>
        )}

        {/* Replace row */}
        {showReplace && (
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                value={replaceText}
                onChange={(e) => setReplaceText(e.target.value)}
                placeholder="Replace"
                className="h-[26px] flex-1 rounded border border-gray-700 bg-gray-800 px-2 text-xs text-white placeholder:text-gray-500 focus:border-blue-500/60 focus:outline-none"
                spellCheck={false}
              />
              <button
                className="h-[26px] shrink-0 rounded border border-gray-700 bg-gray-700 px-2 text-[11px] text-gray-300 hover:bg-gray-600 disabled:opacity-50 disabled:hover:bg-gray-700"
                disabled={results.length === 0 || replacing}
                onClick={handleReplaceAll}
                title="Replace All"
              >
                {replacing ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  "Replace All"
                )}
              </button>
            </div>
            {replaceMessage && (
              <div className="text-[11px] text-green-400">{replaceMessage}</div>
            )}
          </div>
        )}

        {/* File filters */}
        <div className="flex flex-col gap-1">
          <input
            type="text"
            value={includeGlob}
            onChange={(e) => setIncludeGlob(e.target.value)}
            placeholder="Include files (e.g. *.ts, src/**)"
            className="h-[24px] w-full rounded border border-gray-700 bg-gray-800 px-2 text-[11px] text-white placeholder:text-gray-500 focus:border-blue-500/60 focus:outline-none"
            spellCheck={false}
          />
          <input
            type="text"
            value={excludeGlob}
            onChange={(e) => setExcludeGlob(e.target.value)}
            placeholder="Exclude files (e.g. node_modules, dist)"
            className="h-[24px] w-full rounded border border-gray-700 bg-gray-800 px-2 text-[11px] text-white placeholder:text-gray-500 focus:border-blue-500/60 focus:outline-none"
            spellCheck={false}
          />
        </div>
      </div>

      {/* Results area */}
      <div className="flex-1 overflow-y-auto [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-700 [&::-webkit-scrollbar]:w-1.5">
        {/* Loading */}
        {searching && (
          <div className="flex items-center gap-2 px-3 py-4 text-xs text-gray-500">
            <Loader2 size={14} className="animate-spin" />
            Searching…
          </div>
        )}

        {/* Initial state */}
        {!hasSearched && !searching && (
          <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
            <Search size={28} className="text-gray-600" />
            <p className="text-xs text-gray-500">
              Search across files in your workspace
            </p>
          </div>
        )}

        {/* No results */}
        {hasSearched && !searching && query.trim() && totalMatches === 0 && (
          <div className="px-3 py-4 text-xs text-gray-500">
            No results found for &lsquo;<span className="text-gray-400">{query}</span>&rsquo;
          </div>
        )}

        {/* Selection search results */}
        {searchInSelection && selectionResults.length > 0 && (
          <>
            <div className="px-3 py-1.5 text-[11px] text-gray-500">
              {selectionResults.length} result{selectionResults.length !== 1 ? "s" : ""} in selection
            </div>
            {selectionResults.map((hit, i) => (
              <button
                key={`${hit.line}-${hit.column}-${i}`}
                className="flex w-full items-start gap-1.5 px-2 py-[2px] pl-4 text-left hover:bg-gray-800/60"
                onClick={() => {
                  if (selectionFile) {
                    handleMatchClick({
                      filePath: selectionFile,
                      lineNumber: hit.line,
                      lineText: hit.text,
                      matchStart: hit.column,
                      matchEnd: hit.column + hit.matchLength,
                    });
                  }
                }}
              >
                <span className="w-8 shrink-0 select-none text-right font-mono text-[11px] text-gray-500">
                  {hit.line}
                </span>
                <span className="min-w-0 overflow-hidden text-ellipsis">
                  <HighlightedLine
                    text={hit.text}
                    start={hit.column}
                    end={hit.column + hit.matchLength}
                  />
                </span>
              </button>
            ))}
          </>
        )}

        {/* Selection search: no results */}
        {searchInSelection && hasSearched && query.trim() && selectionResults.length === 0 && !searching && (
          <div className="px-3 py-4 text-xs text-gray-500">
            No results in selection for &lsquo;<span className="text-gray-400">{query}</span>&rsquo;
          </div>
        )}

        {/* Results list */}
        {!searching && !searchInSelection && results.length > 0 && (
          <>
            <div className="px-3 py-1.5 text-[11px] text-gray-500">
              {totalMatches > MAX_DISPLAYED
                ? `${displayedCount} of ${totalMatches}+ results in ${fileCount} files (showing first ${MAX_DISPLAYED})`
                : `${totalMatches} result${totalMatches !== 1 ? "s" : ""} in ${fileCount} file${fileCount !== 1 ? "s" : ""}`}
            </div>
            {results.map((group) => (
              <FileGroup
                key={group.filePath}
                group={group}
                onMatchClick={handleMatchClick}
                showReplace={showReplace && !!replaceText}
                onReplaceMatch={(match) => handleReplaceSingle(match, group)}
                onReplaceFile={() => handleReplaceFile(group)}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
