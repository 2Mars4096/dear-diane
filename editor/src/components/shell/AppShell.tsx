/**
 * AppShell: top-level layout that wraps all mode workspaces.
 * Structure: WorkspaceTabs (top) → ModeBar → mode viewport (full remaining height).
 * Each mode is deferred-mounted per workspace and remounts when the active workspace changes,
 * so mode-local frontend UI state does not leak across workspaces.
 */
import { useEffect, useRef, useState, lazy, Suspense } from "react";
import ModeBar, { useModeShortcuts } from "./ModeBar";
import WorkspaceTabs, { useWorkspaceShortcuts } from "./WorkspaceTabs";
import GlobalSettingsPanel from "./GlobalSettingsPanel";
import GlobalCommandPalette from "./GlobalCommandPalette";
import ErrorBoundary from "./ErrorBoundary";
import UpdateNotification from "./UpdateNotification";
import ConnectionBanner from "./ConnectionBanner";
import { ChatSkeleton, EditorSkeleton } from "./PanelSkeleton";
import { useTitleAndFavicon } from "../../hooks/useTitleAndFavicon";
import { useEventRouter } from "../../hooks/useEventRouter";
import { useWorkspaceSession } from "../../hooks/useWorkspaceSession";
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
const activatedModesByWorkspace = new Map<string, Set<string>>();

function shouldRenderModeForWorkspace(
  workspaceKey: string,
  modeKey: string,
  isActive: boolean,
) {
  let mountedModes = activatedModesByWorkspace.get(workspaceKey);
  if (!mountedModes) {
    mountedModes = new Set<string>();
    activatedModesByWorkspace.set(workspaceKey, mountedModes);
  }
  if (isActive) {
    mountedModes.add(modeKey);
  }
  return mountedModes.has(modeKey);
}

function ModePanel({
  workspaceKey,
  modeKey,
  isActive,
  children,
  className = "",
}: {
  workspaceKey: string;
  modeKey: string;
  isActive: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  const everActive = shouldRenderModeForWorkspace(
    workspaceKey,
    modeKey,
    isActive,
  );

  if (!everActive) return null;

  return (
    <div
      className={`absolute inset-0 ${className}`}
      style={
        isActive
          ? { zIndex: 10 }
          : { zIndex: 0, visibility: "hidden", pointerEvents: "none" }
      }
    >
      {children}
    </div>
  );
}

function useDefaultWorkspace() {
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const createWorkspace = useWorkspaceStore((s) => s.createWorkspace);
  const activeMode = useAppStore((s) => s.activeMode);

  useEffect(() => {
    if (workspaces.length === 0) {
      if (activeMode === "research") {
        createWorkspace(undefined, "research");
      } else {
        createWorkspace("Scratch");
      }
    }
  }, [workspaces.length, createWorkspace, activeMode]);
}

function useSyncWorkspaceMode() {
  const activeMode = useAppStore((s) => s.activeMode);
  const activeWsId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const activeWsLastMode = useWorkspaceStore((s) => {
    const ws = s.workspaces.find((w) => w.id === s.activeWorkspaceId);
    return ws?.lastActiveMode;
  });
  const updateWorkspace = useWorkspaceStore((s) => s.updateWorkspace);
  const prevActiveWsId = useRef<string | null>(null);

  useEffect(() => {
    const workspaceChanged = prevActiveWsId.current !== activeWsId;
    prevActiveWsId.current = activeWsId;

    if (!activeWsId || workspaceChanged) return;

    if (activeWsLastMode !== activeMode) {
      updateWorkspace(activeWsId, { lastActiveMode: activeMode });
    }
  }, [activeMode, activeWsId, activeWsLastMode, updateWorkspace]);
}

export default function AppShell() {
  const activeMode = useAppStore((s) => s.activeMode);
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const globalPaletteVisible = useAppStore((s) => s.globalPaletteVisible);
  const [showGlobalSettings, setShowGlobalSettings] = useState(false);
  const workspaceRenderKey = activeWorkspaceId ?? "no-workspace";

  useModeShortcuts();
  useWorkspaceShortcuts();
  useDefaultWorkspace();
  useSyncWorkspaceMode();
  useWorkspaceSession();
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
      action: () => window.dispatchEvent(new CustomEvent("app:toggleModeSidebar")),
      description: "Toggle Sidebar",
    });
    registerGlobalShortcut({
      id: "global.chatSidebar",
      keys: "cmd+j",
      action: () => window.dispatchEvent(new CustomEvent("app:toggleModeChatSidebar")),
      description: "Toggle AI Chat Sidebar",
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
      <ConnectionBanner />
      <WorkspaceTabs />
      <ModeBar />

      <div className="flex-1 min-h-0 relative" key={`viewport-${workspaceRenderKey}`}>
        <ModePanel
          workspaceKey={workspaceRenderKey}
          modeKey="chat"
          isActive={activeMode === "chat"}
        >
          <ErrorBoundary name="Chat" isActive={activeMode === "chat"}>
            <Suspense fallback={<ChatSkeleton />}>
              <ChatMode />
            </Suspense>
          </ErrorBoundary>
        </ModePanel>
        <ModePanel
          workspaceKey={workspaceRenderKey}
          modeKey="development"
          isActive={activeMode === "development"}
        >
          <ErrorBoundary name="Development" isActive={activeMode === "development"}>
            <Suspense fallback={<EditorSkeleton />}>
              <CodeMode />
            </Suspense>
          </ErrorBoundary>
        </ModePanel>
        <ModePanel
          workspaceKey={workspaceRenderKey}
          modeKey="operations"
          isActive={activeMode === "operations"}
        >
          <ErrorBoundary name="Operations" isActive={activeMode === "operations"}>
            <Suspense fallback={<EditorSkeleton />}>
              <OperationsMode />
            </Suspense>
          </ErrorBoundary>
        </ModePanel>
        <ModePanel
          workspaceKey={workspaceRenderKey}
          modeKey="research"
          isActive={activeMode === "research"}
        >
          <ErrorBoundary name="Research" isActive={activeMode === "research"}>
            <Suspense fallback={<EditorSkeleton />}>
              <ResearchMode />
            </Suspense>
          </ErrorBoundary>
        </ModePanel>

        {PLACEHOLDER_MODES.map((m) => (
          <ModePanel
            key={m}
            workspaceKey={workspaceRenderKey}
            modeKey={m}
            isActive={activeMode === m}
          >
            <div className="flex items-center justify-center h-full text-gray-400">
              <div className="text-center">
                <p className="text-lg font-medium">{MODE_LABELS[m]}</p>
                <p className="text-sm mt-1">Coming soon</p>
              </div>
            </div>
          </ModePanel>
        ))}
      </div>

      <ToastContainer />
      {showGlobalSettings && (
        <GlobalSettingsPanel onClose={() => setShowGlobalSettings(false)} />
      )}
      {globalPaletteVisible && <GlobalCommandPalette />}
    </div>
  );
}
