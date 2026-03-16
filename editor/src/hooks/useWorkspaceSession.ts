import { useEffect, useLayoutEffect, useRef } from "react";
import { useWorkspaceStore } from "../store/useWorkspaceStore";
import { useAppStore } from "../store/useAppStore";
import { useCodeStore } from "../store/useCodeStore";
import { useResearchStore } from "../store/useResearchStore";
import { nativeFs, nativeTerminal } from "../lib/electronBridge";
import {
  saveSession,
  loadSession,
  type WorkspaceSession,
} from "../lib/sessionPersistence";

function resolveWorkspaceMode(
  workspaceId: string,
  session: WorkspaceSession | null,
) {
  if (session?.lastActiveMode) return session.lastActiveMode;
  return (
    useWorkspaceStore.getState().workspaces.find((w) => w.id === workspaceId)
      ?.lastActiveMode ?? "chat"
  );
}

function applyCodeSession(code: WorkspaceSession["code"]) {
  const store = useCodeStore.getState();

  store.restoreSession({
    pinnedRoots: code.pinnedRoots,
    showTerminal: code.showTerminal,
    showSidebar: code.showSidebar,
    activeSidebarPanel: code.activeSidebarPanel as
      | "explorer"
      | "search"
      | "git"
      | "extensions"
      | "timeline"
      | "tasks"
      | "testing"
      | "outline"
      | "debug"
      | "workflow"
      | "furnace",
    recentFiles: code.recentFiles,
  });

  useCodeStore.setState({
    expandedDirs: code.expandedDirs ?? {},
    currentBranch: code.currentBranch ?? "",
  });

  const currentPaths = new Set(store.openFiles.map((f) => f.path));

  void (async () => {
    for (const filePath of code.openFilePaths) {
      if (currentPaths.has(filePath)) continue;
      try {
        const content = await nativeFs.readFile(filePath);
        if (content !== null) useCodeStore.getState().openFile(filePath, content);
      } catch { /* file gone — skip */ }
    }
    if (
      code.activeFilePath &&
      useCodeStore.getState().openFiles.some((f) => f.path === code.activeFilePath)
    ) {
      useCodeStore.getState().setActiveFile(code.activeFilePath);
    }
  })();
}

function applyResearchSession(research: WorkspaceSession["research"]) {
  useResearchStore.setState({
    primaryTab: (research.primaryTab as "editor" | "reader" | "furnace") ?? "editor",
    contextTab:
      (research.contextTab as
        | "references"
        | "reviews"
        | "outline"
        | "notes"
        | "distillation") ?? "references",
    secondaryTab: (research.secondaryTab as "code" | "figures" | "data") ?? "code",
    activePaperId: research.activePaperId,
    papers: research.papers ?? [],
    notes: research.notes ?? [],
    documentContent: research.documentContent ?? "",
    showPipeline: research.showPipeline ?? false,
    showSecondary: research.showSecondary ?? false,
    showContextPanel: research.showContextPanel ?? false,
    activeRailSection:
      (research.activeRailSection as "library" | "plan" | "training") ?? "library",
  });
}

function clearCodeState() {
  useCodeStore.setState({
    pinnedRoots: [],
    openFiles: [],
    activeFilePath: null,
    fileTree: {},
    expandedDirs: {},
    recentFiles: [],
    terminals: [],
    activeTerminalId: null,
    showTerminal: true,
    showSidebar: true,
    activeSidebarPanel: "explorer",
    currentBranch: "",
    diffFile: null,
    showDiff: false,
    agentSuggestedFiles: [],
    recentlyClosed: [],
  });
}

function clearResearchState() {
  useResearchStore.setState({
    primaryTab: "editor",
    contextTab: "references",
    secondaryTab: "code",
    activePaperId: null,
    papers: [],
    notes: [],
    documentContent: "",
    pipeline: [],
    showPipeline: false,
    showSecondary: false,
    showContextPanel: false,
    activeRailSection: "library",
  });
}

export function useWorkspaceSession() {
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const prevWsId = useRef<string | null>(null);
  const initialized = useRef(false);

  useLayoutEffect(() => {
    if (!activeWorkspaceId) return;

    if (!initialized.current) {
      initialized.current = true;
      const session = loadSession(activeWorkspaceId);
      if (session) {
        applyCodeSession(session.code);
        applyResearchSession(session.research);
      }
      useAppStore.getState().setMode(resolveWorkspaceMode(activeWorkspaceId, session));
      prevWsId.current = activeWorkspaceId;
      return;
    }

    if (prevWsId.current && prevWsId.current !== activeWorkspaceId) {
      const activeMode = useAppStore.getState().activeMode;
      saveSession(prevWsId.current, activeMode);
      for (const terminal of useCodeStore.getState().terminals) {
        nativeTerminal.kill(terminal.id);
      }

      clearCodeState();
      clearResearchState();

      const newSession = loadSession(activeWorkspaceId);
      if (newSession) {
        applyCodeSession(newSession.code);
        applyResearchSession(newSession.research);
      }
      useAppStore.getState().setMode(resolveWorkspaceMode(activeWorkspaceId, newSession));
    }

    prevWsId.current = activeWorkspaceId;
  }, [activeWorkspaceId]);

  useEffect(() => {
    if (!activeWorkspaceId) return;

    function persistSession() {
      const mode = useAppStore.getState().activeMode;
      saveSession(activeWorkspaceId ?? undefined, mode);
    }

    const interval = setInterval(() => {
      persistSession();
    }, 15_000);

    window.addEventListener("beforeunload", persistSession);

    return () => {
      clearInterval(interval);
      window.removeEventListener("beforeunload", persistSession);
    };
  }, [activeWorkspaceId]);
}
