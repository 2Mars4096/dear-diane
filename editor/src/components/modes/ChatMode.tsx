/**
 * Chat mode: full-screen conversational interface.
 * Uses a single in-mode header (inside ChatPanel) to reduce stacked chrome.
 */
import { useMemo } from "react";
import ChatPanel from "../ChatPanel";
import { useThreadCloseShortcut } from "../shell/ThreadTabs";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";

export default function ChatMode() {
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  useThreadCloseShortcut();

  const containerClass = useMemo(
    () => "h-full flex flex-col overflow-hidden bg-[#1e1e1e]",
    [],
  );

  return (
    <div className={containerClass}>
      <div className="flex-1 min-h-0">
        <ChatPanel fullScreen workspaceId={workspaceId ?? undefined} />
      </div>
    </div>
  );
}
