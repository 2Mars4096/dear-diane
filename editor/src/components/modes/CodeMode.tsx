/**
 * Code mode: VS Code-like IDE workspace with resizable panels.
 * Layout: ActivityBar | Sidebar | (EditorTabs / TerminalPanel) | StatusBar
 */
import { lazy, Suspense, useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Allotment } from "allotment";
import { useCodeStore, type MultiFileEditEntry } from "../../store/useCodeStore";
import MonacoTabs from "../code/MonacoTabs";
import ModeChatSidebar from "../shared/ModeChatSidebar";
import CrashRecoveryBanner from "../code/CrashRecoveryBanner";
import WorkspaceInfo from "../code/WorkspaceInfo";
import { useDebugEvents } from "../code/useDebugEvents";
import type { FileEdit } from "../code/MultiFileEdit";
import ProjectDetectionToast from "../code/ProjectDetectionToast";
import {
  detectProjectType,
  type ProjectDetection,
} from "../../lib/workspaceIntelligence";
import {
  CoverageSummaryBar,
} from "../code/CoverageOverlay";
import { useSessionRestore } from "../../hooks/useSessionRestore";
import { useFileWatcher } from "../../hooks/useFileWatcher";
import { useMonacoLsp } from "../../hooks/useMonacoLsp";
import { useLspDocSync } from "../../hooks/useLspDocSync";
import { useWorkspaceMemory } from "../../hooks/useWorkspaceMemory";
import { useFileChangeDetection } from "../../hooks/useFileChangeDetection";
import { useModeScopedWindowEvent } from "../../hooks/useModeScopedWindowEvent";
import { isElectron, nativeGit, nativeFs } from "../../lib/electronBridge";
import { activateAllInstalledExtensions } from "../../lib/extensions/extensionActivator";
import {
  DevelopmentActivityBar,
  DevelopmentBottomPanelSurface,
  DevelopmentBottomPanelTabs,
  DevelopmentModeStatusStrip,
  DevelopmentRuntimeBanner,
  DevelopmentSidebarSurface,
  DevelopmentStatusBar,
} from "./DevelopmentModeShell";
import type { BottomTab } from "./DevelopmentModeShell";
import { buildDevelopmentModeChatContext } from "./developmentModeChatContext";
import { useDevelopmentModeShortcuts } from "./useDevelopmentModeShortcuts";

const DiffView = lazy(() => import("../code/DiffView"));
const SettingsPanel = lazy(() => import("../code/SettingsPanel"));
const KeybindingsPanel = lazy(() => import("../code/KeybindingsPanel"));
const QuickOpen = lazy(() => import("../code/QuickOpen"));
const CommandPalette = lazy(() => import("../code/CommandPalette"));
const SymbolSearch = lazy(() => import("../code/SymbolSearch"));
const WriteConfirmDialog = lazy(() => import("../code/WriteConfirmDialog"));
const CodebaseQA = lazy(() => import("../code/CodebaseQA"));
const SplitEditor = lazy(() => import("../code/SplitEditor"));
const ZenMode = lazy(() => import("../code/ZenMode"));
const InteractiveRebase = lazy(() => import("../code/InteractiveRebase"));
const MergeEditor = lazy(() => import("../code/MergeEditor"));
const CallHierarchy = lazy(() => import("../code/CallHierarchy"));
const MultiFileEdit = lazy(() => import("../code/MultiFileEdit"));
const FeatureTour = lazy(() => import("../code/FeatureTour"));

/* ------------------------------------------------------------------ */
/*  Main layout                                                       */
/* ------------------------------------------------------------------ */

function DeferredSurfaceFallback({ label }: { label?: string }) {
  return (
    <div className="flex h-full items-center justify-center text-xs text-gray-500 dark:text-gray-400">
      {label ? `Loading ${label}...` : "Loading..."}
    </div>
  );
}

export default function CodeMode() {
  const electron = isElectron();
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
  const currentBranch = useCodeStore((s) => s.currentBranch);
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const addPinnedRoot = useCodeStore((s) => s.addPinnedRoot);
  const pendingWriteConfirmation = useCodeStore((s) => s.pendingWriteConfirmation);
  const setPendingWriteConfirmation = useCodeStore((s) => s.setPendingWriteConfirmation);
  const addAllowedExternalPath = useCodeStore((s) => s.addAllowedExternalPath);
  const markFileSaved = useCodeStore((s) => s.markFileSaved);
  const zenModeFilePath = useCodeStore((s) => s.zenModeFilePath);
  const splitFilePath = useCodeStore((s) => s.splitFilePath);
  const showMultiFileReview = useCodeStore((s) => s.showMultiFileReview);
  const multiFileEdits = useCodeStore((s) => s.multiFileEdits);
  const [showChatSidebar, setShowChatSidebar] = useState(false);
  const [sidebarPaneWidth, setSidebarPaneWidth] = useState(250);
  const [chatPaneWidth, setChatPaneWidth] = useState(350);
  const [bottomTab, setBottomTab] = useState<BottomTab>("terminal");
  const [showCodebaseQA, setShowCodebaseQA] = useState(false);
  const [callHierarchy, setCallHierarchy] = useState<{
    filePath: string;
    line: number;
    character: number;
  } | null>(null);
  const [projectDetectionToast, setProjectDetectionToast] =
    useState<ProjectDetection | null>(null);
  const [showFeatureTour, setShowFeatureTour] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      return localStorage.getItem("dan-hasSeenFeatureTour") !== "1";
    } catch {
      return true;
    }
  });
  const [mergeEditorState, setMergeEditorState] = useState<{ cwd: string; filePath: string } | null>(null);
  const [rebaseState, setRebaseState] = useState<{ cwd: string } | null>(null);
  const toggleChatSidebar = useCallback(() => {
    setShowChatSidebar((value) => !value);
  }, []);
  const closeChatSidebar = useCallback(() => {
    setShowChatSidebar(false);
  }, []);
  const toggleModeSidebar = useCallback(() => {
    useCodeStore.getState().toggleSidebar();
  }, []);
  const developmentModeChatContext = useCallback(
    () => buildDevelopmentModeChatContext(useCodeStore.getState()),
    [],
  );
  const sidebarPaneWidthRef = useRef(sidebarPaneWidth);
  sidebarPaneWidthRef.current = sidebarPaneWidth;
  const chatPaneWidthRef = useRef(chatPaneWidth);
  chatPaneWidthRef.current = chatPaneWidth;
  const lastDetectedRootRef = useRef<string | null>(null);

  useDevelopmentModeShortcuts();
  useDebugEvents();

  useEffect(() => {
    if (!electron) return;
    activateAllInstalledExtensions().catch((err) =>
      console.warn("Extension activation failed:", err),
    );
  }, [electron]);

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

  useModeScopedWindowEvent("development", "app:toggleModeChatSidebar", toggleChatSidebar);

  useModeScopedWindowEvent("development", "app:toggleModeSidebar", toggleModeSidebar);

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

  const handleFeatureTourComplete = useCallback(() => {
    setShowFeatureTour(false);
    try {
      localStorage.setItem("dan-hasSeenFeatureTour", "1");
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    const root = pinnedRoots[0];
    if (!root || root === lastDetectedRootRef.current) return;
    lastDetectedRootRef.current = root;
    let cancelled = false;
    void detectProjectType(root).then((detection) => {
      if (cancelled) return;
      if (
        detection.type !== "unknown"
        || detection.frameworks.length > 0
        || Boolean(detection.packageManager)
      ) {
        setProjectDetectionToast(detection);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [pinnedRoots]);

  useEffect(() => {
    const root = pinnedRoots[0];
    if (!electron || !root) return;
    nativeGit.branch(root).then((r) => {
      if (r.code === 0) setCurrentBranch(r.stdout.trim());
    });
  }, [electron, pinnedRoots, setCurrentBranch]);

  const handleActivityClick = (panel: typeof activeSidebarPanel) => {
    if (showSidebar && activeSidebarPanel === panel) {
      setShowSidebar(false);
    } else {
      setActiveSidebarPanel(panel);
    }
  };

  const handleRootSplitChange = useCallback((sizes: number[]) => {
    if (!showSidebar || sizes.length < 2) return;
    const nextWidth = Math.round(sizes[0] ?? 0);
    if (!Number.isFinite(nextWidth) || nextWidth < 150 || nextWidth > 500) return;
    if (Math.abs(nextWidth - sidebarPaneWidthRef.current) < 1) return;
    setSidebarPaneWidth(nextWidth);
  }, [showSidebar]);

  const handleChatSplitChange = useCallback((sizes: number[]) => {
    if (!showChatSidebar || sizes.length < 2) return;
    const nextWidth = Math.round(sizes[sizes.length - 1] ?? 0);
    if (!Number.isFinite(nextWidth) || nextWidth < 250 || nextWidth > 500) return;
    if (Math.abs(nextWidth - chatPaneWidthRef.current) < 1) return;
    setChatPaneWidth(nextWidth);
  }, [showChatSidebar]);

  const findContainingPinnedRoot = useCallback((filePath: string): string | null => {
    const matches = pinnedRoots
      .filter((root) => filePath === root || filePath.startsWith(root + "/"))
      .sort((a, b) => b.length - a.length);
    return matches[0] ?? null;
  }, [pinnedRoots]);

  const getCommonPinnedRoot = useCallback((filePaths: string[]): string | null => {
    let commonRoot: string | null = null;
    for (const filePath of filePaths) {
      const root = findContainingPinnedRoot(filePath);
      if (!root) return null;
      if (commonRoot === null) {
        commonRoot = root;
        continue;
      }
      if (commonRoot !== root) return null;
    }
    return commonRoot;
  }, [findContainingPinnedRoot]);

  const applyReviewedEdit = useCallback(
    async (entry: MultiFileEditEntry, accept: boolean): Promise<boolean> => {
      const store = useCodeStore.getState();
      if (!accept && entry.createdByThisTurn) {
        const exists = await nativeFs.exists(entry.filePath);
        if (exists) {
          await nativeFs.delete(entry.filePath);
        }
        store.closeFile(entry.filePath);
        return true;
      }

      const nextContent = accept ? entry.modifiedContent : entry.originalContent;
      if (nextContent === null) return false;

      const ok = await nativeFs.writeFile(entry.filePath, nextContent);
      if (!ok) return false;

      if (store.openFiles.some((file) => file.path === entry.filePath)) {
        if (accept) {
          store.reloadFileContent(entry.filePath, nextContent);
        } else {
          store.revertFile(entry.filePath, nextContent);
        }
      }

      window.dispatchEvent(
        new CustomEvent("lsp:fileSaved", {
          detail: { filePath: entry.filePath, text: nextContent },
        }),
      );
      return true;
    },
    [],
  );

  const acceptPendingMultiFileEdits = useCallback(async () => {
    const store = useCodeStore.getState();
    const edits = store.multiFileEdits;
    for (let index = 0; index < edits.length; index += 1) {
      const entry = edits[index];
      if (!entry || entry.accepted !== null) continue;
      if (await applyReviewedEdit(entry, true)) {
        store.acceptMultiFileEdit(index);
      }
    }
  }, [applyReviewedEdit]);

  const multiFileEditProps = useMemo((): {
    edits: FileEdit[];
    onAccept: (filePath: string) => void;
    onReject: (filePath: string) => void;
    onAcceptAll: () => void;
    onClose: () => void;
  } | null => {
    if (!showMultiFileReview || multiFileEdits.length === 0) return null;
    const store = useCodeStore.getState();
    return {
      edits: multiFileEdits.map((e) => ({
        filePath: e.filePath,
        original: e.originalContent ?? "",
        modified: e.modifiedContent,
        accepted: e.accepted,
      })),
      onAccept: (filePath: string) => {
        const idx = multiFileEdits.findIndex((e) => e.filePath === filePath);
        const entry = idx >= 0 ? multiFileEdits[idx] : null;
        if (!entry) return;
        void (async () => {
          if (await applyReviewedEdit(entry, true)) {
            store.acceptMultiFileEdit(idx);
          }
        })();
      },
      onReject: (filePath: string) => {
        const idx = multiFileEdits.findIndex((e) => e.filePath === filePath);
        const entry = idx >= 0 ? multiFileEdits[idx] : null;
        if (!entry) return;
        void (async () => {
          if (await applyReviewedEdit(entry, false)) {
            store.rejectMultiFileEdit(idx);
          }
        })();
      },
      onAcceptAll: () => {
        void acceptPendingMultiFileEdits();
      },
      onClose: () => store.closeMultiFileReview(),
    };
  }, [acceptPendingMultiFileEdits, applyReviewedEdit, multiFileEdits, showMultiFileReview]);

  const handleApplyAllAndTest = useCallback(async () => {
    const store = useCodeStore.getState();
    const reviewRoot = getCommonPinnedRoot(
      store.multiFileEdits.map((edit) => edit.filePath),
    );
    await acceptPendingMultiFileEdits();
    store.closeMultiFileReview();
    const root = reviewRoot;
    if (!root) return;
    const detection = await detectProjectType(root);
    let testCmd: string | null = null;
    if (detection.type === "node") {
      testCmd = `${detection.packageManager ?? "npm"} test`;
    } else if (detection.type === "python") {
      testCmd = "pytest";
    } else if (detection.type === "rust") {
      testCmd = "cargo test";
    } else if (detection.type === "go") {
      testCmd = "go test ./...";
    }
    if (testCmd) {
      window.dispatchEvent(
        new CustomEvent("chat:shellCommand", {
          detail: { command: testCmd, cwd: root },
        }),
      );
    }
  }, [acceptPendingMultiFileEdits, getCommonPinnedRoot]);

  return (
    <div className="h-full w-full flex flex-col bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-white">
      {recoveredFileCount > 0 && (
        <CrashRecoveryBanner
          fileCount={recoveredFileCount}
          onDismiss={dismissRecovery}
        />
      )}
      <DevelopmentRuntimeBanner electron={electron} />
      <div className="flex-1 min-h-0 flex">
        <DevelopmentActivityBar
          electron={electron}
          showSidebar={showSidebar}
          activeSidebarPanel={activeSidebarPanel}
          showChatSidebar={showChatSidebar}
          showSettings={showSettings}
          onPanelClick={handleActivityClick}
          onToggleChat={toggleChatSidebar}
          onToggleSettings={toggleSettings}
        />

        {/* Main area: sidebar + editor/terminal + chat */}
        <Allotment proportionalLayout={false} onChange={handleRootSplitChange}>
          {showSidebar && (
            <Allotment.Pane preferredSize={sidebarPaneWidth} minSize={150} maxSize={500}>
              <div className="h-full bg-white overflow-hidden dark:bg-gray-900">
                <DevelopmentSidebarSurface
                  panel={activeSidebarPanel}
                  electron={electron}
                />
              </div>
            </Allotment.Pane>
          )}

          <Allotment.Pane>
            <Allotment proportionalLayout={false} onChange={handleChatSplitChange}>
              <Allotment.Pane>
                <div className="flex flex-col h-full">
                  <WorkspaceInfo />
                  <CoverageSummaryBar />
                  <DevelopmentModeStatusStrip
                    electron={electron}
                    currentBranch={currentBranch}
                    pinnedRoots={pinnedRoots}
                  />
                  <Allotment vertical proportionalLayout={false} className="flex-1 min-h-0">
                    <Allotment.Pane>
                      {mergeEditorState ? (
                        <Suspense fallback={<DeferredSurfaceFallback label="merge editor" />}>
                          <MergeEditor
                            cwd={mergeEditorState.cwd}
                            filePath={mergeEditorState.filePath}
                            onClose={() => setMergeEditorState(null)}
                            onResolved={() => {
                              setMergeEditorState(null);
                            }}
                          />
                        </Suspense>
                      ) : showMultiFileReview && multiFileEditProps ? (
                        <Suspense fallback={<DeferredSurfaceFallback label="review workspace" />}>
                          <MultiFileEdit
                            {...multiFileEditProps}
                            onApplyAllAndTest={handleApplyAllAndTest}
                          />
                        </Suspense>
                      ) : showKeybindings ? (
                        <Suspense fallback={<DeferredSurfaceFallback label="keybindings" />}>
                          <KeybindingsPanel />
                        </Suspense>
                      ) : showSettings ? (
                        <Suspense fallback={<DeferredSurfaceFallback label="settings" />}>
                          <SettingsPanel />
                        </Suspense>
                      ) : showDiff ? (
                        <Suspense fallback={<DeferredSurfaceFallback label="diff" />}>
                          <DiffView />
                        </Suspense>
                      ) : splitFilePath ? (
                        <Allotment>
                          <Allotment.Pane>
                            <MonacoTabs />
                          </Allotment.Pane>
                          <Allotment.Pane>
                            <Suspense fallback={<DeferredSurfaceFallback label="split editor" />}>
                              <SplitEditor
                                filePath={splitFilePath}
                                onClose={() => useCodeStore.getState().setSplitFilePath(null)}
                              />
                            </Suspense>
                          </Allotment.Pane>
                        </Allotment>
                      ) : (
                        <MonacoTabs />
                      )}
                    </Allotment.Pane>

                    {showTerminal && (
                      <Allotment.Pane preferredSize={200} minSize={100}>
                        <div className="h-full flex flex-col">
                          <DevelopmentBottomPanelTabs
                            activeTab={bottomTab}
                            onTabChange={setBottomTab}
                          />
                          <div className="flex-1 min-h-0">
                            <DevelopmentBottomPanelSurface activeTab={bottomTab} />
                          </div>
                        </div>
                      </Allotment.Pane>
                    )}
                  </Allotment>
                </div>
              </Allotment.Pane>

              {showChatSidebar && (
                <Allotment.Pane preferredSize={chatPaneWidth} minSize={250} maxSize={500}>
                  <ModeChatSidebar
                    mode="development"
                    onClose={closeChatSidebar}
                    contextProvider={developmentModeChatContext}
                  />
                </Allotment.Pane>
              )}
            </Allotment>
          </Allotment.Pane>
        </Allotment>
      </div>

      <DevelopmentStatusBar />

      {projectDetectionToast && (
        <ProjectDetectionToast
          detection={projectDetectionToast}
          onConfigure={() => {
            setShowSidebar(true);
            setActiveSidebarPanel("extensions");
          }}
          onDismiss={() => setProjectDetectionToast(null)}
        />
      )}

      {showFeatureTour && (
        <Suspense fallback={null}>
          <FeatureTour onComplete={handleFeatureTourComplete} />
        </Suspense>
      )}

      {quickOpenVisible && (
        <Suspense fallback={null}>
          <QuickOpen onClose={() => setQuickOpenVisible(false)} />
        </Suspense>
      )}

      {commandPaletteVisible && (
        <Suspense fallback={null}>
          <CommandPalette onClose={() => setCommandPaletteVisible(false)} />
        </Suspense>
      )}

      {symbolSearchVisible && (
        <Suspense fallback={null}>
          <SymbolSearch onClose={() => setSymbolSearchVisible(false)} />
        </Suspense>
      )}

      {pendingWriteConfirmation && (
        <Suspense fallback={null}>
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
        </Suspense>
      )}

      {zenModeFilePath && (
        <Suspense fallback={null}>
          <ZenMode
            filePath={zenModeFilePath}
            onExit={() => useCodeStore.getState().setZenModeFilePath(null)}
          />
        </Suspense>
      )}

      {showCodebaseQA && (
        <Suspense fallback={null}>
          <CodebaseQA onClose={() => setShowCodebaseQA(false)} />
        </Suspense>
      )}

      {callHierarchy && (
        <Suspense fallback={null}>
          <CallHierarchy
            filePath={callHierarchy.filePath}
            line={callHierarchy.line}
            character={callHierarchy.character}
            onClose={() => setCallHierarchy(null)}
            onNavigate={async (uri, line, character) => {
              const state = useCodeStore.getState();
              const isOpen = state.openFiles.some((file) => file.path === uri);
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
        </Suspense>
      )}

      {rebaseState && (
        <Suspense fallback={null}>
          <InteractiveRebase
            cwd={rebaseState.cwd}
            onClose={() => setRebaseState(null)}
            onComplete={() => {
              setRebaseState(null);
            }}
          />
        </Suspense>
      )}
    </div>
  );
}
