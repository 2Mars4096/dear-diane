/**
 * ModeChatSidebar: reusable AI chat sidebar for any mode.
 * Persists chat history per workspace + mode in localStorage.
 * Each mode provides its own context via the `contextProvider` prop.
 */
import {
  useState,
  useRef,
  useEffect,
  useCallback,
  type KeyboardEvent,
  type ChangeEvent,
} from "react";
import { X, Trash2, Send, Loader2, Sparkles, Maximize2 } from "lucide-react";
import { Marked, Renderer } from "marked";
import DOMPurify from "dompurify";
import hljs from "../../lib/hljs";
import { useAppStore, type AppMode } from "../../store/useAppStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import { nativeTerminal } from "../../lib/electronBridge";
import { useCodeStore } from "../../store/useCodeStore";

/* ------------------------------------------------------------------ */
/*  Chat sender registry (per-mode)                                    */
/* ------------------------------------------------------------------ */

const chatSenders = new Map<AppMode, (text: string) => void>();

export function registerModeChatSender(mode: AppMode, fn: (text: string) => void) {
  chatSenders.set(mode, fn);
}

export function sendToModeChat(mode: AppMode, text: string) {
  chatSenders.get(mode)?.(text);
}

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

export interface ModeChatSidebarProps {
  mode: AppMode;
  onClose: () => void;
  contextProvider?: () => string;
}

/* ------------------------------------------------------------------ */
/*  Persistence                                                        */
/* ------------------------------------------------------------------ */

function chatStorageKey(workspaceId: string, mode: AppMode): string {
  return `dan-chat-${workspaceId}-${mode}`;
}

function loadChatHistory(workspaceId: string | null, mode: AppMode): ChatMessage[] {
  if (!workspaceId) return [];
  try {
    const raw = localStorage.getItem(chatStorageKey(workspaceId, mode));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

const MAX_PERSISTED_MESSAGES = 200;

function saveChatHistory(workspaceId: string | null, mode: AppMode, messages: ChatMessage[]) {
  if (!workspaceId) return;
  try {
    localStorage.setItem(chatStorageKey(workspaceId, mode), JSON.stringify(messages.slice(-MAX_PERSISTED_MESSAGES)));
  } catch { /* quota */ }
}

/* ------------------------------------------------------------------ */
/*  Markdown renderer                                                  */
/* ------------------------------------------------------------------ */

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const SHELL_LANGS = new Set(["sh", "bash", "shell", "zsh", "terminal", "console"]);

const markedInstance = (() => {
  const renderer = new Renderer();

  renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
    let highlighted: string;
    try {
      highlighted =
        lang && hljs.getLanguage(lang)
          ? hljs.highlight(text, { language: lang }).value
          : hljs.highlightAuto(text).value;
    } catch {
      highlighted = escapeHtml(text);
    }
    const langLabel = lang
      ? `<span class="text-[10px] text-gray-500 font-sans">${escapeHtml(lang)}</span>`
      : `<span></span>`;
    const encoded = btoa(encodeURIComponent(text));
    const isShell = SHELL_LANGS.has(lang?.toLowerCase() ?? "");
    const copySvg = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
    const runBtn = isShell
      ? `<button data-action="run" class="text-green-400 hover:text-green-300 px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors" title="Run in terminal">\u25B6 Run</button>`
      : "";
    const buttons =
      `<div class="flex items-center gap-1">` +
      `<button data-action="copy" class="text-gray-500 hover:text-gray-300 p-0.5 rounded transition-colors" title="Copy">${copySvg}</button>` +
      runBtn +
      `</div>`;
    return (
      `<div class="my-1.5 rounded-md overflow-hidden border border-gray-700/50" data-code="${encoded}">` +
      `<div class="flex items-center justify-between px-2 py-1 bg-gray-900 border-b border-gray-700/50">${langLabel}${buttons}</div>` +
      `<pre class="bg-gray-950 text-gray-100 p-2 overflow-x-auto text-[11px] leading-relaxed font-mono m-0"><code>${highlighted}</code></pre>` +
      `</div>`
    );
  };

  renderer.codespan = ({ text }: { text: string }) =>
    `<code class="bg-gray-950 text-gray-200 px-1 py-0.5 rounded text-[11px] font-mono">${text}</code>`;

  const m = new Marked({ renderer, async: false });
  return m;
})();

function renderMarkdown(src: string): string {
  try {
    const raw = markedInstance.parse(src) as string;
    return DOMPurify.sanitize(raw, {
      ADD_ATTR: ["data-action", "data-code"],
    });
  } catch {
    return escapeHtml(src);
  }
}

/* ------------------------------------------------------------------ */
/*  API helper                                                         */
/* ------------------------------------------------------------------ */

const API_BASE = "/api";

async function sendChatRequest(
  message: string,
  threadId?: string,
): Promise<Response> {
  return fetch(`${API_BASE}/chat/editor/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      mode: "auto",
      thread_id: threadId ?? null,
    }),
  });
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function ModeChatSidebar({ mode, onClose, contextProvider }: ModeChatSidebarProps) {
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    loadChatHistory(workspaceId, mode),
  );
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const threadIdRef = useRef<string | undefined>(undefined);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (messages.length === 0) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      saveChatHistory(workspaceId, mode, messages);
      saveTimerRef.current = null;
    }, 500);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [messages, workspaceId, mode]);

  // Flush any pending debounced save when the sidebar unmounts
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
        const wsId = useWorkspaceStore.getState().activeWorkspaceId;
        if (wsId && messagesRef.current.length > 0) {
          saveChatHistory(wsId, mode, messagesRef.current);
        }
        saveTimerRef.current = null;
      }
    };
  }, [mode]);

  useEffect(() => {
    setMessages(loadChatHistory(workspaceId, mode));
    threadIdRef.current = undefined;
    setStreaming(false);
  }, [workspaceId, mode]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const handleInputChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      setInput(e.target.value);
      const ta = e.target;
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 96)}px`;
    },
    [],
  );

  /* ------ Code block click handler ----------------------------------- */

  const handleRunInTerminal = useCallback(async (command: string) => {
    const { pinnedRoots, setShowTerminal, addTerminal, setActiveTerminal } =
      useCodeStore.getState();
    setShowTerminal(true);
    const cwd = pinnedRoots[0] || undefined;
    const id = await nativeTerminal.create({ shell: "/bin/zsh", cwd });
    if (id) {
      const shortCmd = command.length > 40 ? command.slice(0, 37) + "..." : command;
      addTerminal(id, `\u26A1 ${shortCmd}`);
      setActiveTerminal(id);
      setTimeout(() => nativeTerminal.write(id, command + "\n"), 300);
    }
  }, []);

  const handleMessageClick = useCallback(
    async (e: React.MouseEvent<HTMLDivElement>) => {
      const btn = (e.target as HTMLElement).closest("[data-action]") as HTMLElement | null;
      if (!btn) return;

      const action = btn.dataset.action;
      const codeBlock = btn.closest("[data-code]") as HTMLElement | null;
      const encoded = codeBlock?.dataset.code;
      if (!encoded) return;

      let code: string;
      try {
        code = decodeURIComponent(atob(encoded));
      } catch {
        return;
      }

      if (action === "copy") {
        navigator.clipboard.writeText(code);
        const prev = btn.innerHTML;
        btn.textContent = "\u2713";
        btn.classList.add("text-green-400");
        setTimeout(() => {
          btn.innerHTML = prev;
          btn.classList.remove("text-green-400");
        }, 1500);
      } else if (action === "run") {
        await handleRunInTerminal(code);
      }
    },
    [handleRunInTerminal],
  );

  /* ------ Send message ------------------------------------------------ */

  const doSend = useCallback(
    async (text: string) => {
      if (!text.trim() || streaming) return;

      const ctx = contextProvider?.() ?? "";
      const fullMessage = ctx ? `${ctx}\n\n${text.trim()}` : text.trim();

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: text.trim(),
        timestamp: Date.now(),
      };

      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setInput("");
      setStreaming(true);

      if (textareaRef.current) textareaRef.current.style.height = "auto";

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const resp = await sendChatRequest(fullMessage, threadIdRef.current);
        if (!resp.ok) {
          const errText = await resp.text().catch(() => "Unknown error");
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id
                ? { ...m, content: `Failed to connect: ${errText}` }
                : m,
            ),
          );
          setStreaming(false);
          return;
        }

        const data = await resp.json();
        if (data.thread_id) threadIdRef.current = data.thread_id;
        const channelId: string | undefined = data.stream_channel_id;

        if (channelId) {
          const proto = location.protocol === "https:" ? "wss:" : "ws:";
          const ws = new WebSocket(
            `${proto}//${location.host}/api/chat/${channelId}/events`,
          );

          ws.onmessage = (ev) => {
            if (controller.signal.aborted) { ws.close(); return; }
            try {
              const evt = JSON.parse(ev.data);
              if (evt.type === "token" && typeof evt.token === "string") {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsg.id ? { ...m, content: m.content + evt.token } : m,
                  ),
                );
              } else if (evt.type === "content" && typeof evt.content === "string") {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsg.id ? { ...m, content: m.content + evt.content } : m,
                  ),
                );
              } else if (evt.type === "done" || evt.type === "end") {
                ws.close();
              }
            } catch { /* ignore */ }
          };

          ws.onclose = () => {
            setStreaming(false);
            abortRef.current = null;
          };

          ws.onerror = () => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id && !m.content
                  ? { ...m, content: "Connection lost. Please try again." }
                  : m,
              ),
            );
            setStreaming(false);
            abortRef.current = null;
          };
        } else {
          const responseText =
            typeof data.response === "string"
              ? data.response
              : typeof data.content === "string"
                ? data.content
                : JSON.stringify(data);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id ? { ...m, content: responseText } : m,
            ),
          );
          setStreaming(false);
        }
      } catch {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id && !m.content
              ? { ...m, content: "Failed to connect to DAN server." }
              : m,
          ),
        );
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [contextProvider, streaming],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        doSend(input);
      }
    },
    [doSend, input],
  );

  useEffect(() => {
    registerModeChatSender(mode, doSend);
    return () => { chatSenders.delete(mode); };
  }, [doSend, mode]);

  const handleClear = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    setMessages([]);
    threadIdRef.current = undefined;
    setStreaming(false);
    if (workspaceId) {
      try { localStorage.removeItem(chatStorageKey(workspaceId, mode)); } catch { /* */ }
    }
  }, [workspaceId, mode]);

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div className="h-full w-full flex flex-col bg-gray-900 text-gray-300">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800 shrink-0">
        <span className="text-[11px] font-semibold tracking-widest text-gray-400 uppercase">
          AI Chat
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => { useAppStore.getState().setMode("chat"); onClose(); }}
            title="Open full Chat mode (threads, history, slash commands)"
            className="p-1 text-gray-500 hover:text-blue-400 transition-colors rounded"
          >
            <Maximize2 size={14} />
          </button>
          <button
            onClick={handleClear}
            title="Clear chat"
            className="p-1 text-gray-500 hover:text-gray-300 transition-colors rounded"
          >
            <Trash2 size={14} />
          </button>
          <button
            onClick={onClose}
            title="Close chat (⌘J)"
            className="p-1 text-gray-500 hover:text-gray-300 transition-colors rounded"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      <div ref={scrollRef} onClick={handleMessageClick} className="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-3">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-600 text-xs gap-2 select-none">
            <Sparkles size={24} className="text-gray-700" />
            <span>Ask anything about your code</span>
            <button
              onClick={() => { useAppStore.getState().setMode("chat"); onClose(); }}
              className="mt-1 text-[10px] text-gray-500 hover:text-blue-400 transition-colors"
            >
              Open full Chat for threads, / commands &amp; history
            </button>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}
          >
            <div
              className={
                msg.role === "user"
                  ? "bg-blue-600 text-white rounded-lg px-3 py-2 max-w-[85%] text-[13px] leading-relaxed"
                  : "bg-gray-800 text-gray-200 rounded-lg px-3 py-2 max-w-[85%] text-[13px] leading-relaxed"
              }
            >
              {msg.role === "user" ? (
                <span className="whitespace-pre-wrap break-words">{msg.content}</span>
              ) : msg.content ? (
                <div
                  className="break-words [&>p]:my-1 [&>ul]:my-1 [&>ol]:my-1 [&>p:first-child]:mt-0 [&>p:last-child]:mb-0"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />
              ) : streaming ? (
                <span className="inline-flex items-center gap-1.5 text-gray-400">
                  <Loader2 size={12} className="animate-spin" />
                  Thinking…
                </span>
              ) : null}
            </div>
            <span className="text-[10px] text-gray-600 mt-0.5 px-1">
              {formatTime(msg.timestamp)}
            </span>
          </div>
        ))}
      </div>

      <div className="px-3 py-2 border-t border-gray-800 shrink-0">
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything…"
            rows={1}
            disabled={streaming}
            className="flex-1 bg-gray-800 border border-gray-700 text-white rounded-lg px-3 py-2 text-[13px] resize-none placeholder-gray-500 focus:outline-none focus:border-gray-600 disabled:opacity-50"
            style={{ maxHeight: 96 }}
          />
          <button
            onClick={() => doSend(input)}
            disabled={!input.trim() || streaming}
            className="p-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg transition-colors shrink-0"
          >
            {streaming ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Send size={16} />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
