import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useAppStore, MODE_CONFIGS } from "../../store/useAppStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface GlobalCommand {
  id: string;
  label: string;
  category: string;
  shortcut?: string;
  action: () => void;
  modes?: string[];
}

/* ------------------------------------------------------------------ */
/*  Recent commands (persisted in localStorage)                        */
/* ------------------------------------------------------------------ */

const RECENT_KEY = "dan-global-palette-recent";
const MAX_RECENT = 8;

function getRecentIds(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
  } catch {
    return [];
  }
}

function pushRecent(id: string) {
  const list = getRecentIds().filter((x) => x !== id);
  list.unshift(id);
  localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, MAX_RECENT)));
}

/* ------------------------------------------------------------------ */
/*  Command registry                                                   */
/* ------------------------------------------------------------------ */

function getGlobalCommands(): GlobalCommand[] {
  const { setMode, toggleSidebar, setChatBarExpanded, setGlobalPaletteVisible } =
    useAppStore.getState();
  const { createWorkspace, workspaces, activeWorkspaceId, setActiveWorkspace } =
    useWorkspaceStore.getState();

  const nextWorkspace = () => {
    const idx = workspaces.findIndex((w) => w.id === activeWorkspaceId);
    if (idx >= 0 && workspaces.length > 1) {
      setActiveWorkspace(workspaces[(idx + 1) % workspaces.length].id);
    }
  };
  const prevWorkspace = () => {
    const idx = workspaces.findIndex((w) => w.id === activeWorkspaceId);
    if (idx >= 0 && workspaces.length > 1) {
      setActiveWorkspace(workspaces[(idx - 1 + workspaces.length) % workspaces.length].id);
    }
  };

  return [
    // Mode switching
    ...MODE_CONFIGS.filter((m) => m.enabled).map((m) => ({
      id: `mode.${m.id}`,
      label: `Switch to ${m.label}`,
      category: "Mode",
      shortcut: `⌘${m.shortcut}`,
      action: () => setMode(m.id),
    })),

    // Workspace
    {
      id: "workspace.new",
      label: "New Workspace",
      category: "Workspace",
      shortcut: "⇧⌘N",
      action: () => createWorkspace(),
    },
    {
      id: "workspace.next",
      label: "Next Workspace",
      category: "Workspace",
      shortcut: "⌥⌘→",
      action: nextWorkspace,
    },
    {
      id: "workspace.prev",
      label: "Previous Workspace",
      category: "Workspace",
      shortcut: "⌥⌘←",
      action: prevWorkspace,
    },

    // General
    {
      id: "settings.open",
      label: "Open Settings",
      category: "General",
      shortcut: "⌘,",
      action: () => window.dispatchEvent(new CustomEvent("app:openSettings")),
    },
    {
      id: "keybindings",
      label: "Keyboard Shortcuts",
      category: "General",
      action: () => window.dispatchEvent(new CustomEvent("app:openKeybindings")),
    },
    {
      id: "sidebar.toggle",
      label: "Toggle Sidebar",
      category: "General",
      shortcut: "⌘B",
      action: toggleSidebar,
    },
    {
      id: "chatbar.toggle",
      label: "Toggle Chat Bar",
      category: "General",
      shortcut: "⌘J",
      action: () => setChatBarExpanded(!useAppStore.getState().chatBarExpanded),
    },
    {
      id: "palette.close",
      label: "Close Command Palette",
      category: "General",
      shortcut: "Esc",
      action: () => setGlobalPaletteVisible(false),
    },

    // Chat-specific
    {
      id: "chat.new",
      label: "New Conversation",
      category: "Chat",
      shortcut: "⌘N",
      modes: ["chat"],
      action: () => {
        useAppStore.getState().setActiveChatThread(null, null);
      },
    },
    {
      id: "chat.search",
      label: "Search Conversations",
      category: "Chat",
      modes: ["chat"],
      action: () => {
        /* TODO: wire thread search */
      },
    },

    // Code-specific (dispatch custom events so CodeMode's own handlers pick them up)
    {
      id: "code.quickOpen",
      label: "Quick Open File",
      category: "Code",
      shortcut: "⌘P",
      modes: ["development"],
      action: () => window.dispatchEvent(new CustomEvent("code:quickOpen")),
    },
    {
      id: "code.symbolSearch",
      label: "Go to Symbol",
      category: "Code",
      shortcut: "⌘T",
      modes: ["development"],
      action: () => window.dispatchEvent(new CustomEvent("code:symbolSearch")),
    },
    {
      id: "code.toggleTerminal",
      label: "Toggle Terminal",
      category: "Code",
      shortcut: "⌃`",
      modes: ["development"],
      action: () => window.dispatchEvent(new CustomEvent("code:toggleTerminal")),
    },
    {
      id: "code.commandPalette",
      label: "Code Command Palette",
      category: "Code",
      modes: ["development"],
      action: () => window.dispatchEvent(new CustomEvent("code:commandPalette")),
    },
  ];
}

/* ------------------------------------------------------------------ */
/*  Fuzzy matcher (same algorithm as Code mode's palette)              */
/* ------------------------------------------------------------------ */

interface FuzzyResult {
  command: GlobalCommand;
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
    if (
      idx === 0 ||
      text[idx - 1] === " " ||
      text[idx - 1] === ":" ||
      text[idx - 1] === "-"
    )
      score += 10;
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
}: {
  text: string;
  matchIndices: Set<number>;
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
            <span key={i} className="text-blue-300 font-medium">
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

  return <span>{spans}</span>;
}

/* ------------------------------------------------------------------ */
/*  Category badge colors                                              */
/* ------------------------------------------------------------------ */

const CATEGORY_COLORS: Record<string, string> = {
  Mode: "bg-purple-500/20 text-purple-300",
  Workspace: "bg-amber-500/20 text-amber-300",
  General: "bg-gray-500/20 text-gray-300",
  Chat: "bg-blue-500/20 text-blue-300",
  Code: "bg-green-500/20 text-green-300",
};

/* ------------------------------------------------------------------ */
/*  GlobalCommandPalette component                                     */
/* ------------------------------------------------------------------ */

export default function GlobalCommandPalette() {
  const activeMode = useAppStore((s) => s.activeMode);
  const setGlobalPaletteVisible = useAppStore((s) => s.setGlobalPaletteVisible);

  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);

  const commands = useMemo(() => {
    const all = getGlobalCommands();
    return all.filter((c) => !c.modes || c.modes.includes(activeMode));
  }, [activeMode]);

  useEffect(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const recentIds = useMemo(() => getRecentIds(), []);

  const results = useMemo((): FuzzyResult[] => {
    const raw = query.replace(/^>\s*/, "").trim();

    if (!raw) {
      const recentSet = new Set(recentIds);
      const recent: FuzzyResult[] = [];
      const rest: FuzzyResult[] = [];

      for (const c of commands) {
        const item: FuzzyResult = { command: c, score: 0, matchIndices: [] };
        if (recentSet.has(c.id)) {
          item.score = MAX_RECENT - recentIds.indexOf(c.id);
          recent.push(item);
        } else {
          rest.push(item);
        }
      }
      recent.sort((a, b) => b.score - a.score);
      return [...recent, ...rest];
    }

    const matches: FuzzyResult[] = [];
    for (const cmd of commands) {
      const m = fuzzyMatch(raw, cmd.label) ?? fuzzyMatch(raw, cmd.category + " " + cmd.label);
      if (m) matches.push({ command: cmd, ...m });
    }
    matches.sort((a, b) => b.score - a.score);
    return matches;
  }, [query, commands, recentIds]);

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
    (cmd: GlobalCommand) => {
      pushRecent(cmd.id);
      setGlobalPaletteVisible(false);
      requestAnimationFrame(() => cmd.action());
    },
    [setGlobalPaletteVisible],
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
          setGlobalPaletteVisible(false);
          break;
      }
    },
    [results, selectedIndex, handleSelect, setGlobalPaletteVisible],
  );

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === backdropRef.current) setGlobalPaletteVisible(false);
    },
    [setGlobalPaletteVisible],
  );

  return (
    <div
      ref={backdropRef}
      className="fixed inset-0 z-[200] bg-black/50"
      onClick={handleBackdropClick}
    >
      <div className="max-w-2xl mx-auto mt-[10vh] rounded-lg shadow-2xl bg-[#1e1e2e] border border-[#313244] overflow-hidden">
        {/* Search input */}
        <div className="flex items-center gap-2 px-4 border-b border-[#313244]">
          <span className="text-gray-500 text-sm select-none">⌘⇧P</span>
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

        {/* Results */}
        <div ref={listRef} className="max-h-[50vh] overflow-y-auto">
          {results.length === 0 && (
            <div className="px-4 py-6 text-center text-sm text-gray-500">
              No matching commands
            </div>
          )}

          {results.map((result, i) => {
            const isSelected = i === selectedIndex;
            const matchSet = new Set(result.matchIndices);
            const catColor =
              CATEGORY_COLORS[result.command.category] ?? CATEGORY_COLORS.General;

            return (
              <div
                key={result.command.id}
                className={`px-4 py-2 flex items-center gap-3 cursor-pointer transition-colors ${
                  isSelected ? "bg-[#45475a]" : "hover:bg-[#313244]"
                }`}
                onClick={() => handleSelect(result.command)}
                onMouseEnter={() => setSelectedIndex(i)}
              >
                <span
                  className={`shrink-0 text-[10px] font-medium px-1.5 py-0.5 rounded ${catColor}`}
                >
                  {result.command.category}
                </span>

                <div className="flex-1 text-sm text-gray-200 truncate">
                  {matchSet.size > 0 ? (
                    <HighlightedText
                      text={result.command.label}
                      matchIndices={matchSet}
                    />
                  ) : (
                    result.command.label
                  )}
                </div>

                {result.command.shortcut && (
                  <kbd className="shrink-0 text-[11px] text-gray-500 bg-[#1e1e1e] border border-[#3c3c3c] rounded px-1.5 py-0.5">
                    {result.command.shortcut}
                  </kbd>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer hint */}
        <div className="border-t border-[#313244] px-4 py-1.5 flex items-center gap-4 text-[10px] text-gray-600">
          <span>
            <kbd className="px-1 border border-gray-700 rounded">↑↓</kbd> navigate
          </span>
          <span>
            <kbd className="px-1 border border-gray-700 rounded">↵</kbd> select
          </span>
          <span>
            <kbd className="px-1 border border-gray-700 rounded">esc</kbd> close
          </span>
        </div>
      </div>
    </div>
  );
}
