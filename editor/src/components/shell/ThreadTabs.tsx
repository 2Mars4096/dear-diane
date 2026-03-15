import { useEffect, useRef } from "react";
import { Plus, X, MessageSquare } from "lucide-react";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";

export interface ThreadTabInfo {
  id: string;
  title: string;
}

interface ThreadTabsProps {
  threadTitles: Map<string, string>;
  onSelectThread: (threadId: string) => void;
  onCloseThread: (threadId: string) => void;
  onNewThread: () => void;
}

export default function ThreadTabs({
  threadTitles,
  onSelectThread,
  onCloseThread,
  onNewThread,
}: ThreadTabsProps) {
  const activeWs = useWorkspaceStore((s) => s.getActiveWorkspace());
  const openThreadIds = activeWs?.openThreadIds ?? [];
  const activeThreadId = activeWs?.activeThreadId ?? null;

  const scrollRef = useRef<HTMLDivElement>(null);

  if (openThreadIds.length === 0) return null;

  return (
    <div className="flex items-center bg-gray-100 border-b border-gray-200 h-[28px] flex-shrink-0 px-1 gap-0.5">
      <div
        ref={scrollRef}
        className="flex items-center gap-0.5 overflow-x-auto flex-1 min-w-0 scrollbar-none"
      >
        {openThreadIds.map((tid) => {
          const title = threadTitles.get(tid) || "Untitled";
          const isActive = tid === activeThreadId;
          return (
            <button
              key={tid}
              onClick={() => onSelectThread(tid)}
              onMouseDown={(e) => {
                if (e.button === 1) {
                  e.preventDefault();
                  onCloseThread(tid);
                }
              }}
              className={`
                group flex items-center gap-1 px-2 h-[22px] rounded text-[11px] font-medium
                whitespace-nowrap max-w-[160px] transition-colors
                ${isActive
                  ? "bg-white text-gray-900 shadow-sm"
                  : "text-gray-500 hover:text-gray-700 hover:bg-gray-200/60"
                }
              `}
            >
              <MessageSquare size={10} className="flex-shrink-0 opacity-50" />
              <span className="truncate">{title}</span>
              <span
                role="button"
                tabIndex={-1}
                onClick={(e) => { e.stopPropagation(); onCloseThread(tid); }}
                className="ml-0.5 p-0.5 rounded opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-gray-300/60 transition-opacity"
              >
                <X size={10} />
              </span>
            </button>
          );
        })}
      </div>
      <button
        onClick={onNewThread}
        title="New Thread"
        className="flex items-center justify-center w-5 h-5 text-gray-400 hover:text-gray-600 hover:bg-gray-200/60 rounded transition-colors flex-shrink-0"
      >
        <Plus size={12} />
      </button>
    </div>
  );
}

export function useThreadCloseShortcut() {
  const activeWs = useWorkspaceStore((s) => s.getActiveWorkspace());
  const closeThread = useWorkspaceStore((s) => s.closeThread);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "w" && !e.shiftKey && !e.altKey) {
        if (!activeWs?.activeThreadId) return;
        e.preventDefault();
        closeThread(activeWs.activeThreadId);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [activeWs?.activeThreadId, closeThread]);
}
