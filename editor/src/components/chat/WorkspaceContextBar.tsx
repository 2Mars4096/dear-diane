import { Loader2, Eye } from "lucide-react";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";

interface ActiveTask {
  title: string;
  onView?: () => void;
}

interface Props {
  activeTask?: ActiveTask | null;
}

export default function WorkspaceContextBar({ activeTask }: Props) {
  const workspace = useWorkspaceStore((s) => s.getActiveWorkspace());

  if (!workspace) return null;

  const pinned = workspace.pinnedPaths;
  const displayPaths = pinned.slice(0, 2).map((p) => {
    const parts = p.split("/");
    return parts.length > 2
      ? `…/${parts.slice(-2).join("/")}`
      : p;
  });
  const extraCount = Math.max(0, pinned.length - 2);

  return (
    <div className="flex h-7 items-center gap-3 border-b border-[#1e1e1e] bg-[#252526] px-3 text-[11px] text-gray-400 shrink-0 select-none">
      <span className="font-semibold text-gray-200 truncate max-w-[140px]">
        {workspace.name}
      </span>

      {displayPaths.length > 0 && (
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-gray-600">|</span>
          {displayPaths.map((p, i) => (
            <span key={i} className="truncate max-w-[120px] text-gray-500">
              {p}
            </span>
          ))}
          {extraCount > 0 && (
            <span className="text-gray-600 whitespace-nowrap">+{extraCount} more</span>
          )}
        </div>
      )}

      {activeTask && (
        <div className="ml-auto flex items-center gap-1.5 min-w-0">
          <Loader2 size={11} className="animate-spin text-blue-400 shrink-0" />
          <span className="truncate max-w-[160px] text-blue-300">{activeTask.title}</span>
          {activeTask.onView && (
            <button
              onClick={activeTask.onView}
              className="flex items-center gap-0.5 text-blue-400 hover:text-blue-300 transition-colors whitespace-nowrap"
            >
              <Eye size={10} />
              View
            </button>
          )}
        </div>
      )}
    </div>
  );
}
