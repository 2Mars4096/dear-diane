import {
  Suspense,
  lazy,
  type ReactNode,
} from "react";
import {
  AlertTriangle,
  Blocks,
  Bug,
  Clock,
  FileText,
  Files,
  FlaskConical,
  Flame,
  GitBranch,
  ListTodo,
  ListTree,
  MessageSquare,
  Search,
  Settings,
  Terminal as TerminalIcon,
  Workflow,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { useSettingsStore } from "../../store/useSettingsStore";
import ProblemsPanel, { useProblemsCount } from "../code/ProblemsPanel";
import OutputPanel from "../code/OutputPanel";
import SearchPanel from "../code/SearchPanel";
import FileExplorer from "../code/FileExplorer";
import TerminalPanel from "../code/TerminalPanel";
import {
  DevelopmentModeStatusStrip,
  DevelopmentPreviewPanel,
  FurnaceSidebarPanel,
  PREVIEW_ONLY_CODE_PANELS,
  WorkflowSidebarPanel,
  type DevelopmentSidebarPanel,
} from "./DevelopmentModePanels";

const GitPanel = lazy(() => import("../code/GitPanel"));
const ExtensionsPanel = lazy(() => import("../code/ExtensionsPanel"));
const TaskRunner = lazy(() => import("../code/TaskRunner"));
const TestExplorer = lazy(() => import("../code/TestExplorer"));
const DevelopmentTimelinePanel = lazy(
  () => import("../code/DevelopmentTimelinePanel"),
);
const OutlineView = lazy(() => import("../code/OutlineView"));
const DebugPanel = lazy(() => import("../code/DebugPanel"));
const DebugConsole = lazy(() => import("../code/DebugConsole"));

export type BottomTab = "terminal" | "problems" | "output" | "debugConsole";

function PanelLoadingState({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center text-xs text-gray-500 dark:text-gray-400">
      Loading {label}...
    </div>
  );
}

interface ActivityItemProps {
  icon: ReactNode;
  active?: boolean;
  title: string;
  previewOnly?: boolean;
  onClick?: () => void;
}

function ActivityItem({
  icon,
  active,
  title,
  previewOnly,
  onClick,
}: ActivityItemProps) {
  return (
    <button
      title={previewOnly ? `${title} (browser preview is limited)` : title}
      onClick={onClick}
      className={`flex w-full items-center justify-center py-2.5 transition-colors ${
        active
          ? "border-l-2 border-blue-600 bg-blue-50/80 text-blue-700 dark:border-white dark:bg-transparent dark:text-white"
          : "border-l-2 border-transparent text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-transparent dark:hover:text-gray-300"
      }`}
    >
      <span className="relative flex items-center justify-center">
        {icon}
        {previewOnly && (
          <span className="absolute -right-1 -top-0.5 h-1.5 w-1.5 rounded-full bg-amber-500" />
        )}
      </span>
    </button>
  );
}

function ActivitySeparator() {
  return (
    <div className="mx-auto my-1 w-5 border-t border-gray-300 dark:border-gray-700/50" />
  );
}

export function DevelopmentRuntimeBanner({
  electron,
}: {
  electron: boolean;
}) {
  if (electron) return null;

  return (
    <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
      <AlertTriangle size={14} className="shrink-0" />
      <span>
        Development mode requires the desktop app for folders, git, LSP, and
        terminal access. Browser preview stays available for demo and layout
        purposes only.
      </span>
    </div>
  );
}

export function DevelopmentActivityBar({
  electron,
  showSidebar,
  activeSidebarPanel,
  showChatSidebar,
  showSettings,
  onPanelClick,
  onToggleChat,
  onToggleSettings,
}: {
  electron: boolean;
  showSidebar: boolean;
  activeSidebarPanel: DevelopmentSidebarPanel;
  showChatSidebar: boolean;
  showSettings: boolean;
  onPanelClick: (panel: DevelopmentSidebarPanel) => void;
  onToggleChat: () => void;
  onToggleSettings: () => void;
}) {
  const isActive = (panel: DevelopmentSidebarPanel) =>
    showSidebar && activeSidebarPanel === panel;

  return (
    <div className="w-[40px] shrink-0 border-r border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <div className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto py-1">
        <ActivityItem
          icon={<Files size={20} />}
          active={isActive("explorer")}
          title="Explorer (Cmd+B)"
          previewOnly={!electron && PREVIEW_ONLY_CODE_PANELS.has("explorer")}
          onClick={() => onPanelClick("explorer")}
        />
        <ActivityItem
          icon={<Search size={20} />}
          active={isActive("search")}
          title="Search (Cmd+Shift+F)"
          previewOnly={!electron && PREVIEW_ONLY_CODE_PANELS.has("search")}
          onClick={() => onPanelClick("search")}
        />
        <ActivityItem
          icon={<GitBranch size={20} />}
          active={isActive("git")}
          title="Source Control"
          previewOnly={!electron && PREVIEW_ONLY_CODE_PANELS.has("git")}
          onClick={() => onPanelClick("git")}
        />

        <ActivitySeparator />

        <ActivityItem
          icon={<Blocks size={20} />}
          active={isActive("extensions")}
          title="Extensions (⌘⇧X)"
          onClick={() => onPanelClick("extensions")}
        />
        <ActivityItem
          icon={<ListTodo size={20} />}
          active={isActive("tasks")}
          title="Tasks (⌘⇧B: Build)"
          previewOnly={!electron && PREVIEW_ONLY_CODE_PANELS.has("tasks")}
          onClick={() => onPanelClick("tasks")}
        />
        <ActivityItem
          icon={<FlaskConical size={20} />}
          active={isActive("testing")}
          title="Testing (⌘⇧T: Test)"
          previewOnly={!electron && PREVIEW_ONLY_CODE_PANELS.has("testing")}
          onClick={() => onPanelClick("testing")}
        />

        <ActivitySeparator />

        <ActivityItem
          icon={<Clock size={20} />}
          active={isActive("timeline")}
          title="Timeline"
          previewOnly={!electron && PREVIEW_ONLY_CODE_PANELS.has("timeline")}
          onClick={() => onPanelClick("timeline")}
        />
        <ActivityItem
          icon={<ListTree size={20} />}
          active={isActive("outline")}
          title="Outline"
          previewOnly={!electron && PREVIEW_ONLY_CODE_PANELS.has("outline")}
          onClick={() => onPanelClick("outline")}
        />
        <ActivityItem
          icon={<Bug size={20} />}
          active={isActive("debug")}
          title="Debug (F5)"
          previewOnly={!electron && PREVIEW_ONLY_CODE_PANELS.has("debug")}
          onClick={() => onPanelClick("debug")}
        />

        <ActivitySeparator />

        <ActivityItem
          icon={<Workflow size={20} />}
          active={isActive("workflow")}
          title="Workflows"
          onClick={() => onPanelClick("workflow")}
        />
        <ActivityItem
          icon={<Flame size={20} />}
          active={isActive("furnace")}
          title="Furnace"
          onClick={() => onPanelClick("furnace")}
        />
      </div>

      <div className="flex shrink-0 flex-col items-center border-t border-gray-200 bg-white py-1 dark:border-gray-800 dark:bg-gray-900">
        <button
          title="AI Chat (⌘J)"
          onClick={onToggleChat}
          className={`flex w-full items-center justify-center border-l-2 py-2 transition-colors ${
            showChatSidebar
              ? "border-blue-500 bg-blue-50 text-blue-700 dark:border-blue-400 dark:bg-blue-500/10 dark:text-white"
              : "border-transparent text-blue-500 hover:bg-blue-50 hover:text-blue-700 dark:text-blue-400 dark:hover:bg-blue-500/5 dark:hover:text-blue-300"
          }`}
        >
          <MessageSquare size={20} />
        </button>
        <ActivityItem
          icon={<Settings size={20} />}
          active={showSettings}
          title="Settings (Cmd+,)"
          onClick={onToggleSettings}
        />
      </div>
    </div>
  );
}

export function DevelopmentStatusBar() {
  const activeFilePath = useCodeStore((state) => state.activeFilePath);
  const openFiles = useCodeStore((state) => state.openFiles);
  const cursorPosition = useCodeStore((state) => state.cursorPosition);
  const currentBranch = useCodeStore((state) => state.currentBranch);
  const activeFile = openFiles.find((file) => file.path === activeFilePath);
  const { errors, warnings } = useProblemsCount();
  const tabSize = useSettingsStore((state) => state.tabSize);

  return (
    <div className="flex h-[22px] shrink-0 items-center justify-between bg-[#007acc] px-2 text-[11px] text-white select-none">
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
            <button className="rounded px-1 hover:bg-white/10">
              Ln {cursorPosition.lineNumber}, Col {cursorPosition.column}
            </button>
            <span>Spaces: {tabSize}</span>
            <span>UTF-8</span>
            <button className="rounded px-1 capitalize hover:bg-white/10">
              {activeFile.language}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export function DevelopmentBottomPanelTabs({
  activeTab,
  onTabChange,
}: {
  activeTab: BottomTab;
  onTabChange: (tab: BottomTab) => void;
}) {
  const tabs: Array<{ id: BottomTab; label: string; icon: ReactNode }> = [
    { id: "terminal", label: "Terminal", icon: <TerminalIcon size={13} /> },
    { id: "problems", label: "Problems", icon: <AlertTriangle size={13} /> },
    { id: "output", label: "Output", icon: <FileText size={13} /> },
    { id: "debugConsole", label: "Debug Console", icon: <Bug size={13} /> },
  ];

  return (
    <div
      data-tour="bottom-panel"
      className="flex shrink-0 items-center border-b border-gray-200 bg-gray-50 dark:border-[#3c3c3c] dark:bg-[#252526]"
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onTabChange(tab.id)}
          className={`flex items-center gap-1.5 border-b px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide transition-colors ${
            activeTab === tab.id
              ? "border-blue-600 text-blue-700 dark:border-white dark:text-white"
              : "border-transparent text-gray-500 hover:text-gray-900 dark:hover:text-gray-300"
          }`}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function DevelopmentSidebarSurface({
  panel,
  electron,
}: {
  panel: DevelopmentSidebarPanel;
  electron: boolean;
}) {
  if (!electron && PREVIEW_ONLY_CODE_PANELS.has(panel)) {
    return <DevelopmentPreviewPanel panel={panel} />;
  }

  switch (panel) {
    case "search":
      return <SearchPanel />;
    case "git":
      return (
        <Suspense fallback={<PanelLoadingState label="source control" />}>
          <GitPanel />
        </Suspense>
      );
    case "extensions":
      return (
        <Suspense fallback={<PanelLoadingState label="extensions" />}>
          <ExtensionsPanel />
        </Suspense>
      );
    case "tasks":
      return (
        <Suspense fallback={<PanelLoadingState label="tasks" />}>
          <TaskRunner />
        </Suspense>
      );
    case "testing":
      return (
        <Suspense fallback={<PanelLoadingState label="tests" />}>
          <TestExplorer />
        </Suspense>
      );
    case "timeline":
      return (
        <Suspense fallback={<PanelLoadingState label="timeline" />}>
          <DevelopmentTimelinePanel />
        </Suspense>
      );
    case "outline":
      return (
        <Suspense fallback={<PanelLoadingState label="outline" />}>
          <OutlineView />
        </Suspense>
      );
    case "debug":
      return (
        <Suspense fallback={<PanelLoadingState label="debug tools" />}>
          <DebugPanel />
        </Suspense>
      );
    case "workflow":
      return <WorkflowSidebarPanel />;
    case "furnace":
      return <FurnaceSidebarPanel />;
    default:
      return <FileExplorer />;
  }
}

export function DevelopmentBottomPanelSurface({
  activeTab,
}: {
  activeTab: BottomTab;
}) {
  if (activeTab === "terminal") {
    return <TerminalPanel />;
  }
  if (activeTab === "problems") {
    return <ProblemsPanel />;
  }
  if (activeTab === "output") {
    return <OutputPanel />;
  }

  return (
    <Suspense fallback={<PanelLoadingState label="debug console" />}>
      <DebugConsole />
    </Suspense>
  );
}

export { DevelopmentModeStatusStrip };
