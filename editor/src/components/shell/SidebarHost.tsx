/**
 * SidebarHost: generic left sidebar that renders mode-specific content.
 * Provides workspace header, search/filter, quick access, and delegates main content to mode.
 */
import { useState } from "react";
import {
  PanelLeftOpen,
  PanelLeftClose,
  Search,
  Clock,
  PlayCircle,
  Pin,
} from "lucide-react";
import { useAppStore } from "../../store/useAppStore";
import { WORKSPACE_COLORS } from "../../store/useWorkspaceStore";

interface SidebarHostProps {
  mode: string;
  workspaceName?: string;
  workspaceColor?: string;
}

function resolveWorkspaceColor(color?: string): string | undefined {
  if (!color) return undefined;
  const found = WORKSPACE_COLORS.find((c) => c.id === color);
  return found?.hex ?? (color.startsWith("#") ? color : undefined);
}

export default function SidebarHost({
  mode,
  workspaceName,
  workspaceColor,
}: SidebarHostProps) {
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const collapsed = !sidebarOpen;
  const [searchQuery, setSearchQuery] = useState("");
  const resolvedColor = resolveWorkspaceColor(workspaceColor);

  if (collapsed) {
    return (
      <div className="w-10 border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-[#1e1e1e] flex flex-col items-center pt-2">
        <button
          onClick={toggleSidebar}
          className="p-1.5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-800"
          title="Expand sidebar (Cmd+B)"
        >
          <PanelLeftOpen size={16} />
        </button>
      </div>
    );
  }

  return (
    <div className="w-64 border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-[#1e1e1e] flex flex-col overflow-hidden">
      {/* Workspace Header (3-2) */}
      <div className="px-3 py-2 border-b border-gray-200 dark:border-gray-800">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            {resolvedColor && (
              <div
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: resolvedColor }}
              />
            )}
            <span className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">
              {workspaceName ?? "Workspace"}
            </span>
          </div>
          <button
            onClick={toggleSidebar}
            className="p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-800"
          >
            <PanelLeftClose size={14} />
          </button>
        </div>
      </div>

      {/* Search / Filter (3-5) */}
      <div className="px-3 py-1.5 border-b border-gray-200 dark:border-gray-800">
        <div className="relative">
          <Search
            size={12}
            className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search workspace..."
            className="w-full h-[26px] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded pl-7 pr-2 text-[11px] text-gray-800 dark:text-gray-200 placeholder:text-gray-400 focus:border-blue-500/60 focus:outline-none"
          />
        </div>
      </div>

      {/* Quick Access (3-3) */}
      <div className="px-3 py-2 border-b border-gray-200 dark:border-gray-800">
        <h4 className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">
          Quick Access
        </h4>
        <div className="space-y-0.5">
          <QuickAccessItem icon={<Clock size={12} />} label="Recent Files" />
          <QuickAccessItem icon={<PlayCircle size={12} />} label="Recent Runs" />
          <QuickAccessItem icon={<Pin size={12} />} label="Pinned Items" />
        </div>
      </div>

      {/* Mode-specific content (3-1) */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <SidebarContent mode={mode} searchQuery={searchQuery} />
      </div>
    </div>
  );
}

function QuickAccessItem({
  icon,
  label,
}: {
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button className="w-full flex items-center gap-2 px-2 py-1 text-[11px] text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-800 rounded">
      {icon}
      {label}
    </button>
  );
}

function SidebarContent({
  mode,
  searchQuery,
}: {
  mode: string;
  searchQuery: string;
}) {
  const filterHint = searchQuery.trim() ? (
    <p className="px-3 py-1 text-[10px] text-gray-400 truncate">
      Filter: &quot;{searchQuery}&quot;
    </p>
  ) : null;

  switch (mode) {
    case "chat":
      return (
        <>
          {filterHint}
          <p className="px-3 py-2 text-xs text-gray-500">
            Thread list is rendered by ChatPanel
          </p>
        </>
      );
    case "development":
      return (
        <>
          {filterHint}
          <p className="px-3 py-2 text-xs text-gray-500">
            File explorer is rendered by CodeMode
          </p>
        </>
      );
    case "operations":
      return (
        <>
          {filterHint}
          <p className="px-3 py-2 text-xs text-gray-500">
            Workflow list appears here
          </p>
        </>
      );
    default:
      return (
        <>
          {filterHint}
          <p className="px-3 py-2 text-xs text-gray-500">
            No sidebar content for this mode
          </p>
        </>
      );
  }
}
