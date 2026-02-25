import {
  useState,
  useRef,
  useEffect,
  useCallback,
  type KeyboardEvent,
} from "react";
import {
  Send,
  X,
  MessageSquare,
  RotateCcw,
  Plus,
  ArrowLeft,
  Trash2,
  History,
  Loader2,
} from "lucide-react";
import { useGraphStore } from "../store/useGraphStore";
import type { ChatMessage, ChatStreamEvent } from "../types/chat";
import type { ChatThreadSummary } from "../lib/api";
import * as api from "../lib/api";
import ChatMessageBubble from "./ChatMessage";
import MentionAutocomplete from "./MentionAutocomplete";
import {
  findMentionQuery,
  insertMention,
  type MentionRef,
} from "../lib/mentionParser";

const DEFAULT_WIDTH = 380;
const MIN_WIDTH = 280;
const MAX_WIDTH = 640;

const EXAMPLE_PROMPTS = [
  "Add a reviewer node after the writer",
  "Connect the output of Planner to Drafter",
  "Create a 3-node research pipeline",
];

// ---------------------------------------------------------------------------
// Backend ↔ frontend message conversion
// ---------------------------------------------------------------------------

function toBackendMessage(m: ChatMessage): Record<string, unknown> {
  return {
    id: m.id,
    role: m.role,
    content: m.content,
    timestamp: new Date(m.timestamp).toISOString(),
    token_usage: m.tokenUsage
      ? { prompt: m.tokenUsage.prompt, completion: m.tokenUsage.completion }
      : null,
    mutation_plan: m.mutationPlan ?? null,
    mutation_id: m.mutationId ?? null,
    mutation_status: m.mutationStatus ?? null,
    run_ref: m.runRef
      ? {
          run_id: m.runRef.runId,
          scope: m.runRef.scope,
          status: m.runRef.status,
        }
      : null,
    mentions: m.mentions ?? [],
  };
}

function fromBackendMessage(m: Record<string, unknown>): ChatMessage {
  const tu = m.token_usage as Record<string, number> | null;
  const rr = m.run_ref as Record<string, string> | null;
  return {
    id: m.id as string,
    role: m.role as ChatMessage["role"],
    content: m.content as string,
    timestamp: new Date(m.timestamp as string).getTime(),
    tokenUsage: tu ? { prompt: tu.prompt, completion: tu.completion } : null,
    mutationPlan: m.mutation_plan ?? null,
    mutationId: (m.mutation_id as string) ?? null,
    mutationStatus:
      (m.mutation_status as ChatMessage["mutationStatus"]) ?? null,
    runRef: rr
      ? { runId: rr.run_id, scope: rr.scope, status: rr.status }
      : null,
    mentions: (m.mentions as ChatMessage["mentions"]) ?? [],
  };
}

function relativeTimeShort(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ChatPanel() {
  const graphId = useGraphStore((s) => s.graphId);

  const [chatOpen, setChatOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [inputText, setInputText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH);

  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionAnchor, setMentionAnchor] = useState<{
    top: number;
    left: number;
  } | null>(null);

  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [showThreadList, setShowThreadList] = useState(true);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [threadTitle, setThreadTitle] = useState("");
  const [sessionMarkers, setSessionMarkers] = useState<
    Record<string, { historyCursor: number }>
  >({});

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const dragging = useRef(false);
  const activeThreadIdRef = useRef<string | null>(null);
  const prevGraphIdRef = useRef<string | null>(null);

  activeThreadIdRef.current = activeThreadId;

  // -------------------------------------------------------------------------
  // Thread persistence helpers
  // -------------------------------------------------------------------------

  const fetchThreads = useCallback(async (wfId: string) => {
    setLoadingThreads(true);
    try {
      const { threads: list } = await api.listChatThreads(wfId);
      const sorted = [...list].sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      );
      setThreads(sorted);
      return sorted;
    } catch (err) {
      console.warn("Failed to fetch threads:", err);
      return [];
    } finally {
      setLoadingThreads(false);
    }
  }, []);

  const loadThread = useCallback(
    async (wfId: string, threadId: string) => {
      try {
        const data = await api.getChatThread(wfId, threadId);
        const backendMsgs = (data.messages ?? []) as Record<string, unknown>[];
        setMessages(backendMsgs.map(fromBackendMessage));
        setActiveThreadId(threadId);
        setThreadTitle((data.title as string) || "");
        setShowThreadList(false);
        setError(null);
      } catch (err) {
        console.warn("Failed to load thread:", err);
      }
    },
    [],
  );

  // -------------------------------------------------------------------------
  // Auto-restore on panel open / workflow switch
  // -------------------------------------------------------------------------

  useEffect(() => {
    if (!chatOpen) return;

    const oldGraphId = prevGraphIdRef.current;
    const oldThreadId = activeThreadIdRef.current;

    if (oldGraphId && oldGraphId !== graphId && oldThreadId) {
      api
        .updateChatThread(oldGraphId, oldThreadId, {
          messages: messages.map(toBackendMessage),
        })
        .catch((err) => console.warn("Failed to save thread on switch:", err));
      setActiveThreadId(null);
      setMessages([]);
      setThreadTitle("");
      setShowThreadList(true);
      setSessionMarkers({});
    }

    prevGraphIdRef.current = graphId;
    if (!graphId) return;

    let cancelled = false;
    (async () => {
      const sorted = await fetchThreads(graphId);
      if (cancelled) return;
      if (sorted.length > 0 && !activeThreadIdRef.current) {
        await loadThread(graphId, sorted[0].id);
      } else if (sorted.length === 0) {
        setShowThreadList(true);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatOpen, graphId]);

  // -------------------------------------------------------------------------
  // Auto-scroll on new messages
  // -------------------------------------------------------------------------

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // -------------------------------------------------------------------------
  // Textarea auto-resize
  // -------------------------------------------------------------------------

  const adjustTextarea = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, []);

  useEffect(adjustTextarea, [inputText, adjustTextarea]);

  // -------------------------------------------------------------------------
  // Mention system
  // -------------------------------------------------------------------------

  const checkMention = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    const result = findMentionQuery(ta.value, ta.selectionStart);
    if (result) {
      setMentionQuery(result.query);
      const rect = ta.getBoundingClientRect();
      setMentionAnchor({ top: rect.bottom, left: rect.left });
    } else {
      setMentionQuery(null);
      setMentionAnchor(null);
    }
  }, []);

  const handleMentionSelect = useCallback(
    (mention: {
      type: "node" | "workflow" | "subgraph";
      id: string;
      name: string;
    }) => {
      const ta = textareaRef.current;
      if (!ta) return;
      const ref: MentionRef = {
        name: mention.name,
        type: mention.type,
        id: mention.id,
      };
      const { newText, newCursorPos } = insertMention(
        ta.value,
        ta.selectionStart,
        ref,
      );
      setInputText(newText);
      setMentionQuery(null);
      setMentionAnchor(null);
      requestAnimationFrame(() => {
        ta.focus();
        ta.setSelectionRange(newCursorPos, newCursorPos);
      });
    },
    [],
  );

  const dismissMention = useCallback(() => {
    setMentionQuery(null);
    setMentionAnchor(null);
  }, []);

  // Clean up WebSocket on unmount
  useEffect(() => {
    return () => wsRef.current?.close();
  }, []);

  // -------------------------------------------------------------------------
  // Width resize via left drag handle
  // -------------------------------------------------------------------------

  const onResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      const startX = e.clientX;
      const startW = panelWidth;

      const onMove = (ev: MouseEvent) => {
        if (!dragging.current) return;
        const delta = startX - ev.clientX;
        setPanelWidth(
          Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startW + delta)),
        );
      };
      const onUp = () => {
        dragging.current = false;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [panelWidth],
  );

  const totalTokens = messages.reduce((sum, m) => {
    if (!m.tokenUsage) return sum;
    return sum + m.tokenUsage.prompt + m.tokenUsage.completion;
  }, 0);

  // -------------------------------------------------------------------------
  // Send message (with thread auto-creation & save-on-complete)
  // -------------------------------------------------------------------------

  const sendMessage = useCallback(
    async (text?: string) => {
      const content = (text ?? inputText).trim();
      if (!content || isStreaming) return;

      let threadId = activeThreadIdRef.current;
      if (!threadId && graphId) {
        try {
          const autoTitle =
            content.length > 50 ? content.slice(0, 50) + "…" : content;
          const data = await api.createChatThread(graphId, autoTitle);
          threadId = (data as Record<string, unknown>).id as string;
          setActiveThreadId(threadId);
          setThreadTitle(
            content.length > 40 ? content.slice(0, 40) + "…" : content,
          );
          setShowThreadList(false);
        } catch (err) {
          console.warn("Failed to create thread:", err);
        }
      }

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content,
        timestamp: Date.now(),
      };

      const assistantId = crypto.randomUUID();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setInputText("");
      setIsStreaming(true);
      setError(null);

      const capturedGraphId = graphId;

      try {
        const res = await fetch("/api/chat/message", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workflow_id: graphId,
            message: content,
            history: messages.map((m) => ({
              role: m.role,
              content: m.content,
            })),
            thread_id: threadId,
          }),
        });

        if (!res.ok) {
          throw new Error(
            res.status === 404
              ? "Chat backend not available"
              : `${res.status}: ${await res.text()}`,
          );
        }

        const resBody = await res.json();

        if (resBody.type === "run_started" || resBody.type === "run_error") {
          const isError = resBody.type === "run_error";
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    content: isError
                      ? `Run failed: ${resBody.error?.detail ?? "Unknown error"}`
                      : `Started ${resBody.scope ?? "full"} run.`,
                    runRef: isError
                      ? null
                      : {
                          runId: resBody.run_id as string,
                          scope: (resBody.scope as string) ?? "full",
                          status: "running",
                        },
                  }
                : m,
            ),
          );
          setIsStreaming(false);
          return;
        }

        const { stream_channel_id } = resBody as {
          message_id: string;
          stream_channel_id: string;
        };

        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(
          `${proto}//${location.host}/api/chat/${stream_channel_id}/events`,
        );
        wsRef.current = ws;

        ws.onmessage = (e) => {
          try {
            const evt: ChatStreamEvent = JSON.parse(e.data);
            if (evt.type === "chat_token") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content:
                          evt.accumulated ?? m.content + (evt.delta ?? ""),
                      }
                    : m,
                ),
              );
            } else if (evt.type === "chat_complete") {
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: evt.content ?? m.content,
                        tokenUsage: evt.token_usage ?? null,
                      }
                    : m,
                );
                const tid = activeThreadIdRef.current;
                if (tid && capturedGraphId) {
                  api
                    .updateChatThread(capturedGraphId, tid, {
                      messages: updated.map(toBackendMessage),
                    })
                    .catch((err: unknown) =>
                      console.warn("Failed to save thread:", err),
                    );
                }
                return updated;
              });
              setIsStreaming(false);
              ws.close();
            } else if (evt.type === "chat_mutation") {
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: evt.content ?? m.content,
                        tokenUsage: evt.token_usage ?? null,
                        mutationPlan: evt.mutation_plan ?? null,
                        mutationStatus: "proposed" as const,
                        mutationId: evt.message_id ?? null,
                      }
                    : m,
                );
                const tid = activeThreadIdRef.current;
                if (tid && capturedGraphId) {
                  api
                    .updateChatThread(capturedGraphId, tid, {
                      messages: updated.map(toBackendMessage),
                    })
                    .catch((err: unknown) =>
                      console.warn("Failed to save thread:", err),
                    );
                }
                return updated;
              });
              setIsStreaming(false);
              ws.close();
            } else if (evt.type === "chat_error") {
              setError(evt.error ?? "Unknown error");
              setIsStreaming(false);
              ws.close();
            }
          } catch {
            /* ignore parse errors */
          }
        };

        ws.onerror = () => {
          setError("WebSocket connection failed");
          setIsStreaming(false);
        };

        ws.onclose = () => setIsStreaming(false);
      } catch (err) {
        const msg =
          err instanceof Error ? err.message : "Failed to send message";
        setError(msg);
        setIsStreaming(false);
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
      }
    },
    [inputText, isStreaming, graphId, messages],
  );

  const retryLast = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    setMessages((prev) => {
      const idx = prev.lastIndexOf(lastUser);
      return prev.slice(0, idx);
    });
    setError(null);
    sendMessage(lastUser.content);
  }, [messages, sendMessage]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (mentionQuery !== null) {
        if (["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(e.key))
          return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    },
    [sendMessage, mentionQuery],
  );

  // -------------------------------------------------------------------------
  // Thread management handlers
  // -------------------------------------------------------------------------

  const handleNewChat = useCallback(() => {
    setActiveThreadId(null);
    setMessages([]);
    setThreadTitle("");
    setShowThreadList(false);
    setError(null);
    setSessionMarkers({});
  }, []);

  const handleBackToList = useCallback(async () => {
    const tid = activeThreadIdRef.current;
    if (tid && graphId && messages.length > 0) {
      try {
        await api.updateChatThread(graphId, tid, {
          messages: messages.map(toBackendMessage),
        });
      } catch (err) {
        console.warn("Failed to save thread:", err);
      }
    }
    setShowThreadList(true);
    if (graphId) fetchThreads(graphId);
  }, [graphId, messages, fetchThreads]);

  const handleSelectThread = useCallback(
    async (threadId: string) => {
      if (!graphId) return;
      setSessionMarkers({});
      await loadThread(graphId, threadId);
    },
    [graphId, loadThread],
  );

  const handleDeleteThread = useCallback(
    async (threadId: string) => {
      if (!graphId) return;
      if (!window.confirm("Delete this conversation?")) return;
      try {
        await api.deleteChatThread(graphId, threadId);
        if (activeThreadId === threadId) {
          setActiveThreadId(null);
          setMessages([]);
          setThreadTitle("");
        }
        await fetchThreads(graphId);
      } catch (err) {
        console.warn("Failed to delete thread:", err);
      }
    },
    [graphId, activeThreadId, fetchThreads],
  );

  const handleTitleSave = useCallback(
    async (newTitle: string) => {
      setEditingTitle(false);
      const trimmed = newTitle.trim();
      setThreadTitle(trimmed);
      if (!graphId || !activeThreadId) return;
      try {
        await api.updateChatThread(graphId, activeThreadId, {
          title: trimmed,
        });
      } catch (err) {
        console.warn("Failed to update title:", err);
      }
    },
    [graphId, activeThreadId],
  );

  const handleRevert = useCallback(
    (messageId: string) => {
      const marker = sessionMarkers[messageId];
      if (!marker) return;
      const store = useGraphStore.getState();
      let cursor = store._history.past.length;
      while (cursor > marker.historyCursor) {
        store.undo();
        cursor--;
      }
      setSessionMarkers((prev) => {
        const next = { ...prev };
        delete next[messageId];
        return next;
      });
    },
    [sessionMarkers],
  );

  /**
   * Future integration point: call this after a chat-originated mutation
   * is applied to record the undo cursor position.
   */
  const _recordMutationMarker = useCallback((messageId: string) => {
    const cursor = useGraphStore.getState()._history.past.length;
    setSessionMarkers((prev) => ({
      ...prev,
      [messageId]: { historyCursor: cursor },
    }));
  }, []);
  void _recordMutationMarker; // suppress unused lint

  // -------------------------------------------------------------------------
  // Collapsed state — just a toggle button
  // -------------------------------------------------------------------------

  if (!chatOpen) {
    return (
      <button
        onClick={() => setChatOpen(true)}
        className="fixed right-4 bottom-16 z-50 bg-indigo-600 hover:bg-indigo-700 text-white rounded-full p-3 shadow-lg transition-colors"
        title="Open Chat"
      >
        <MessageSquare size={20} />
      </button>
    );
  }

  // -------------------------------------------------------------------------
  // Expanded panel
  // -------------------------------------------------------------------------

  return (
    <div
      className="flex flex-shrink-0 h-full border-l border-gray-200 bg-white"
      style={{ width: panelWidth }}
    >
      {/* Resize handle */}
      <div
        onMouseDown={onResizeStart}
        className="w-1 cursor-col-resize hover:bg-indigo-200 active:bg-indigo-300 transition-colors flex-shrink-0"
      />

      <div className="flex flex-col flex-1 min-w-0">
        {showThreadList ? (
          <ThreadListView
            threads={threads}
            loading={loadingThreads}
            onNewChat={handleNewChat}
            onSelectThread={handleSelectThread}
            onDeleteThread={handleDeleteThread}
            onClose={() => setChatOpen(false)}
          />
        ) : (
          <>
            {/* Active chat header */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 flex-shrink-0">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <button
                  onClick={handleBackToList}
                  className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors flex-shrink-0"
                  title="Back to threads"
                >
                  <ArrowLeft size={14} />
                </button>
                {editingTitle ? (
                  <input
                    autoFocus
                    defaultValue={threadTitle}
                    onBlur={(e) => handleTitleSave(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter")
                        handleTitleSave(
                          (e.target as HTMLInputElement).value,
                        );
                      if (e.key === "Escape") setEditingTitle(false);
                    }}
                    className="text-sm font-semibold text-gray-800 bg-gray-50 border border-gray-200 rounded px-1.5 py-0.5 outline-none focus:border-indigo-300 min-w-0 flex-1"
                  />
                ) : (
                  <span
                    onClick={() => setEditingTitle(true)}
                    className="text-sm font-semibold text-gray-800 truncate cursor-pointer hover:text-indigo-600 transition-colors"
                    title="Click to rename"
                  >
                    {threadTitle || "Untitled chat"}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                {totalTokens > 0 && (
                  <span className="text-[10px] text-gray-400 tabular-nums">
                    {totalTokens.toLocaleString()} tok
                  </span>
                )}
                <button
                  onClick={() => setChatOpen(false)}
                  className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors"
                >
                  <X size={14} />
                </button>
              </div>
            </div>

            {/* Messages area */}
            <div className="flex-1 overflow-y-auto px-3 py-3 min-h-0">
              {messages.length === 0 ? (
                <EmptyState onSelect={(t) => sendMessage(t)} />
              ) : (
                <>
                  {messages.map((m) => (
                    <ChatMessageBubble
                      key={m.id}
                      message={m}
                      sessionMarker={sessionMarkers[m.id]}
                      onRevert={() => handleRevert(m.id)}
                    />
                  ))}

                  {isStreaming &&
                    messages[messages.length - 1]?.content === "" && (
                      <StreamingDots />
                    )}

                  {error && (
                    <div className="flex items-start gap-2 mb-3 px-2 py-2 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
                      <span className="flex-1">{error}</span>
                      <button
                        onClick={retryLast}
                        className="flex items-center gap-1 text-red-600 hover:text-red-800 font-medium text-xs flex-shrink-0"
                      >
                        <RotateCcw size={12} />
                        Retry
                      </button>
                    </div>
                  )}
                </>
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input area */}
            <div className="flex-shrink-0 border-t border-gray-200 p-3">
              <div className="flex items-end gap-2 border border-gray-200 rounded-xl px-3 py-2 focus-within:shadow-sm focus-within:border-indigo-300 transition-shadow">
                <textarea
                  ref={textareaRef}
                  value={inputText}
                  onChange={(e) => {
                    setInputText(e.target.value);
                    requestAnimationFrame(checkMention);
                  }}
                  onKeyDown={handleKeyDown}
                  onKeyUp={checkMention}
                  onClick={checkMention}
                  placeholder="Ask about your workflow… (@ to mention)"
                  rows={1}
                  disabled={isStreaming}
                  className="flex-1 resize-none text-sm text-gray-900 placeholder-gray-400 bg-transparent outline-none min-h-[24px] max-h-[160px] leading-snug disabled:opacity-50"
                />
                {mentionQuery !== null && (
                  <MentionAutocomplete
                    query={mentionQuery}
                    anchorRect={mentionAnchor}
                    onSelect={handleMentionSelect}
                    onDismiss={dismissMention}
                  />
                )}
                <button
                  onClick={() => sendMessage()}
                  disabled={!inputText.trim() || isStreaming}
                  className="text-indigo-500 hover:text-indigo-700 disabled:text-gray-300 transition-colors p-0.5 flex-shrink-0"
                >
                  <Send size={16} />
                </button>
              </div>
              <div className="text-[10px] text-gray-400 mt-1 px-1">
                Enter to send · Shift+Enter for newline
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ThreadListView({
  threads,
  loading,
  onNewChat,
  onSelectThread,
  onDeleteThread,
  onClose,
}: {
  threads: ChatThreadSummary[];
  loading: boolean;
  onNewChat: () => void;
  onSelectThread: (id: string) => void;
  onDeleteThread: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 flex-shrink-0">
        <div className="flex items-center gap-2">
          <History size={14} className="text-indigo-500" />
          <span className="text-sm font-semibold text-gray-800">
            Chat History
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onNewChat}
            className="text-indigo-500 hover:text-indigo-700 p-1 rounded transition-colors"
            title="New Chat"
          >
            <Plus size={14} />
          </button>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 size={20} className="text-gray-300 animate-spin" />
          </div>
        ) : threads.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-4">
            <div className="w-10 h-10 rounded-full bg-gray-50 flex items-center justify-center mb-3">
              <MessageSquare size={20} className="text-gray-300" />
            </div>
            <p className="text-sm text-gray-400 mb-4">
              No conversations yet
            </p>
            <button
              onClick={onNewChat}
              className="text-xs text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded-lg px-3 py-2 transition-colors"
            >
              Start a new chat
            </button>
          </div>
        ) : (
          <div className="py-1">
            {threads.map((t) => (
              <ThreadRow
                key={t.id}
                thread={t}
                onSelect={() => onSelectThread(t.id)}
                onDelete={() => onDeleteThread(t.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ThreadRow({
  thread,
  onSelect,
  onDelete,
}: {
  thread: ChatThreadSummary;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const title = thread.title || "Untitled chat";
  const displayTitle = title.length > 40 ? title.slice(0, 40) + "…" : title;

  return (
    <div
      onClick={onSelect}
      className="group flex items-center gap-2 px-3 py-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm text-gray-800 truncate">{displayTitle}</div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[10px] text-gray-400">
            {thread.message_count} msg
            {thread.message_count !== 1 ? "s" : ""}
          </span>
          <span className="text-[10px] text-gray-300">·</span>
          <span className="text-[10px] text-gray-400">
            {relativeTimeShort(thread.updated_at)}
          </span>
        </div>
      </div>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-red-500 p-0.5 rounded transition-all"
        title="Delete"
      >
        <Trash2 size={12} />
      </button>
    </div>
  );
}

function EmptyState({ onSelect }: { onSelect: (text: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-4">
      <div className="w-10 h-10 rounded-full bg-indigo-50 flex items-center justify-center mb-3">
        <MessageSquare size={20} className="text-indigo-400" />
      </div>
      <h3 className="text-sm font-semibold text-gray-700 mb-1">
        Workflow Assistant
      </h3>
      <p className="text-xs text-gray-400 mb-4 max-w-[260px]">
        Ask questions about your graph or describe changes you'd like to make.
      </p>
      <div className="flex flex-col gap-1.5 w-full">
        {EXAMPLE_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            onClick={() => onSelect(prompt)}
            className="text-left text-xs text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded-lg px-3 py-2 transition-colors"
          >
            "{prompt}"
          </button>
        ))}
      </div>
    </div>
  );
}

function StreamingDots() {
  return (
    <div className="flex justify-start mb-3">
      <div className="bg-gray-50 rounded-2xl rounded-bl-md px-3.5 py-3 shadow-xs">
        <div className="flex gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:0ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:150ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  );
}
