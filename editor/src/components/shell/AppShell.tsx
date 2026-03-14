/**
 * AppShell: top-level layout that wraps all mode workspaces.
 * Structure: WorkspaceTabs (top) → ModeBar → Breadcrumb → SidebarHost + active mode workspace (center) → overlays.
 * Each mode is lazy-rendered but kept mounted to preserve state.
 */
import { useEffect, useState, lazy, Suspense } from "react";
import ModeBar, { useModeShortcuts } from "./ModeBar";
import WorkspaceTabs, { useWorkspaceShortcuts } from "./WorkspaceTabs";
import Breadcrumb from "./Breadcrumb";
import SidebarHost from "./SidebarHost";
import GlobalSettingsPanel from "./GlobalSettingsPanel";
import GlobalCommandPalette from "./GlobalCommandPalette";
import ErrorBoundary from "./ErrorBoundary";
import PersistentChatBar from "./PersistentChatBar";
import UpdateNotification from "./UpdateNotification";
import { ChatSkeleton, EditorSkeleton } from "./PanelSkeleton";
import { useTitleAndFavicon } from "../../hooks/useTitleAndFavicon";
import { useEventRouter } from "../../hooks/useEventRouter";
import { useAppStore } from "../../store/useAppStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import ToastContainer from "../ToastContainer";
import {
  initGlobalShortcuts,
  registerGlobalShortcut,
} from "../../lib/globalShortcuts";

const ChatMode = lazy(() => import("../modes/ChatMode"));
const CodeMode = lazy(() => import("../modes/CodeMode"));
const ResearchMode = lazy(() => import("../modes/ResearchMode"));
const OperationsMode = lazy(() => import("../modes/OperationsMode"));

const PLACEHOLDER_MODES = ["analytics", "content"] as const;
const MODE_LABELS: Record<string, string> = {
  analytics: "Analytics Mode",
  content: "Content Mode",
};

function ModePanel({
  isActive,
  children,
  className = "",
}: {
  isActive: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`absolute inset-0 transition-opacity duration-200 ${className} ${
        isActive ? "opacity-100 z-10" : "opacity-0 z-0 pointer-events-none"
      }`}
    >
      {children}
    </div>
  );
}

function useDefaultWorkspace() {
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const createWorkspace = useWorkspaceStore((s) => s.createWorkspace);

  useEffect(() => {
    if (workspaces.length === 0) {
      createWorkspace("Scratch");
    }
  }, [workspaces.length, createWorkspace]);
}

function useSyncWorkspaceMode() {
  const activeMode = useAppStore((s) => s.activeMode);
  const activeWs = useWorkspaceStore((s) => s.getActiveWorkspace());
  const updateWorkspace = useWorkspaceStore((s) => s.updateWorkspace);

  useEffect(() => {
    if (activeWs && activeWs.lastActiveMode !== activeMode) {
      updateWorkspace(activeWs.id, { lastActiveMode: activeMode });
    }
  }, [activeMode, activeWs, updateWorkspace]);
}

export default function AppShell() {
  const activeMode = useAppStore((s) => s.activeMode);
  const activeWorkspace = useWorkspaceStore((s) => s.getActiveWorkspace());
  const globalPaletteVisible = useAppStore((s) => s.globalPaletteVisible);
  const [showGlobalSettings, setShowGlobalSettings] = useState(false);

  useModeShortcuts();
  useWorkspaceShortcuts();
  useDefaultWorkspace();
  useSyncWorkspaceMode();
  useTitleAndFavicon();
  useEventRouter();

  useEffect(() => {
    const cleanup = initGlobalShortcuts();

    registerGlobalShortcut({
      id: "global.commandPalette",
      keys: "cmd+shift+p",
      action: () => useAppStore.getState().setGlobalPaletteVisible(true),
      description: "Open Command Palette",
    });
    registerGlobalShortcut({
      id: "global.settings",
      keys: "cmd+,",
      action: () => setShowGlobalSettings(true),
      description: "Open Settings",
    });
    registerGlobalShortcut({
      id: "global.sidebar",
      keys: "cmd+b",
      action: () => useAppStore.getState().toggleSidebar(),
      description: "Toggle Sidebar",
    });

    const onOpenSettings = () => setShowGlobalSettings(true);
    window.addEventListener("app:openSettings", onOpenSettings);
    window.addEventListener("app:openKeybindings", onOpenSettings);

    return () => {
      cleanup();
      window.removeEventListener("app:openSettings", onOpenSettings);
      window.removeEventListener("app:openKeybindings", onOpenSettings);
    };
  }, []);

  return (
    <div className="h-screen w-screen flex flex-col bg-white dark:bg-gray-950">
      <UpdateNotification />
      <WorkspaceTabs />
      <ModeBar />
      <Breadcrumb />

      <div className="flex-1 min-h-0 flex">
        <SidebarHost
          mode={activeMode}
          workspaceName={activeWorkspace?.name}
          workspaceColor={activeWorkspace?.color}
        />
        <div className="flex-1 min-h-0 relative">
        <ModePanel isActive={activeMode === "chat"} className="overflow-hidden flex flex-col">
          <ErrorBoundary name="Chat">
            <Suspense fallback={<ChatSkeleton />}>
              <ChatMode />
            </Suspense>
          </ErrorBoundary>
        </ModePanel>
        <ModePanel isActive={activeMode === "development"}>
          <ErrorBoundary name="Development">
            <Suspense fallback={<EditorSkeleton />}>
              <CodeMode />
            </Suspense>
          </ErrorBoundary>
        </ModePanel>
        <ModePanel isActive={activeMode === "operations"}>
          <ErrorBoundary name="Operations">
            <Suspense fallback={<EditorSkeleton />}>
              <OperationsMode />
            </Suspense>
          </ErrorBoundary>
        </ModePanel>
        <ModePanel isActive={activeMode === "research"}>
          <ErrorBoundary name="Research">
            <Suspense fallback={<EditorSkeleton />}>
              <ResearchMode />
            </Suspense>
          </ErrorBoundary>
        </ModePanel>

        {PLACEHOLDER_MODES.map((m) => (
          <ModePanel key={m} isActive={activeMode === m}>
            <div className="flex items-center justify-center h-full text-gray-400">
              <div className="text-center">
                <p className="text-lg font-medium">{MODE_LABELS[m]}</p>
                <p className="text-sm mt-1">Coming soon</p>
              </div>
            </div>
          </ModePanel>
        ))}
        </div>
      </div>

      <PersistentChatBar />
      <ToastContainer />
      {showGlobalSettings && (
        <GlobalSettingsPanel onClose={() => setShowGlobalSettings(false)} />
      )}
      {globalPaletteVisible && <GlobalCommandPalette />}
    </div>
  );
}
