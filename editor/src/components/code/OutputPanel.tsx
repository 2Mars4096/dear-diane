import { useState, useEffect, useRef, useCallback, type JSX } from "react";
import { Trash2, Search, Copy, Download, X } from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeFs } from "../../lib/electronBridge";

interface OutputChannel {
  id: string;
  label: string;
  logs: string[];
}

const DEFAULT_CHANNELS: OutputChannel[] = [
  { id: "extension-host", label: "Extension Host", logs: [] },
  { id: "dan-server", label: "DAN Server", logs: [] },
  { id: "git", label: "Git", logs: [] },
];

export function logToOutput(channel: string, message: string) {
  window.dispatchEvent(
    new CustomEvent("output:log", { detail: { channel, message } }),
  );
}

// ---------------------------------------------------------------------------
// Actionable log links — parse file:line references into clickable spans
// ---------------------------------------------------------------------------

const FILE_LINE_PATTERN = /((?:(?:\/|\.\.?\/|[a-zA-Z]:[\\/])[\w./@\\-]+)+(?:\.\w+)):(\d+)(?::(\d+))?/g;

function renderLogMessage(
  message: string,
  onNavigate: (path: string, line: number, col?: number) => void,
  searchQuery: string,
): JSX.Element | string {
  const parts: (string | JSX.Element)[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let keyIdx = 0;

  FILE_LINE_PATTERN.lastIndex = 0;
  while ((match = FILE_LINE_PATTERN.exec(message)) !== null) {
    const [fullMatch, filePath, line, col] = match;
    const start = match.index;

    if (start > lastIndex) {
      parts.push(message.slice(lastIndex, start));
    }

    parts.push(
      <span
        key={`link-${keyIdx++}`}
        className="text-blue-400 hover:text-blue-300 hover:underline cursor-pointer"
        onClick={() => onNavigate(filePath, parseInt(line), col ? parseInt(col) : undefined)}
      >
        {fullMatch}
      </span>,
    );

    lastIndex = start + fullMatch.length;
  }

  if (lastIndex < message.length) {
    parts.push(message.slice(lastIndex));
  }

  if (parts.length <= 1 && !searchQuery) return message;

  if (searchQuery) {
    return <>{highlightParts(parts.length ? parts : [message], searchQuery)}</>;
  }

  return <>{parts}</>;
}

function highlightParts(
  parts: (string | JSX.Element)[],
  query: string,
): (string | JSX.Element)[] {
  if (!query) return parts;
  const lowerQ = query.toLowerCase();
  const result: (string | JSX.Element)[] = [];
  let keyIdx = 0;

  for (const part of parts) {
    if (typeof part !== "string") {
      result.push(part);
      continue;
    }
    const lower = part.toLowerCase();
    let cursor = 0;
    let pos = lower.indexOf(lowerQ, cursor);
    while (pos !== -1) {
      if (pos > cursor) result.push(part.slice(cursor, pos));
      result.push(
        <mark key={`hl-${keyIdx++}`} className="bg-yellow-500/40 text-inherit rounded-sm px-0.5">
          {part.slice(pos, pos + query.length)}
        </mark>,
      );
      cursor = pos + query.length;
      pos = lower.indexOf(lowerQ, cursor);
    }
    if (cursor < part.length) result.push(part.slice(cursor));
  }
  return result;
}

// ---------------------------------------------------------------------------
// OutputPanel
// ---------------------------------------------------------------------------

export default function OutputPanel() {
  const [channels, setChannels] = useState<OutputChannel[]>(DEFAULT_CHANNELS);
  const [activeChannel, setActiveChannel] = useState("extension-host");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchVisible, setSearchVisible] = useState(false);
  const [copyToast, setCopyToast] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handler = (e: Event) => {
      const { channel, message } = (e as CustomEvent).detail;
      const ts = new Date().toLocaleTimeString();
      setChannels(prev =>
        prev.map(c =>
          c.id === channel
            ? { ...c, logs: [...c.logs, `[${ts}] ${message}`] }
            : c,
        ),
      );
    };
    window.addEventListener("output:log", handler);
    return () => window.removeEventListener("output:log", handler);
  }, []);

  const active = channels.find(c => c.id === activeChannel);

  const filteredLogs = searchQuery
    ? (active?.logs ?? []).filter(l => l.toLowerCase().includes(searchQuery.toLowerCase()))
    : (active?.logs ?? []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filteredLogs.length]);

  // Cmd+F to toggle search within the panel
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "f") {
        const panel = scrollRef.current?.closest("[data-output-panel]");
        if (panel) {
          e.preventDefault();
          e.stopPropagation();
          setSearchVisible(true);
          requestAnimationFrame(() => searchInputRef.current?.focus());
        }
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, []);

  const clearChannel = () => {
    setChannels(prev =>
      prev.map(c => (c.id === activeChannel ? { ...c, logs: [] } : c)),
    );
  };

  const formatLogs = useCallback(() => {
    return filteredLogs.join("\n");
  }, [filteredLogs]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(formatLogs());
    setCopyToast(true);
    setTimeout(() => setCopyToast(false), 1500);
  }, [formatLogs]);

  const handleExport = useCallback(() => {
    const text = formatLogs();
    const filename = `dan-output-${activeChannel}-${Date.now()}.log`;
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }, [formatLogs, activeChannel]);

  const handleNavigate = useCallback(async (filePath: string, line: number, col?: number) => {
    const content = await nativeFs.readFile(filePath);
    if (content !== null) {
      useCodeStore.getState().openFile(filePath, content);
      setTimeout(() => {
        window.dispatchEvent(
          new CustomEvent("editor:goToLine", {
            detail: { lineNumber: line, column: col ?? 1 },
          }),
        );
      }, 100);
    }
  }, []);

  const closeSearch = () => {
    setSearchVisible(false);
    setSearchQuery("");
  };

  return (
    <div data-output-panel className="flex h-full flex-col bg-[#1e1e1e] text-gray-300">
      {/* Header */}
      <div className="flex items-center gap-1 px-2 py-1 border-b border-[#3c3c3c]">
        <select
          value={activeChannel}
          onChange={e => setActiveChannel(e.target.value)}
          className="bg-[#2d2d2d] text-xs text-gray-300 border border-[#3c3c3c] rounded px-2 py-0.5 outline-none focus:border-[#007acc]"
        >
          {channels.map(c => (
            <option key={c.id} value={c.id}>{c.label}</option>
          ))}
        </select>

        <div className="ml-auto flex items-center gap-0.5">
          <button
            onClick={() => {
              setSearchVisible(v => !v);
              if (!searchVisible) requestAnimationFrame(() => searchInputRef.current?.focus());
              else setSearchQuery("");
            }}
            className={`p-1 transition-colors rounded ${searchVisible ? "text-blue-400" : "text-gray-500 hover:text-gray-300"}`}
            title="Search Output (⌘F)"
          >
            <Search size={12} />
          </button>
          <button
            onClick={handleCopy}
            className="p-1 text-gray-500 hover:text-gray-300 transition-colors relative"
            title="Copy Output"
          >
            <Copy size={12} />
            {copyToast && (
              <span className="absolute -top-5 left-1/2 -translate-x-1/2 text-[10px] bg-[#333] text-green-400 px-1.5 py-0.5 rounded whitespace-nowrap">
                Copied
              </span>
            )}
          </button>
          <button
            onClick={handleExport}
            className="p-1 text-gray-500 hover:text-gray-300 transition-colors"
            title="Export to File"
          >
            <Download size={12} />
          </button>
          <button
            onClick={clearChannel}
            className="p-1 text-gray-500 hover:text-gray-300 transition-colors"
            title="Clear Output"
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {/* Search bar */}
      {searchVisible && (
        <div className="flex items-center gap-1.5 px-2 py-1 border-b border-[#3c3c3c] bg-[#252526]">
          <Search size={11} className="text-gray-500 shrink-0" />
          <input
            ref={searchInputRef}
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => { if (e.key === "Escape") closeSearch(); }}
            placeholder="Filter output..."
            className="flex-1 bg-transparent text-xs text-gray-300 outline-none placeholder-gray-600"
          />
          {searchQuery && (
            <span className="text-[10px] text-gray-500 shrink-0">
              {filteredLogs.length} match{filteredLogs.length !== 1 ? "es" : ""}
            </span>
          )}
          <button onClick={closeSearch} className="p-0.5 text-gray-500 hover:text-gray-300">
            <X size={11} />
          </button>
        </div>
      )}

      {/* Log entries */}
      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-y-auto px-2 py-1 font-mono text-[11px] leading-relaxed"
      >
        {filteredLogs.map((log, i) => (
          <div key={i} className="text-gray-400 whitespace-pre-wrap">
            {renderLogMessage(log, handleNavigate, searchQuery)}
          </div>
        ))}
        {filteredLogs.length === 0 && (
          <div className="text-gray-600 text-xs py-4 text-center">
            {searchQuery ? "No matching output" : "No output"}
          </div>
        )}
      </div>
    </div>
  );
}
