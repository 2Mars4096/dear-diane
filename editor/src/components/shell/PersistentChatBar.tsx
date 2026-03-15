/**
 * PersistentChatBar: always-visible chat input at the bottom of every mode
 * except Chat (which has its own input in ChatPanel).
 * Cmd+J toggles expansion. Sends route to chat mode on submit.
 */
import { useState, useRef, useEffect } from "react";
import { Send, X } from "lucide-react";
import { useAppStore } from "../../store/useAppStore";

export default function PersistentChatBar() {
  const activeMode = useAppStore((s) => s.activeMode);
  const chatBarExpanded = useAppStore((s) => s.chatBarExpanded);
  const setChatBarExpanded = useAppStore((s) => s.setChatBarExpanded);
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    if (!input.trim()) return;
    const msg = input;
    setInput("");
    setChatBarExpanded(false);
    const store = useAppStore.getState();
    store.setPendingChatMessage(msg);
    store.setMode("chat");
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        const next = !useAppStore.getState().chatBarExpanded;
        setChatBarExpanded(next);
        if (next) {
          requestAnimationFrame(() => inputRef.current?.focus());
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [setChatBarExpanded]);

  useEffect(() => {
    const ta = inputRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height =
      Math.min(ta.scrollHeight, chatBarExpanded ? 120 : 36) + "px";
  }, [input, chatBarExpanded]);

  if (activeMode === "chat") return null;

  return (
    <div
      className={`border-t border-gray-200 bg-white transition-all ${chatBarExpanded ? "pb-1" : ""}`}
    >
      <div className="max-w-3xl mx-auto px-4 py-2">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            onFocus={() => setChatBarExpanded(true)}
            placeholder="Ask DAN anything… (⌘J to expand)"
            className="flex-1 resize-none bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-indigo-300 focus:shadow-sm transition-all min-h-[36px] max-h-[120px]"
            rows={1}
            spellCheck={false}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim()}
            className="p-2 rounded-lg bg-indigo-600 text-white disabled:opacity-40 hover:bg-indigo-500 transition-colors flex-shrink-0"
          >
            <Send size={16} />
          </button>
        </div>
        {chatBarExpanded && (
          <div className="flex items-center justify-end mt-1 text-[10px] text-gray-400">
            <div className="flex items-center gap-2">
              <span>Enter to send · Shift+Enter for newline</span>
              <button
                onClick={() => setChatBarExpanded(false)}
                className="hover:text-gray-600 transition-colors"
                title="Collapse"
              >
                <X size={12} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
