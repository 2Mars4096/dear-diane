/**
 * Chat mode: full-screen conversational interface.
 * Wraps the existing ChatPanel in a full-width layout with a thread
 * list sidebar. This is the default mode — as simple as ChatGPT/Claude.
 */
import ChatPanel from "../ChatPanel";

export default function ChatMode() {
  return (
    <div className="flex h-full bg-white">
      <div className="flex-1 min-w-0">
        <ChatPanel fullScreen />
      </div>
    </div>
  );
}
