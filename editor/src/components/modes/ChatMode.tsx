/**
 * Chat mode: full-screen conversational interface.
 * Wraps the existing ChatPanel in a full-width layout with workspace-scoped
 * thread tabs synced via the workspace store.
 */
import { useState, useCallback } from "react";
import ChatPanel from "../ChatPanel";
import ThreadTabs, { useThreadCloseShortcut } from "../shell/ThreadTabs";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";

export default function ChatMode() {
  const activeWorkspace = useWorkspaceStore((s) => s.getActiveWorkspace());
  const openThread = useWorkspaceStore((s) => s.openThread);
  const closeThread = useWorkspaceStore((s) => s.closeThread);
  const setActiveThread = useWorkspaceStore((s) => s.setActiveThread);

  const [threadTitles, setThreadTitles] = useState<Map<string, string>>(
    () => new Map(),
  );

  const handleThreadOpen = useCallback(
    (threadId: string, threadTitle?: string) => {
      openThread(threadId);
      if (threadTitle) {
        setThreadTitles((prev) => {
          const next = new Map(prev);
          next.set(threadId, threadTitle);
          return next;
        });
      }
    },
    [openThread],
  );

  const handleThreadTitleUpdate = useCallback(
    (threadId: string, title: string) => {
      setThreadTitles((prev) => {
        const next = new Map(prev);
        next.set(threadId, title);
        return next;
      });
    },
    [],
  );

  const handleSelectThread = useCallback(
    (threadId: string) => {
      setActiveThread(threadId);
      window.dispatchEvent(
        new CustomEvent("workspace:selectThread", { detail: { threadId } }),
      );
    },
    [setActiveThread],
  );

  const handleCloseThread = useCallback(
    (threadId: string) => {
      closeThread(threadId);
    },
    [closeThread],
  );

  const handleNewThread = useCallback(() => {
    window.dispatchEvent(new CustomEvent("workspace:newThread"));
  }, []);

  useThreadCloseShortcut();

  return (
    <div className="absolute inset-0 overflow-hidden flex flex-col bg-white">
      <ThreadTabs
        threadTitles={threadTitles}
        onSelectThread={handleSelectThread}
        onCloseThread={handleCloseThread}
        onNewThread={handleNewThread}
      />
      <div className="flex-1 min-h-0 relative">
        <ChatPanel
          fullScreen
          workspaceId={activeWorkspace?.id}
          onThreadOpen={handleThreadOpen}
          onThreadTitleUpdate={handleThreadTitleUpdate}
        />
      </div>
    </div>
  );
}
