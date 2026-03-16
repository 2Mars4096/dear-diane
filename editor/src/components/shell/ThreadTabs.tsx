import { useEffect, useRef } from "react";
import { Plus, X, MessageSquare, History } from "lucide-react";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import { useGraphStore } from "../../store/useGraphStore";

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

  return (
    <div className="flex items-center bg-[#252526] border-b border-[#3c3c3c] h-[32px] flex-shrink-0 px-1.5 gap-1">
      <button
        onClick={() => window.dispatchEvent(new CustomEvent("chat:toggleHistory"))}
        title="Chat history (⌘⇧L)"
        className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded transition-colors flex-shrink-0"
      >
        <History size={12} />
      </button>
      <div className="w-px h-3.5 bg-gray-700/50 flex-shrink-0" />
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
                  ? "bg-white/10 text-gray-200 shadow-sm"
                  : "text-gray-500 hover:text-gray-300 hover:bg-white/5"
                }
              `}
            >
              <MessageSquare size={10} className="flex-shrink-0 opacity-50" />
              <span className="truncate">{title}</span>
              <span
                role="button"
                tabIndex={-1}
                onClick={(e) => { e.stopPropagation(); onCloseThread(tid); }}
                className="ml-0.5 p-0.5 rounded opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-white/10 transition-opacity"
              >
                <X size={10} />
              </span>
            </button>
          );
        })}
      </div>
      <button
        onClick={onNewThread}
        title="New Thread (⌘N)"
        className="flex items-center justify-center w-5 h-5 text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded transition-colors flex-shrink-0"
      >
        <Plus size={12} />
      </button>
    </div>
  );
}

export function useThreadCloseShortcut() {
  const activeWs = useWorkspaceStore((s) => s.getActiveWorkspace());
  const closeThread = useWorkspaceStore((s) => s.closeThread);
  const openThread = useWorkspaceStore((s) => s.openThread);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "w" && !e.shiftKey && !e.altKey) {
        if (!activeWs?.activeThreadId) return;
        e.preventDefault();
        const closedId = activeWs.activeThreadId;
        closeThread(closedId);
        useGraphStore.getState().addToast({
          type: "info",
          message: "Thread closed",
          durationMs: 5000,
          action: { label: "Undo", onClick: () => openThread(closedId) },
        });
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [activeWs?.activeThreadId, closeThread, openThread]);
}
