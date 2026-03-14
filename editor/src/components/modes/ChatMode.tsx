/**
 * Chat mode: full-screen conversational interface.
 * Wraps the existing ChatPanel in a full-width layout with a thread
 * list sidebar. This is the default mode — as simple as ChatGPT/Claude.
 */
import ChatPanel from "../ChatPanel";

export default function ChatMode() {
  return (
    <div className="flex-1 min-h-0 w-full overflow-hidden flex flex-col bg-white">
      <ChatPanel fullScreen />
    </div>
  );
}
