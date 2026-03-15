/**
 * Code mode: VS Code-like IDE workspace with resizable panels.
 * Layout: ActivityBar | Sidebar | (EditorTabs / TerminalPanel) | StatusBar
 */
import { useState, useEffect, useCallback } from "react";
import { Allotment } from "allotment";
import {
  Files,
  Search,
  GitBranch,
  Blocks,
  MessageSquare,
  Terminal as TerminalIcon,
  AlertTriangle,
  FileText,
  Settings,
  Clock,
  ListTodo,
  FlaskConical,
  ListTree,
  Bug,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { useAppStore } from "../../store/useAppStore";
import FileExplorer from "../code/FileExplorer";
import SearchPanel from "../code/SearchPanel";
import GitPanel from "../code/GitPanel";
import MonacoTabs from "../code/MonacoTabs";
import DiffView from "../code/DiffView";
import SettingsPanel from "../code/SettingsPanel";
import KeybindingsPanel from "../code/KeybindingsPanel";
import TerminalPanel from "../code/TerminalPanel";
import ProblemsPanel, { useProblemsCount } from "../code/ProblemsPanel";
import OutputPanel from "../code/OutputPanel";
import ChatSidebar from "../code/ChatSidebar";
import QuickOpen from "../code/QuickOpen";
import CommandPalette from "../code/CommandPalette";
import SymbolSearch from "../code/SymbolSearch";
import LocalHistoryPanel from "../code/LocalHistoryPanel";
import CrashRecoveryBanner from "../code/CrashRecoveryBanner";
import WriteConfirmDialog from "../code/WriteConfirmDialog";
import CodebaseQA from "../code/CodebaseQA";
import TaskRunner from "../code/TaskRunner";
import TestExplorer from "../code/TestExplorer";
import OutlineView from "../code/OutlineView";
import SplitEditor from "../code/SplitEditor";
import ZenMode from "../code/ZenMode";
import WorkspaceInfo from "../code/WorkspaceInfo";
import DebugPanel, { DebugConsole, useDebugEvents } from "../code/DebugPanel";
import InteractiveRebase from "../code/InteractiveRebase";
import MergeEditor from "../code/MergeEditor";
import ExtensionsPanel from "../code/ExtensionsPanel";
import CallHierarchy from "../code/CallHierarchy";
import {
  CoverageSummaryBar,
} from "../code/CoverageOverlay";
import { useSessionRestore } from "../../hooks/useSessionRestore";
import { useFileWatcher } from "../../hooks/useFileWatcher";
import { useMonacoLsp } from "../../hooks/useMonacoLsp";
import { useLspDocSync } from "../../hooks/useLspDocSync";
import { useWorkspaceMemory } from "../../hooks/useWorkspaceMemory";
import { useFileChangeDetection } from "../../hooks/useFileChangeDetection";
import { useSettingsStore } from "../../store/useSettingsStore";
import { useDebugStore } from "../../store/useDebugStore";
import { nativeGit, nativeFs, nativeDebug } from "../../lib/electronBridge";
import { goBack, goForward } from "../../hooks/useCursorHistory";
import { activateAllInstalledExtensions } from "../../lib/extensions/extensionActivator";

/* ------------------------------------------------------------------ */
/*  Activity bar                                                      */
/* ------------------------------------------------------------------ */

interface ActivityItemProps {
  icon: React.ReactNode;
  active?: boolean;
  title: string;
  onClick?: () => void;
}

function ActivityItem({ icon, active, title, onClick }: ActivityItemProps) {
  return (
    <button
      title={title}
      onClick={onClick}
      className={`w-full flex items-center justify-center py-2.5 transition-colors ${
        active
          ? "text-white border-l-2 border-white"
          : "text-gray-500 hover:text-gray-300 border-l-2 border-transparent"
      }`}
    >
      {icon}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Status bar                                                        */
/* ------------------------------------------------------------------ */

function StatusBar() {
  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const openFiles = useCodeStore((s) => s.openFiles);
  const cursorPosition = useCodeStore((s) => s.cursorPosition);
  const currentBranch = useCodeStore((s) => s.currentBranch);
  const activeFile = openFiles.find((f) => f.path === activeFilePath);
  const { errors, warnings } = useProblemsCount();
  const tabSize = useSettingsStore((s) => s.tabSize);

  return (
    <div className="h-[22px] bg-[#007acc] text-white flex items-center justify-between px-2 text-[11px] shrink-0 select-none">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1">
          <GitBranch size={12} />
          {currentBranch || "no branch"}
        </span>
        {(errors > 0 || warnings > 0) && (
          <span className="flex items-center gap-1.5">
            {errors > 0 && (
              <span className="flex items-center gap-0.5">&#x2297; {errors}</span>
            )}
            {warnings > 0 && (
              <span className="flex items-center gap-0.5">&#x26A0; {warnings}</span>
            )}
          </span>
        )}
      </div>
      <div className="flex items-center gap-3">
        {activeFile && (
          <>
            <button className="hover:bg-white/10 px-1 rounded">
              Ln {cursorPosition.lineNumber}, Col {cursorPosition.column}
            </button>
            <span>Spaces: {tabSize}</span>
            <span>UTF-8</span>
            <button className="hover:bg-white/10 px-1 rounded capitalize">
              {activeFile.language}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Keyboard shortcuts                                                */
/* ------------------------------------------------------------------ */

let zenPending = false;
let zenTimeout: ReturnType<typeof setTimeout> | undefined;

function useCodeShortcuts() {
  const activeMode = useAppStore((s) => s.activeMode);
  const toggleSidebar = useCodeStore((s) => s.toggleSidebar);
  const toggleTerminal = useCodeStore((s) => s.toggleTerminal);
  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const markFileSaved = useCodeStore((s) => s.markFileSaved);
  const closeFile = useCodeStore((s) => s.closeFile);
  const reopenClosedFile = useCodeStore((s) => s.reopenClosedFile);

  const setActiveSidebarPanel = useCodeStore((s) => s.setActiveSidebarPanel);
  const setQuickOpenVisible = useCodeStore((s) => s.setQuickOpenVisible);
  const setCommandPaletteVisible = useCodeStore((s) => s.setCommandPaletteVisible);
  const toggleSettings = useCodeStore((s) => s.toggleSettings);

  const handleSaveAll = useCallback(async () => {
    const state = useCodeStore.getState();
    for (const f of state.openFiles) {
      if (f.dirty) {
        const isUnderRoot = state.pinnedRoots.some(
          (root) => f.path.startsWith(root + "/") || f.path === root,
        );
        if (!isUnderRoot && !state.allowedExternalPaths.has(f.path)) {
          state.setPendingWriteConfirmation({ filePath: f.path, content: f.content });
          return;
        }
        const ok = await nativeFs.writeFile(f.path, f.content);
        if (ok) state.markFileSaved(f.path);
      }
    }
  }, []);

  const navigateCursorHistory = useCallback(
    async (direction: "back" | "forward") => {
      const entry = await (direction === "back" ? goBack() : goForward());
      if (!entry) return;
      const { openFiles, openFile, setActiveFile } = useCodeStore.getState();
      const isOpen = openFiles.find((f) => f.path === entry.filePath);
      if (isOpen) {
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
    },
    [],
  );

  const handler = useCallback(
    (e: KeyboardEvent) => {
      if (activeMode !== "development") return;
      const meta = e.metaKey || e.ctrlKey;

      if (!meta && zenPending && e.key === "z") {
        e.preventDefault();
        zenPending = false;
        clearTimeout(zenTimeout);
        const state = useCodeStore.getState();
        if (state.zenModeFilePath) {
          state.setZenModeFilePath(null);
        } else if (state.activeFilePath) {
          state.setZenModeFilePath(state.activeFilePath);
        }
        return;
      }

      if (meta && e.key === "k") {
        zenPending = true;
        clearTimeout(zenTimeout);
        zenTimeout = setTimeout(() => { zenPending = false; }, 1500);
      } else if (!meta || e.key !== "k") {
        if (zenPending && e.key !== "z") zenPending = false;
      }

      if (e.altKey && !meta && e.key === "ArrowLeft") {
        e.preventDefault();
        navigateCursorHistory("back");
        return;
      }
      if (e.altKey && !meta && e.key === "ArrowRight") {
        e.preventDefault();
        navigateCursorHistory("forward");
        return;
      }

      // Shift+Alt+H: call hierarchy
      if (e.shiftKey && e.altKey && (e.key === "h" || e.key === "H") && !meta) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("codemode:showCallHierarchy"));
        return;
      }

      // Debug shortcuts (function keys, no meta required)
      if (e.key === "F5" && !meta) {
        e.preventDefault();
        const ds = useDebugStore.getState();
        if (e.shiftKey) {
          nativeDebug.stop();
        } else if (ds.status === "paused") {
          nativeDebug.continue_(ds.activeThreadId ?? 1);
        } else if (ds.status === "idle" || ds.status === "stopped") {
          const config = ds.launchConfigs[ds.activeLaunchConfigIndex];
          if (config) {
            ds.setStatus("running");
            nativeDebug.start(config).then((r) => {
              if (!r.success) {
                ds.setStatus("idle");
                ds.appendConsoleOutput(`Error: ${r.error}\n`);
              }
            });
          }
        }
        return;
      }
      if (e.key === "F10" && !meta && !e.shiftKey) {
        e.preventDefault();
        const ds = useDebugStore.getState();
        if (ds.status === "paused") nativeDebug.next(ds.activeThreadId ?? 1);
        return;
      }
      if (e.key === "F11" && !meta) {
        e.preventDefault();
        const ds = useDebugStore.getState();
        if (ds.status === "paused") {
          if (e.shiftKey) {
            nativeDebug.stepOut(ds.activeThreadId ?? 1);
          } else {
            nativeDebug.stepIn(ds.activeThreadId ?? 1);
          }
        }
        return;
      }

      if (!meta) return;

      if (e.key === "b" && e.shiftKey) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("taskRunner:runBuild"));
      } else if (e.key === "b") {
        e.preventDefault();
        toggleSidebar();
      } else if (e.key === "`") {
        e.preventDefault();
        toggleTerminal();
      } else if (e.key === "s" && e.altKey) {
        e.preventDefault();
        handleSaveAll();
      } else if (e.key === "s") {
        e.preventDefault();
        if (activeFilePath) {
          const state = useCodeStore.getState();
          const file = state.openFiles.find((f) => f.path === activeFilePath);
          if (file?.dirty) {
            const isUnderRoot = state.pinnedRoots.some(
              (root) => activeFilePath.startsWith(root + "/") || activeFilePath === root,
            );
            if (!isUnderRoot && !state.allowedExternalPaths.has(activeFilePath)) {
              state.setPendingWriteConfirmation({ filePath: activeFilePath, content: file.content });
              return;
            }
          }
          markFileSaved(activeFilePath);
        }
      } else if (e.key === "w") {
        e.preventDefault();
        if (activeFilePath) closeFile(activeFilePath);
      } else if (e.key === "t" && e.shiftKey) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("taskRunner:runTest"));
      } else if (e.key === "t" && !e.shiftKey) {
        e.preventDefault();
        useCodeStore.getState().setSymbolSearchVisible(true);
      } else if (e.key === "p" && e.shiftKey) {
        e.preventDefault();
        setCommandPaletteVisible(true);
      } else if (e.key === "p") {
        e.preventDefault();
        setQuickOpenVisible(true);
      } else if (e.key === "f" && e.shiftKey) {
        e.preventDefault();
        setActiveSidebarPanel("search");
      } else if (e.key === "x" && e.shiftKey) {
        e.preventDefault();
        setActiveSidebarPanel("extensions");
      } else if (e.key === ",") {
        e.preventDefault();
        toggleSettings();
      } else if (e.key === "i" && e.shiftKey) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("codemode:toggleQA"));
      } else if (e.key === "\\") {
        e.preventDefault();
        const state = useCodeStore.getState();
        if (state.splitFilePath) {
          state.setSplitFilePath(null);
        } else if (state.activeFilePath) {
          state.setSplitFilePath(state.activeFilePath);
        }
      }
    },
    [activeMode, toggleSidebar, toggleTerminal, activeFilePath, markFileSaved, closeFile, reopenClosedFile, handleSaveAll, setActiveSidebarPanel, setQuickOpenVisible, setCommandPaletteVisible, toggleSettings, navigateCursorHistory],
  );

  useEffect(() => {
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handler]);
}

/* ------------------------------------------------------------------ */
/*  Bottom panel tabs (Terminal / Problems)                            */
/* ------------------------------------------------------------------ */

type BottomTab = "terminal" | "problems" | "output" | "debugConsole";

function BottomPanelTabs({
  activeTab,
  onTabChange,
}: {
  activeTab: BottomTab;
  onTabChange: (tab: BottomTab) => void;
}) {
  const tabs: { id: BottomTab; label: string; icon: React.ReactNode }[] = [
    { id: "terminal", label: "Terminal", icon: <TerminalIcon size={13} /> },
    { id: "problems", label: "Problems", icon: <AlertTriangle size={13} /> },
    { id: "output", label: "Output", icon: <FileText size={13} /> },
    { id: "debugConsole", label: "Debug Console", icon: <Bug size={13} /> },
  ];

  return (
    <div className="flex items-center bg-[#252526] border-b border-[#3c3c3c] shrink-0">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onTabChange(t.id)}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide border-b transition-colors ${
            activeTab === t.id
              ? "text-white border-white"
              : "text-gray-500 hover:text-gray-300 border-transparent"
          }`}
        >
          {t.icon}
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main layout                                                       */
/* ------------------------------------------------------------------ */

export default function CodeMode() {
  const showSidebar = useCodeStore((s) => s.showSidebar);
  const showTerminal = useCodeStore((s) => s.showTerminal);
  const showDiff = useCodeStore((s) => s.showDiff);
  const showSettings = useCodeStore((s) => s.showSettings);
  const showKeybindings = useCodeStore((s) => s.showKeybindings);
  const toggleSettings = useCodeStore((s) => s.toggleSettings);
  const activeSidebarPanel = useCodeStore((s) => s.activeSidebarPanel);
  const setActiveSidebarPanel = useCodeStore((s) => s.setActiveSidebarPanel);
  const setShowSidebar = useCodeStore((s) => s.setShowSidebar);
  const quickOpenVisible = useCodeStore((s) => s.quickOpenVisible);
  const setQuickOpenVisible = useCodeStore((s) => s.setQuickOpenVisible);
  const commandPaletteVisible = useCodeStore((s) => s.commandPaletteVisible);
  const setCommandPaletteVisible = useCodeStore((s) => s.setCommandPaletteVisible);
  const symbolSearchVisible = useCodeStore((s) => s.symbolSearchVisible);
  const setSymbolSearchVisible = useCodeStore((s) => s.setSymbolSearchVisible);
  const setCurrentBranch = useCodeStore((s) => s.setCurrentBranch);
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const addPinnedRoot = useCodeStore((s) => s.addPinnedRoot);
  const pendingWriteConfirmation = useCodeStore((s) => s.pendingWriteConfirmation);
  const setPendingWriteConfirmation = useCodeStore((s) => s.setPendingWriteConfirmation);
  const addAllowedExternalPath = useCodeStore((s) => s.addAllowedExternalPath);
  const markFileSaved = useCodeStore((s) => s.markFileSaved);
  const zenModeFilePath = useCodeStore((s) => s.zenModeFilePath);
  const splitFilePath = useCodeStore((s) => s.splitFilePath);
  const [showChatSidebar, setShowChatSidebar] = useState(false);
  const [bottomTab, setBottomTab] = useState<BottomTab>("terminal");
  const [showCodebaseQA, setShowCodebaseQA] = useState(false);
  const [callHierarchy, setCallHierarchy] = useState<{
    filePath: string;
    line: number;
    character: number;
  } | null>(null);
  const [mergeEditorState, setMergeEditorState] = useState<{ cwd: string; filePath: string } | null>(null);
  const [rebaseState, setRebaseState] = useState<{ cwd: string } | null>(null);

  useCodeShortcuts();
  useDebugEvents();

  useEffect(() => {
    activateAllInstalledExtensions().catch((err) =>
      console.warn("Extension activation failed:", err),
    );
  }, []);

  // Listen for merge editor open events from GitPanel
  useEffect(() => {
    const handler = (e: Event) => {
      const { cwd, filePath } = (e as CustomEvent).detail;
      setMergeEditorState({ cwd, filePath });
    };
    window.addEventListener("codemode:openMergeEditor", handler);
    return () => window.removeEventListener("codemode:openMergeEditor", handler);
  }, []);

  // Listen for interactive rebase open events from GitPanel
  useEffect(() => {
    const handler = (e: Event) => {
      const { cwd } = (e as CustomEvent).detail;
      setRebaseState({ cwd });
    };
    window.addEventListener("codemode:openInteractiveRebase", handler);
    return () => window.removeEventListener("codemode:openInteractiveRebase", handler);
  }, []);

  useEffect(() => {
    const handler = () => setShowCodebaseQA((v) => !v);
    window.addEventListener("codemode:toggleQA", handler);
    return () => window.removeEventListener("codemode:toggleQA", handler);
  }, []);

  // Listen for call hierarchy trigger
  useEffect(() => {
    const handler = () => {
      const state = useCodeStore.getState();
      const afp = state.activeFilePath;
      if (!afp) return;
      const pos = state.cursorPosition;
      setCallHierarchy({
        filePath: afp,
        line: pos.lineNumber - 1,
        character: pos.column - 1,
      });
    };
    window.addEventListener("codemode:showCallHierarchy", handler);
    return () => window.removeEventListener("codemode:showCallHierarchy", handler);
  }, []);
  const { recoveredFileCount, dismissRecovery } = useSessionRestore();
  useFileWatcher();
  useMonacoLsp();
  useLspDocSync();

  const { trackFileAccess } = useWorkspaceMemory();
  useFileChangeDetection();

  const activeFilePath_ws = useCodeStore((s) => s.activeFilePath);
  useEffect(() => {
    if (activeFilePath_ws) trackFileAccess(activeFilePath_ws);
  }, [activeFilePath_ws, trackFileAccess]);

  useEffect(() => {
    const root = pinnedRoots[0];
    if (!root) return;
    nativeGit.branch(root).then((r) => {
      if (r.code === 0) setCurrentBranch(r.stdout.trim());
    });
  }, [pinnedRoots, setCurrentBranch]);

  const handleActivityClick = (panel: typeof activeSidebarPanel) => {
    if (showSidebar && activeSidebarPanel === panel) {
      setShowSidebar(false);
    } else {
      setActiveSidebarPanel(panel);
    }
  };

  const sidebarContent = (() => {
    switch (activeSidebarPanel) {
      case "search": return <SearchPanel />;
      case "git": return <GitPanel />;
      case "extensions": return <ExtensionsPanel />;
      case "tasks": return <TaskRunner />;
      case "testing": return <TestExplorer />;
      case "timeline": return <LocalHistoryPanel />;
      case "outline": return <OutlineView />;
      case "debug": return <DebugPanel />;
      default: return <FileExplorer />;
    }
  })();

  return (
    <div className="h-full w-full flex flex-col bg-gray-900">
      {recoveredFileCount > 0 && (
        <CrashRecoveryBanner
          fileCount={recoveredFileCount}
          onDismiss={dismissRecovery}
        />
      )}
      <div className="flex-1 min-h-0 flex">
        {/* Activity bar */}
        <div className="w-[40px] bg-gray-900 border-r border-gray-800 flex flex-col items-center py-1 shrink-0">
          <ActivityItem
            icon={<Files size={20} />}
            active={showSidebar && activeSidebarPanel === "explorer"}
            title="Explorer (Cmd+B)"
            onClick={() => handleActivityClick("explorer")}
          />
          <ActivityItem
            icon={<Search size={20} />}
            active={showSidebar && activeSidebarPanel === "search"}
            title="Search (Cmd+Shift+F)"
            onClick={() => handleActivityClick("search")}
          />
          <ActivityItem
            icon={<GitBranch size={20} />}
            active={showSidebar && activeSidebarPanel === "git"}
            title="Source Control"
            onClick={() => handleActivityClick("git")}
          />
          <ActivityItem
            icon={<Blocks size={20} />}
            active={showSidebar && activeSidebarPanel === "extensions"}
            title="Extensions (⌘⇧X)"
            onClick={() => handleActivityClick("extensions")}
          />
          <ActivityItem
            icon={<ListTodo size={20} />}
            active={showSidebar && activeSidebarPanel === "tasks"}
            title="Tasks (⌘⇧B: Build)"
            onClick={() => handleActivityClick("tasks")}
          />
          <ActivityItem
            icon={<FlaskConical size={20} />}
            active={showSidebar && activeSidebarPanel === "testing"}
            title="Testing (⌘⇧T: Test)"
            onClick={() => handleActivityClick("testing")}
          />
          <ActivityItem
            icon={<Clock size={20} />}
            active={showSidebar && activeSidebarPanel === "timeline"}
            title="Timeline"
            onClick={() => handleActivityClick("timeline")}
          />
          <ActivityItem
            icon={<ListTree size={20} />}
            active={showSidebar && activeSidebarPanel === "outline"}
            title="Outline"
            onClick={() => handleActivityClick("outline")}
          />
          <ActivityItem
            icon={<Bug size={20} />}
            active={showSidebar && activeSidebarPanel === "debug"}
            title="Debug (F5)"
            onClick={() => handleActivityClick("debug")}
          />

          <div className="mt-auto flex flex-col items-center">
            <ActivityItem
              icon={<MessageSquare size={20} />}
              active={showChatSidebar}
              title="AI Chat"
              onClick={() => setShowChatSidebar((v) => !v)}
            />
            <ActivityItem
              icon={<Settings size={20} />}
              active={showSettings}
              title="Settings (Cmd+,)"
              onClick={toggleSettings}
            />
          </div>
        </div>

        {/* Main area: sidebar + editor/terminal */}
        <Allotment proportionalLayout={false}>
          {showSidebar && (
            <Allotment.Pane preferredSize={250} minSize={150} maxSize={500}>
              <div className="h-full bg-gray-900 overflow-hidden">
                {sidebarContent}
              </div>
            </Allotment.Pane>
          )}

          <Allotment.Pane>
            <div className="flex flex-col h-full">
            <WorkspaceInfo />
            <CoverageSummaryBar />
            <Allotment vertical proportionalLayout={false} className="flex-1 min-h-0">
              <Allotment.Pane>
                {mergeEditorState ? (
                  <MergeEditor
                    cwd={mergeEditorState.cwd}
                    filePath={mergeEditorState.filePath}
                    onClose={() => setMergeEditorState(null)}
                    onResolved={() => {
                      setMergeEditorState(null);
                    }}
                  />
                ) : showKeybindings ? (
                  <KeybindingsPanel />
                ) : showSettings ? (
                  <SettingsPanel />
                ) : showDiff ? (
                  <DiffView />
                ) : splitFilePath ? (
                  <Allotment>
                    <Allotment.Pane>
                      <MonacoTabs />
                    </Allotment.Pane>
                    <Allotment.Pane>
                      <SplitEditor
                        filePath={splitFilePath}
                        onClose={() => useCodeStore.getState().setSplitFilePath(null)}
                      />
                    </Allotment.Pane>
                  </Allotment>
                ) : (
                  <MonacoTabs />
                )}
              </Allotment.Pane>

              {showTerminal && (
                <Allotment.Pane preferredSize={200} minSize={100}>
                  <div className="h-full flex flex-col">
                    <BottomPanelTabs activeTab={bottomTab} onTabChange={setBottomTab} />
                    <div className="flex-1 min-h-0">
                      {bottomTab === "terminal" && <TerminalPanel />}
                      {bottomTab === "problems" && <ProblemsPanel />}
                      {bottomTab === "output" && <OutputPanel />}
                      {bottomTab === "debugConsole" && <DebugConsole />}
                    </div>
                  </div>
                </Allotment.Pane>
              )}
            </Allotment>
            </div>
          </Allotment.Pane>

          {showChatSidebar && (
            <Allotment.Pane preferredSize={350} minSize={250} maxSize={500}>
              <ChatSidebar onClose={() => setShowChatSidebar(false)} />
            </Allotment.Pane>
          )}
        </Allotment>
      </div>

      <StatusBar />

      {quickOpenVisible && (
        <QuickOpen onClose={() => setQuickOpenVisible(false)} />
      )}

      {commandPaletteVisible && (
        <CommandPalette onClose={() => setCommandPaletteVisible(false)} />
      )}

      {symbolSearchVisible && (
        <SymbolSearch onClose={() => setSymbolSearchVisible(false)} />
      )}

      {pendingWriteConfirmation && (
        <WriteConfirmDialog
          filePath={pendingWriteConfirmation.filePath}
          onCancel={() => setPendingWriteConfirmation(null)}
          onAllow={async () => {
            const { filePath, content } = pendingWriteConfirmation;
            addAllowedExternalPath(filePath);
            const ok = await nativeFs.writeFile(filePath, content);
            if (ok) markFileSaved(filePath);
            setPendingWriteConfirmation(null);
          }}
          onPin={async () => {
            const { filePath, content } = pendingWriteConfirmation;
            const parentDir = filePath.split("/").slice(0, -1).join("/");
            addPinnedRoot(parentDir);
            const ok = await nativeFs.writeFile(filePath, content);
            if (ok) markFileSaved(filePath);
            setPendingWriteConfirmation(null);
          }}
        />
      )}

      {zenModeFilePath && (
        <ZenMode
          filePath={zenModeFilePath}
          onExit={() => useCodeStore.getState().setZenModeFilePath(null)}
        />
      )}

      {showCodebaseQA && (
        <CodebaseQA onClose={() => setShowCodebaseQA(false)} />
      )}

      {callHierarchy && (
        <CallHierarchy
          filePath={callHierarchy.filePath}
          line={callHierarchy.line}
          character={callHierarchy.character}
          onClose={() => setCallHierarchy(null)}
          onNavigate={async (uri, line, character) => {
            const state = useCodeStore.getState();
            const isOpen = state.openFiles.some((f) => f.path === uri);
            if (isOpen) {
              state.setActiveFile(uri);
            } else {
              const content = await nativeFs.readFile(uri);
              if (content !== null) state.openFile(uri, content);
            }
            setTimeout(() => {
              window.dispatchEvent(
                new CustomEvent("editor:goToLine", {
                  detail: { lineNumber: line, column: character },
                }),
              );
            }, 100);
          }}
          style={{ top: 120, left: "50%", transform: "translateX(-50%)" }}
        />
      )}

      {rebaseState && (
        <InteractiveRebase
          cwd={rebaseState.cwd}
          onClose={() => setRebaseState(null)}
          onComplete={() => {
            setRebaseState(null);
          }}
        />
      )}
    </div>
  );
}
