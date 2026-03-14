/**
 * Breadcrumb: shows active mode > workspace for context awareness.
 * Kept thin (20-22px) to minimize vertical space.
 */
import { ChevronRight } from "lucide-react";
import { useAppStore } from "../../store/useAppStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";

const MODE_LABELS: Record<string, string> = {
  chat: "Chat",
  development: "Code",
  operations: "Operations",
  research: "Research",
  analytics: "Analytics",
  content: "Content",
};

export default function Breadcrumb() {
  const activeMode = useAppStore((s) => s.activeMode);
  const workspace = useWorkspaceStore((s) => s.getActiveWorkspace());

  const label = MODE_LABELS[activeMode] ?? activeMode;

  return (
    <div className="flex items-center gap-1.5 px-4 py-1 text-[11px] text-gray-500 dark:text-gray-500 bg-gray-50 dark:bg-[#1a1a1a] border-b border-gray-200 dark:border-gray-800 select-none h-[22px] flex-shrink-0">
      <span className="text-gray-400 dark:text-gray-600">{label}</span>
      {workspace && (
        <>
          <ChevronRight size={10} className="text-gray-400 dark:text-gray-700 flex-shrink-0" />
          <span className="text-gray-500 dark:text-gray-400 truncate">{workspace.name}</span>
        </>
      )}
    </div>
  );
}
