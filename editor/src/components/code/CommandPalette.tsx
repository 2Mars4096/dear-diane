import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useCodeStore } from "../../store/useCodeStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import { goBack, goForward } from "../../hooks/useCursorHistory";
import { nativeFs } from "../../lib/electronBridge";
import { clearFileHistory } from "../../lib/localHistory";

/* ------------------------------------------------------------------ */
/*  Command definitions                                                */
/* ------------------------------------------------------------------ */

interface Command {
  id: string;
  label: string;
  shortcut?: string;
  action: () => void;
}

function getCommands(): Command[] {
  return [
    {
      id: "theme.system",
      label: "Preferences: Color Theme - System",
      action: () => useSettingsStore.getState().updateSetting("theme", "system"),
    },
    {
      id: "theme.dark",
      label: "Preferences: Color Theme - Dark",
      action: () => useSettingsStore.getState().updateSetting("theme", "vs-dark"),
    },
    {
      id: "theme.light",
      label: "Preferences: Color Theme - Light",
      action: () => useSettingsStore.getState().updateSetting("theme", "vs"),
    },
    {
      id: "theme.hc",
      label: "Preferences: Color Theme - High Contrast",
      action: () => useSettingsStore.getState().updateSetting("theme", "hc-black"),
    },
    {
      id: "minimap.toggle",
      label: "View: Toggle Minimap",
      action: () => {
        const s = useSettingsStore.getState();
        s.updateSetting("minimap", !s.minimap);
      },
    },
    {
      id: "wordwrap.toggle",
      label: "View: Toggle Word Wrap",
      action: () => {
        const s = useSettingsStore.getState();
        s.updateSetting("wordWrap", s.wordWrap === "on" ? "off" : "on");
      },
    },
    {
      id: "terminal.toggle",
      label: "View: Toggle Terminal",
      shortcut: "⌃`",
      action: () => useCodeStore.getState().toggleTerminal(),
    },
    {
      id: "sidebar.toggle",
      label: "View: Toggle Sidebar",
      shortcut: "⌘B",
      action: () => useCodeStore.getState().toggleSidebar(),
    },
    {
      id: "settings.open",
      label: "Preferences: Open Settings",
      action: () => useCodeStore.getState().setShowSettings(true),
    },
    {
      id: "keybindings.open",
      label: "Preferences: Keyboard Shortcuts",
      action: () => useCodeStore.getState().setShowKeybindings(true),
    },
    {
      id: "file.quickOpen",
      label: "Go to File...",
      shortcut: "⌘P",
      action: () => useCodeStore.getState().setQuickOpenVisible(true),
    },
    {
      id: "file.reopenClosed",
      label: "File: Reopen Closed Editor",
      shortcut: "⇧⌘T",
      action: () => useCodeStore.getState().reopenClosedFile(),
    },
    {
      id: "file.saveAll",
      label: "File: Save All",
      shortcut: "⌥⌘S",
      action: async () => {
        const { openFiles, markFileSaved } = useCodeStore.getState();
        for (const f of openFiles) {
          if (f.dirty) {
            const ok = await nativeFs.writeFile(f.path, f.content);
            if (ok) markFileSaved(f.path);
          }
        }
      },
    },
    {
      id: "file.revertFile",
      label: "File: Revert File",
      action: async () => {
        const { activeFilePath, revertFile } = useCodeStore.getState();
        if (!activeFilePath) return;
        const content = await nativeFs.readFile(activeFilePath);
        if (content !== null) revertFile(activeFilePath, content);
      },
    },
    {
      id: "symbol.search",
      label: "Go to Symbol in Workspace...",
      shortcut: "⌘T",
      action: () => useCodeStore.getState().setSymbolSearchVisible(true),
    },
    {
      id: "localHistory.open",
      label: "Local History: Open Timeline",
      action: () => {
        const store = useCodeStore.getState();
        store.setActiveSidebarPanel("timeline");
      },
    },
    {
      id: "localHistory.clear",
      label: "Local History: Clear File History",
      action: () => {
        const { activeFilePath } = useCodeStore.getState();
        if (activeFilePath) clearFileHistory(activeFilePath);
      },
    },
    {
      id: "nav.goBack",
      label: "Go Back",
      shortcut: "Alt+←",
      action: () => {
        goBack().then(async (entry) => {
          if (!entry) return;
          const { openFiles, openFile, setActiveFile } = useCodeStore.getState();
          if (openFiles.find((f) => f.path === entry.filePath)) {
            setActiveFile(entry.filePath);
          } else {
            const content = await nativeFs.readFile(entry.filePath);
            if (content !== null) openFile(entry.filePath, content);
          }
          window.dispatchEvent(
            new CustomEvent("editor:goToLine", {
              detail: { lineNumber: entry.lineNumber, column: entry.column },
            }),
          );
        });
      },
    },
    {
      id: "nav.goForward",
      label: "Go Forward",
      shortcut: "Alt+→",
      action: () => {
        goForward().then(async (entry) => {
          if (!entry) return;
          const { openFiles, openFile, setActiveFile } = useCodeStore.getState();
          if (openFiles.find((f) => f.path === entry.filePath)) {
            setActiveFile(entry.filePath);
          } else {
            const content = await nativeFs.readFile(entry.filePath);
            if (content !== null) openFile(entry.filePath, content);
          }
          window.dispatchEvent(
            new CustomEvent("editor:goToLine", {
              detail: { lineNumber: entry.lineNumber, column: entry.column },
            }),
          );
        });
      },
    },
  ];
}

/* ------------------------------------------------------------------ */
/*  Fuzzy matcher                                                      */
/* ------------------------------------------------------------------ */

interface FuzzyResult {
  command: Command;
  score: number;
  matchIndices: number[];
}

function fuzzyMatch(pattern: string, text: string): { score: number; matchIndices: number[] } | null {
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
    if (idx === 0 || text[idx - 1] === " " || text[idx - 1] === ":" || text[idx - 1] === "-") score += 10;
    score += idx / text.length;
  }
  score -= text.length * 0.01;

  return { score, matchIndices: indices };
}

/* ------------------------------------------------------------------ */
/*  Highlighted text                                                   */
/* ------------------------------------------------------------------ */

function HighlightedText({ text, matchIndices }: { text: string; matchIndices: Set<number> }) {
  const spans: ReactNode[] = [];
  let run = "";
  let runHighlighted = false;

  for (let i = 0; i <= text.length; i++) {
    const isMatch = matchIndices.has(i);
    if (i === text.length || isMatch !== runHighlighted) {
      if (run) {
        spans.push(
          runHighlighted ? (
            <span key={i} className="text-blue-300 font-medium">{run}</span>
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

  return <span>{spans}</span>;
}

/* ------------------------------------------------------------------ */
/*  CommandPalette component                                           */
/* ------------------------------------------------------------------ */

export default function CommandPalette({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);

  const commands = useMemo(() => getCommands(), []);

  useEffect(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const results = useMemo((): FuzzyResult[] => {
    const raw = query.replace(/^>\s*/, "");
    if (!raw.trim()) {
      return commands.map((c) => ({ command: c, score: 0, matchIndices: [] }));
    }

    const matches: FuzzyResult[] = [];
    for (const cmd of commands) {
      const m = fuzzyMatch(raw, cmd.label);
      if (m) matches.push({ command: cmd, ...m });
    }
    matches.sort((a, b) => b.score - a.score);
    return matches;
  }, [query, commands]);

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
    (cmd: Command) => {
      onClose();
      requestAnimationFrame(() => cmd.action());
    },
    [onClose],
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
          if (results[selectedIndex]) handleSelect(results[selectedIndex].command);
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
        <div className="flex items-center gap-2 px-4 border-b border-[#3c3c3c]">
          <span className="text-gray-500 text-sm select-none">&gt;</span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a command…"
            className="w-full bg-transparent text-white text-sm px-0 py-3 outline-none placeholder:text-gray-600"
            autoComplete="off"
            spellCheck={false}
          />
        </div>

        <div ref={listRef} className="max-h-[50vh] overflow-y-auto">
          {results.length === 0 && (
            <div className="px-4 py-6 text-center text-sm text-gray-500">
              No matching commands
            </div>
          )}

          {results.map((result, i) => {
            const isSelected = i === selectedIndex;
            const matchSet = new Set(result.matchIndices);

            return (
              <div
                key={result.command.id}
                className={`px-4 py-2 flex items-center justify-between cursor-pointer transition-colors ${
                  isSelected ? "bg-[#094771]" : "hover:bg-[#2a2d2e]"
                }`}
                onClick={() => handleSelect(result.command)}
                onMouseEnter={() => setSelectedIndex(i)}
              >
                <div className="text-sm text-gray-200 truncate">
                  {matchSet.size > 0 ? (
                    <HighlightedText text={result.command.label} matchIndices={matchSet} />
                  ) : (
                    result.command.label
                  )}
                </div>
                {result.command.shortcut && (
                  <kbd className="ml-4 shrink-0 text-[11px] text-gray-500 bg-[#1e1e1e] border border-[#3c3c3c] rounded px-1.5 py-0.5">
                    {result.command.shortcut}
                  </kbd>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
