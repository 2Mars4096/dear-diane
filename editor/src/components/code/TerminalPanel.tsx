import { useEffect, useRef, useCallback, useState, useMemo } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { Plus, X, Columns2, Copy, ClipboardPaste, Eraser, ScreenShare, CheckSquare, ChevronDown } from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { useSettingsStore, type TerminalProfile } from "../../store/useSettingsStore";
import { nativeTerminal } from "../../lib/electronBridge";
import { resolveMonacoTheme } from "../../lib/appearanceTheme";

interface TermContextMenu {
  x: number;
  y: number;
  termId: string;
  hasSelection: boolean;
}

const TERM_OPTIONS = {
  fontFamily: "SF Mono, Menlo, Monaco, Courier New, monospace",
  fontSize: 13,
  lineHeight: 1.2,
  scrollback: 5000,
  cursorBlink: true,
  cursorStyle: "block" as const,
  macOptionIsMeta: true,
  macOptionClickForcesSelection: true,
  allowTransparency: true,
} as const;

interface XtermInstance {
  terminal: Terminal;
  fitAddon: FitAddon;
  container: HTMLDivElement;
}

export default function TerminalPanel() {
  const {
    terminals,
    activeTerminalId,
    addTerminal,
    removeTerminal,
    setActiveTerminal,
  } = useCodeStore();

  const xtermMapRef = useRef<Map<string, XtermInstance>>(new Map());
  const wrapperRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef(false);

  const setActiveTerminalRef = useRef(setActiveTerminal);
  setActiveTerminalRef.current = setActiveTerminal;

  const [splitTerminalIds, setSplitTerminalIds] = useState<[string, string] | null>(null);
  const [termCtxMenu, setTermCtxMenu] = useState<TermContextMenu | null>(null);
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const appTheme = useSettingsStore((s) => s.theme);
  const terminalProfiles = useSettingsStore((s) => s.terminalProfiles);
  const resolvedMonacoTheme = resolveMonacoTheme(appTheme);
  const isTerminalDark = resolvedMonacoTheme !== "vs";
  const splitBorderColor = isTerminalDark ? "#3c3c3c" : "#d1d5db";
  const terminalTheme = useMemo(
    () =>
      isTerminalDark
        ? {
            background: "#1e1e1e",
            foreground: "#d4d4d4",
            cursor: "#d4d4d4",
            selectionBackground: "#264f78",
          }
        : {
            background: "#ffffff",
            foreground: "#1f2937",
            cursor: "#111827",
            selectionBackground: "#bfdbfe",
          },
    [isTerminalDark],
  );

  const activeSplitSide = splitTerminalIds
    ? activeTerminalId === splitTerminalIds[0] ? "left" : "right"
    : null;

  const attachTerminalInstance = useCallback((termId: string) => {
    if (xtermMapRef.current.has(termId)) return;

    const container = document.createElement("div");
    container.style.width = "100%";
    container.style.height = "100%";
    container.style.display = "none";

    container.addEventListener("mousedown", () => {
      setActiveTerminalRef.current(termId);
    });

    const terminal = new Terminal({
      ...TERM_OPTIONS,
      theme: terminalTheme,
    });
    const fitAddon = new FitAddon();
    const webLinksAddon = new WebLinksAddon();

    terminal.loadAddon(fitAddon);
    terminal.loadAddon(webLinksAddon);

    terminal.attachCustomKeyEventHandler((e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "c" && terminal.hasSelection()) {
        navigator.clipboard.writeText(terminal.getSelection());
        terminal.clearSelection();
        return false;
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "v") {
        navigator.clipboard.readText().then((text) => {
          if (text) nativeTerminal.write(termId, text);
        });
        return false;
      }
      return true;
    });

    xtermMapRef.current.set(termId, { terminal, fitAddon, container });

    if (wrapperRef.current) {
      wrapperRef.current.appendChild(container);
      terminal.open(container);
      requestAnimationFrame(() => {
        fitAddon.fit();
        const dims = fitAddon.proposeDimensions();
        if (dims) nativeTerminal.resize(termId, dims.cols, dims.rows);
      });
    }

    terminal.onData((data) => {
      nativeTerminal.write(termId, data);
    });
  }, [terminalTheme]);

  const createTerminalInstance = useCallback(async (profile?: TerminalProfile): Promise<string | null> => {
    const pinnedRoots = useCodeStore.getState().pinnedRoots;
    const settings = useSettingsStore.getState();

    const resolvedProfile = profile
      ?? settings.terminalProfiles.find((p) => p.id === settings.defaultTerminalProfile)
      ?? settings.terminalProfiles.find((p) => p.isDefault)
      ?? settings.terminalProfiles[0];

    const cwd = resolvedProfile?.cwd || pinnedRoots[0] || undefined;
    const shell = resolvedProfile?.shell || "/bin/zsh";

    const termId = await nativeTerminal.create({ shell, cwd });
    if (!termId) return null;

    addTerminal(termId);
    attachTerminalInstance(termId);

    return termId;
  }, [addTerminal, attachTerminalInstance]);

  useEffect(() => {
    if (mountedRef.current) return;
    mountedRef.current = true;

    if (terminals.length === 0) {
      void createTerminalInstance();
    } else {
      terminals.forEach((term) => attachTerminalInstance(term.id));
      if (!activeTerminalId && terminals[0]) {
        setActiveTerminal(terminals[0].id);
      }
    }
  }, [
    terminals,
    activeTerminalId,
    createTerminalInstance,
    attachTerminalInstance,
    setActiveTerminal,
  ]);

  useEffect(() => {
    terminals.forEach((term) => attachTerminalInstance(term.id));

    for (const [id, inst] of xtermMapRef.current) {
      if (!terminals.some((term) => term.id === id)) {
        inst.terminal.dispose();
        inst.container.remove();
        xtermMapRef.current.delete(id);
      }
    }
  }, [terminals, attachTerminalInstance]);

  useEffect(() => {
    const removeDataListener = nativeTerminal.onData((id, data) => {
      xtermMapRef.current.get(id)?.terminal.write(data);
    });

    const removeExitListener = nativeTerminal.onExit((id, code) => {
      const inst = xtermMapRef.current.get(id);
      if (inst) {
        inst.terminal.write(
          `\r\n\x1b[90m[Process exited with code ${code ?? "unknown"}]\x1b[0m\r\n`,
        );
      }
    });

    return () => {
      removeDataListener();
      removeExitListener();
    };
  }, []);

  useEffect(() => {
    for (const [, inst] of xtermMapRef.current) {
      inst.terminal.options.theme = terminalTheme;
      inst.terminal.refresh(0, Math.max(0, inst.terminal.rows - 1));
    }
  }, [terminalTheme]);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;

    if (splitTerminalIds) {
      wrapper.style.display = "flex";

      for (const [id, inst] of xtermMapRef.current) {
        const isLeft = id === splitTerminalIds[0];
        const isRight = id === splitTerminalIds[1];
        if (isLeft || isRight) {
          inst.container.style.display = "block";
          inst.container.style.flex = "1";
          inst.container.style.width = "";
          inst.container.style.minWidth = "0";
          inst.container.style.borderRight = isLeft ? `1px solid ${splitBorderColor}` : "";
          requestAnimationFrame(() => {
            inst.fitAddon.fit();
            const dims = inst.fitAddon.proposeDimensions();
            if (dims) nativeTerminal.resize(id, dims.cols, dims.rows);
          });
        } else {
          inst.container.style.display = "none";
          inst.container.style.flex = "";
          inst.container.style.width = "100%";
          inst.container.style.minWidth = "";
          inst.container.style.borderRight = "";
        }
      }
    } else {
      wrapper.style.display = "";

      for (const [id, inst] of xtermMapRef.current) {
        const isActive = id === activeTerminalId;
        inst.container.style.display = isActive ? "block" : "none";
        inst.container.style.flex = "";
        inst.container.style.width = "100%";
        inst.container.style.minWidth = "";
        inst.container.style.borderRight = "";
        if (isActive) {
          requestAnimationFrame(() => {
            inst.fitAddon.fit();
            inst.terminal.focus();
            const dims = inst.fitAddon.proposeDimensions();
            if (dims) nativeTerminal.resize(id, dims.cols, dims.rows);
          });
        }
      }
    }
  }, [activeTerminalId, splitTerminalIds, splitBorderColor]);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;

    const observer = new ResizeObserver(() => {
      if (splitTerminalIds) {
        for (const id of splitTerminalIds) {
          const inst = xtermMapRef.current.get(id);
          if (inst) requestAnimationFrame(() => {
            inst.fitAddon.fit();
            const dims = inst.fitAddon.proposeDimensions();
            if (dims) nativeTerminal.resize(id, dims.cols, dims.rows);
          });
        }
      } else if (activeTerminalId) {
        const inst = xtermMapRef.current.get(activeTerminalId);
        if (inst) requestAnimationFrame(() => {
          inst.fitAddon.fit();
          const dims = inst.fitAddon.proposeDimensions();
          if (dims && activeTerminalId) nativeTerminal.resize(activeTerminalId, dims.cols, dims.rows);
        });
      }
    });

    observer.observe(wrapper);
    return () => observer.disconnect();
  }, [activeTerminalId, splitTerminalIds]);

  useEffect(() => {
    const xtermMap = xtermMapRef.current;
    return () => {
      for (const [, inst] of xtermMap) {
        inst.terminal.dispose();
      }
      xtermMap.clear();
    };
  }, []);

  const handleSplit = useCallback(async () => {
    if (splitTerminalIds) {
      setSplitTerminalIds(null);
      return;
    }

    const currentActiveId = activeTerminalId;
    if (!currentActiveId) return;

    if (terminals.length >= 2) {
      const otherId = terminals.find(t => t.id !== currentActiveId)?.id;
      if (otherId) {
        setSplitTerminalIds([currentActiveId, otherId]);
      }
    } else {
      const newId = await createTerminalInstance();
      if (newId) setSplitTerminalIds([currentActiveId, newId]);
    }
  }, [splitTerminalIds, terminals, activeTerminalId, createTerminalInstance]);

  const handleClose = useCallback(
    (e: React.MouseEvent, id: string) => {
      e.stopPropagation();

      if (splitTerminalIds && (splitTerminalIds[0] === id || splitTerminalIds[1] === id)) {
        setSplitTerminalIds(null);
      }

      const inst = xtermMapRef.current.get(id);
      if (inst) {
        inst.terminal.dispose();
        inst.container.remove();
        xtermMapRef.current.delete(id);
      }
      nativeTerminal.kill(id);
      removeTerminal(id);
    },
    [removeTerminal, splitTerminalIds],
  );

  const handleTermContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const id = activeTerminalId;
      if (!id) return;
      const inst = xtermMapRef.current.get(id);
      setTermCtxMenu({
        x: e.clientX,
        y: e.clientY,
        termId: id,
        hasSelection: inst?.terminal.hasSelection() ?? false,
      });
    },
    [activeTerminalId],
  );

  const handleTermCopy = useCallback(() => {
    if (!termCtxMenu) return;
    const inst = xtermMapRef.current.get(termCtxMenu.termId);
    if (inst?.terminal.hasSelection()) {
      navigator.clipboard.writeText(inst.terminal.getSelection());
      inst.terminal.clearSelection();
    }
    setTermCtxMenu(null);
  }, [termCtxMenu]);

  const handleTermPaste = useCallback(() => {
    if (!termCtxMenu) return;
    navigator.clipboard.readText().then((text) => {
      if (text) nativeTerminal.write(termCtxMenu.termId, text);
    });
    setTermCtxMenu(null);
  }, [termCtxMenu]);

  const handleTermSelectAll = useCallback(() => {
    if (!termCtxMenu) return;
    const inst = xtermMapRef.current.get(termCtxMenu.termId);
    inst?.terminal.selectAll();
    setTermCtxMenu(null);
  }, [termCtxMenu]);

  const handleTermClear = useCallback(() => {
    if (!termCtxMenu) return;
    const inst = xtermMapRef.current.get(termCtxMenu.termId);
    inst?.terminal.clear();
    setTermCtxMenu(null);
  }, [termCtxMenu]);

  useEffect(() => {
    if (!termCtxMenu) return;
    const dismiss = () => setTermCtxMenu(null);
    window.addEventListener("click", dismiss);
    window.addEventListener("contextmenu", dismiss);
    return () => {
      window.removeEventListener("click", dismiss);
      window.removeEventListener("contextmenu", dismiss);
    };
  }, [termCtxMenu]);

  useEffect(() => {
    const handler = async (e: Event) => {
      const { command, cwd } = (e as CustomEvent<{ command: string; cwd?: string }>).detail;
      const { pinnedRoots, setShowTerminal } = useCodeStore.getState();
      setShowTerminal(true);

      const termCwd = cwd || pinnedRoots[0] || undefined;
      const id = await nativeTerminal.create({ shell: "/bin/zsh", cwd: termCwd });
      if (!id) return;

      const shortCmd = command.length > 40 ? command.slice(0, 37) + "..." : command;
      addTerminal(id, `\u26A1 ${shortCmd}`);
      setActiveTerminal(id);

      const container = document.createElement("div");
      container.style.width = "100%";
      container.style.height = "100%";
      container.style.display = "none";

      container.addEventListener("mousedown", () => {
        setActiveTerminalRef.current(id);
      });

      const terminal = new Terminal({
        ...TERM_OPTIONS,
        theme: terminalTheme,
      });
      const fitAddon = new FitAddon();
      const webLinksAddon = new WebLinksAddon();
      terminal.loadAddon(fitAddon);
      terminal.loadAddon(webLinksAddon);

      terminal.attachCustomKeyEventHandler((ev) => {
        if ((ev.metaKey || ev.ctrlKey) && ev.key === "c" && terminal.hasSelection()) {
          navigator.clipboard.writeText(terminal.getSelection());
          terminal.clearSelection();
          return false;
        }
        if ((ev.metaKey || ev.ctrlKey) && ev.key === "v") {
          navigator.clipboard.readText().then((text) => {
            if (text) nativeTerminal.write(id, text);
          });
          return false;
        }
        return true;
      });

      xtermMapRef.current.set(id, { terminal, fitAddon, container });

      if (wrapperRef.current) {
        wrapperRef.current.appendChild(container);
        terminal.open(container);
        requestAnimationFrame(() => {
          fitAddon.fit();
          const dims = fitAddon.proposeDimensions();
          if (dims) nativeTerminal.resize(id, dims.cols, dims.rows);
        });
      }

      terminal.onData((data) => {
        nativeTerminal.write(id, data);
      });

      setTimeout(() => nativeTerminal.write(id, command + "\n"), 300);
    };

    window.addEventListener("chat:shellCommand", handler);
    return () => window.removeEventListener("chat:shellCommand", handler);
  }, [addTerminal, setActiveTerminal, terminalTheme]);

  useEffect(() => {
    if (!showProfileMenu) return;
    const dismiss = () => setShowProfileMenu(false);
    const timer = setTimeout(() => window.addEventListener("click", dismiss), 0);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("click", dismiss);
    };
  }, [showProfileMenu]);

  const handleTabClick = useCallback(
    (id: string) => {
      if (splitTerminalIds) {
        const isInSplit = id === splitTerminalIds[0] || id === splitTerminalIds[1];
        if (isInSplit) {
          setActiveTerminal(id);
        } else {
          const newSplit: [string, string] = activeSplitSide === "left"
            ? [id, splitTerminalIds[1]]
            : [splitTerminalIds[0], id];
          setSplitTerminalIds(newSplit);
          setActiveTerminal(id);
        }
      } else {
        setActiveTerminal(id);
      }
    },
    [splitTerminalIds, activeSplitSide, setActiveTerminal],
  );

  return (
    <div className="flex h-full w-full flex-col bg-white dark:bg-[#1e1e1e]">
      {/* Tab bar */}
      <div className="flex items-center gap-0 bg-gray-100 dark:bg-[#252526] px-1 border-b border-gray-300 dark:border-[#3c3c3c]">
        {terminals.map((t) => {
          const isInSplit = splitTerminalIds != null &&
            (splitTerminalIds[0] === t.id || splitTerminalIds[1] === t.id);
          const isActive = splitTerminalIds
            ? (activeSplitSide === "left" ? splitTerminalIds[0] : splitTerminalIds[1]) === t.id
            : t.id === activeTerminalId;

          return (
            <button
              key={t.id}
              onClick={() => handleTabClick(t.id)}
              className={`group flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border-r border-gray-300 dark:border-[#3c3c3c] transition-colors ${
                isActive
                  ? "bg-white text-gray-900 dark:bg-[#1e1e1e] dark:text-white"
                  : isInSplit
                    ? "bg-gray-100 text-gray-700 dark:bg-[#2a2a2a] dark:text-gray-300"
                    : "bg-gray-50 text-gray-500 hover:text-gray-800 dark:bg-[#2d2d2d] dark:text-gray-400 dark:hover:text-gray-200"
              }`}
            >
              <span className="truncate max-w-[120px]">{t.title}</span>
              {isInSplit && (
                <span className="text-[9px] text-blue-400 ml-0.5">●</span>
              )}
              <span
                onClick={(e) => handleClose(e, t.id)}
                className="ml-1 rounded p-0.5 opacity-0 group-hover:opacity-100 hover:bg-gray-200 dark:hover:bg-white/10 transition-opacity"
              >
                <X size={12} />
              </span>
            </button>
          );
        })}

        <button
          onClick={handleSplit}
          className={`flex items-center justify-center p-1.5 transition-colors ${
            splitTerminalIds ? "text-blue-500 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300" : "text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-white"
          }`}
          title={splitTerminalIds ? "Unsplit Terminal" : "Split Terminal"}
        >
          <Columns2 size={14} />
        </button>

        <div className="relative flex items-center">
          <button
            onClick={() => createTerminalInstance()}
            className="flex items-center justify-center p-1.5 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-white transition-colors"
            title="New Terminal"
          >
            <Plus size={14} />
          </button>
          <button
            onClick={() => setShowProfileMenu((v) => !v)}
            className="flex items-center justify-center p-1 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-white transition-colors -ml-0.5"
            title="Select Terminal Profile"
          >
            <ChevronDown size={10} />
          </button>

          {showProfileMenu && (
            <div className="absolute top-full right-0 mt-1 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded shadow-lg py-1 z-50 min-w-[160px]">
              {terminalProfiles.map((profile) => (
                <button
                  key={profile.id}
                  onClick={() => {
                    createTerminalInstance(profile);
                    setShowProfileMenu(false);
                  }}
                  className="block w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                >
                  {profile.name}
                  {profile.isDefault && (
                    <span className="text-gray-500 dark:text-gray-500 ml-1">(default)</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Terminal display area */}
      <div
        ref={wrapperRef}
        className="flex-1 min-h-0 overflow-hidden"
        onContextMenu={handleTermContextMenu}
      />

      {termCtxMenu && (
        <div
          className="fixed z-50 min-w-[180px] rounded-md bg-white dark:bg-[#252526] border border-gray-300 dark:border-[#3c3c3c] shadow-xl py-1 text-sm text-gray-700 dark:text-gray-300"
          style={{ left: termCtxMenu.x, top: termCtxMenu.y }}
        >
          {termCtxMenu.hasSelection && (
            <button
              onClick={handleTermCopy}
              className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-[#094771] transition-colors text-left"
            >
              <Copy size={14} /> Copy
              <span className="ml-auto text-xs text-gray-500 dark:text-gray-500">⌘C</span>
            </button>
          )}
          <button
            onClick={handleTermPaste}
            className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-[#094771] transition-colors text-left"
          >
            <ClipboardPaste size={14} /> Paste
            <span className="ml-auto text-xs text-gray-500 dark:text-gray-500">⌘V</span>
          </button>
          <button
            onClick={handleTermSelectAll}
            className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-[#094771] transition-colors text-left"
          >
            <CheckSquare size={14} /> Select All
          </button>
          <div className="border-t border-gray-300 dark:border-[#3c3c3c] my-1" />
          <button
            onClick={handleTermClear}
            className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-[#094771] transition-colors text-left"
          >
            <Eraser size={14} /> Clear Terminal
          </button>
          <button
            onClick={() => { setTermCtxMenu(null); handleSplit(); }}
            className="flex items-center gap-2 w-full px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-[#094771] transition-colors text-left"
          >
            <ScreenShare size={14} /> Split Terminal
          </button>
        </div>
      )}
    </div>
  );
}
