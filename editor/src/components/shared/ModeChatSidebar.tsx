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
import { X, Trash2, Send, Loader2, Sparkles, Maximize2, Paperclip, FileText, ImageIcon } from "lucide-react";
import { Marked, Renderer } from "marked";
import DOMPurify from "dompurify";
import hljs from "../../lib/hljs";
import { useAppStore, type AppMode } from "../../store/useAppStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import { nativeTerminal } from "../../lib/electronBridge";
import { useCodeStore } from "../../store/useCodeStore";
import type { ChatMessage } from "../../types/chat";
import {
  type ComposerAttachmentDraft,
  fileToAttachmentDraft,
  normalizeAttachmentDrafts,
  startEditorChat,
  streamEditorChatResponse,
} from "../../lib/editorChat";

/* ------------------------------------------------------------------ */
/*  Chat sender registry (per-mode)                                    */
/* ------------------------------------------------------------------ */

const chatSenders = new Map<AppMode, (text: string) => void>();
const chatAttachmentReceivers = new Map<
  AppMode,
  (attachment: ComposerAttachmentDraft) => void
>();
const pendingModeMessages = new Map<AppMode, string[]>();
const pendingModeAttachments = new Map<AppMode, ComposerAttachmentDraft[]>();
let persistentChatBridgeInstalled = false;

export function registerModeChatSender(mode: AppMode, fn: (text: string) => void) {
  chatSenders.set(mode, fn);
  const pending = pendingModeMessages.get(mode);
  if (pending?.length) {
    pendingModeMessages.delete(mode);
    queueMicrotask(() => {
      for (const message of pending) fn(message);
    });
  }
}

export function sendToModeChat(mode: AppMode, text: string) {
  const sender = chatSenders.get(mode);
  if (sender) {
    sender(text);
    return;
  }
  const pending = pendingModeMessages.get(mode) ?? [];
  pending.push(text);
  pendingModeMessages.set(mode, pending);
}

export function registerModeChatAttachmentReceiver(
  mode: AppMode,
  fn: (attachment: ComposerAttachmentDraft) => void,
) {
  chatAttachmentReceivers.set(mode, fn);
  const pending = pendingModeAttachments.get(mode);
  if (pending?.length) {
    pendingModeAttachments.delete(mode);
    queueMicrotask(() => {
      for (const attachment of pending) fn(attachment);
    });
  }
}

export function appendAttachmentToModeChat(
  mode: AppMode,
  attachment: ComposerAttachmentDraft,
) {
  const receiver = chatAttachmentReceivers.get(mode);
  if (receiver) {
    receiver(attachment);
    return;
  }
  const pending = pendingModeAttachments.get(mode) ?? [];
  pending.push(attachment);
  pendingModeAttachments.set(mode, pending);
}

if (typeof window !== "undefined" && !persistentChatBridgeInstalled) {
  persistentChatBridgeInstalled = true;
  window.addEventListener("persistent-chat:send", ((event: Event) => {
    const customEvent = event as CustomEvent<{ message?: string }>;
    const message = customEvent.detail?.message?.trim();
    if (!message) return;
    const activeMode = useAppStore.getState().activeMode;
    if (activeMode === "chat") return;
    if (!chatSenders.has(activeMode)) {
      window.dispatchEvent(new CustomEvent("app:toggleModeChatSidebar"));
    }
    sendToModeChat(activeMode, message);
  }) as EventListener);
}

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

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
      `<pre data-mode-chat-copy-zone="true" class="bg-gray-950 text-gray-100 p-2 overflow-x-auto text-[11px] leading-relaxed font-mono m-0"><code>${highlighted}</code></pre>` +
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
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function ModeChatSidebar({ mode, onClose, contextProvider }: ModeChatSidebarProps) {
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);

  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    loadChatHistory(workspaceId, mode),
  );
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [userAttachments, setUserAttachments] = useState<ComposerAttachmentDraft[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
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
    setUserAttachments([]);
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

  const appendAttachment = useCallback((attachment: ComposerAttachmentDraft) => {
    setUserAttachments((prev) => [...prev, attachment]);
  }, []);

  const handleFileSelection = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      if (files.length > 0) {
        setUserAttachments((prev) => [...prev, ...files.map(fileToAttachmentDraft)]);
      }
      e.target.value = "";
    },
    [],
  );

  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData.files ?? []);
    if (files.length === 0) return;
    e.preventDefault();
    setUserAttachments((prev) => [...prev, ...files.map(fileToAttachmentDraft)]);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (e.dataTransfer.types.includes("Files")) {
      e.preventDefault();
      setIsDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setIsDragOver(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    setIsDragOver(false);
    setUserAttachments((prev) => [
      ...prev,
      ...Array.from(e.dataTransfer.files).map(fileToAttachmentDraft),
    ]);
  }, []);

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
      if (streaming) return;
      const trimmed = text.trim();
      const pendingAttachments = userAttachments;
      if (!trimmed && pendingAttachments.length === 0) return;
      const attachments = await normalizeAttachmentDrafts(pendingAttachments);

      const ctx = contextProvider?.() ?? "";
      const fullMessage = ctx ? (trimmed ? `${ctx}\n\n${trimmed}` : ctx) : trimmed;

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content:
          trimmed ||
          (attachments.length === 1
            ? `Attached ${attachments[0].name}`
            : `Attached ${attachments.length} items`),
        timestamp: Date.now(),
        attachments:
          attachments.length > 0
            ? attachments.map((attachment) => ({
                path: attachment.path ?? attachment.source ?? attachment.name,
                filename: attachment.name,
                size: attachment.size,
              }))
            : undefined,
      };

      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setInput("");
      setUserAttachments([]);
      setStreaming(true);

      if (textareaRef.current) textareaRef.current.style.height = "auto";

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const { threadId, response } = await startEditorChat({
          message: fullMessage,
          history: messagesRef.current.flatMap((message) =>
            message.role === "user" || message.role === "assistant"
              ? [{ role: message.role, content: message.content }]
              : [],
          ),
          threadId: threadIdRef.current,
          mode: "auto",
          scope: `mode-chat:${mode}`,
          attachments,
          surfaceContext: {
            mode,
            workspace_id: workspaceId,
          },
        });
        threadIdRef.current = threadId;

        const ws = streamEditorChatResponse(response, {
          onQueued: (position) => {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMsg.id
                  ? {
                      ...message,
                      content:
                        position > 1
                          ? `Queued behind ${position} earlier messages...`
                          : "Queued behind an earlier message...",
                    }
                  : message,
              ),
            );
          },
          onProgress: (content) => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMsg.id ? { ...message, content } : message,
              ),
            );
          },
          onToolCallStart: (toolCall) => {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMsg.id
                  ? {
                      ...message,
                      toolCalls: [
                        ...(message.toolCalls ?? []),
                        {
                          id: toolCall.id,
                          toolName: toolCall.toolName,
                          argsPreview: toolCall.argsPreview,
                          status: "running",
                        },
                      ],
                    }
                  : message,
              ),
            );
          },
          onToolCallResult: (toolCall) => {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMsg.id
                  ? {
                      ...message,
                      toolCalls: (message.toolCalls ?? []).map((existing) =>
                        existing.id === toolCall.id
                          ? {
                              ...existing,
                              status:
                                toolCall.status === "running" ||
                                toolCall.status === "error" ||
                                toolCall.status === "success"
                                  ? toolCall.status
                                  : "success",
                              outputPreview: toolCall.outputPreview,
                              durationMs: toolCall.durationMs,
                            }
                          : existing,
                      ),
                    }
                  : message,
              ),
            );
          },
          onFileAttachment: (attachment) => {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMsg.id
                  ? {
                      ...message,
                      attachments: (message.attachments ?? []).some(
                        (existing) => existing.path === attachment.path,
                      )
                        ? message.attachments
                        : [
                            ...(message.attachments ?? []),
                            {
                              path: attachment.path,
                              filename: attachment.filename,
                              size: attachment.size,
                            },
                          ],
                    }
                  : message,
              ),
            );
          },
          onComplete: (content) => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMsg.id ? { ...message, content } : message,
              ),
            );
            setStreaming(false);
            abortRef.current = null;
          },
          onError: (message) => {
            setMessages((prev) =>
              prev.map((entry) =>
                entry.id === assistantMsg.id
                  ? { ...entry, content: message || "Failed to connect to DAN server." }
                  : entry,
              ),
            );
            setStreaming(false);
            abortRef.current = null;
          },
          onCloseWithoutTerminalEvent: () => {
            setMessages((prev) =>
              prev.map((entry) =>
                entry.id === assistantMsg.id && !entry.content
                  ? { ...entry, content: "Connection lost. Please try again." }
                  : entry,
              ),
            );
            setStreaming(false);
            abortRef.current = null;
          },
        });

        if (ws) {
          controller.signal.addEventListener(
            "abort",
            () => {
              ws.close();
              abortRef.current = null;
            },
            { once: true },
          );
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
    [contextProvider, mode, streaming, userAttachments, workspaceId],
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
    registerModeChatAttachmentReceiver(mode, appendAttachment);
    return () => {
      chatSenders.delete(mode);
      chatAttachmentReceivers.delete(mode);
    };
  }, [appendAttachment, doSend, mode]);

  const handleClear = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    setMessages([]);
    setUserAttachments([]);
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
    <div
      className={`h-full w-full flex flex-col bg-white text-gray-800 dark:bg-gray-900 dark:text-gray-300 ${
        isDragOver ? "ring-2 ring-blue-500/50 ring-inset" : ""
      }`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 shrink-0 dark:border-gray-800">
        <span className="text-[11px] font-semibold tracking-widest text-gray-500 uppercase dark:text-gray-400">
          AI Chat
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => { useAppStore.getState().setMode("chat"); onClose(); }}
            title="Open full Chat mode (threads, history, slash commands)"
            className="rounded p-1 text-gray-500 transition-colors hover:text-blue-600 dark:hover:text-blue-400"
          >
            <Maximize2 size={14} />
          </button>
          <button
            onClick={handleClear}
            title="Clear chat"
            className="rounded p-1 text-gray-500 transition-colors hover:text-gray-800 dark:hover:text-gray-300"
          >
            <Trash2 size={14} />
          </button>
          <button
            onClick={onClose}
            title="Close chat (⌘J)"
            className="rounded p-1 text-gray-500 transition-colors hover:text-gray-800 dark:hover:text-gray-300"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      <div ref={scrollRef} onClick={handleMessageClick} className="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-3">
        {messages.length === 0 && (
          <div className="flex h-full select-none flex-col items-center justify-center gap-2 text-xs text-gray-500 dark:text-gray-600">
            <Sparkles size={24} className="text-gray-400 dark:text-gray-700" />
            <span>
              {mode === "research"
                ? "Ask about the active paper, notes, or figures"
                : "Ask anything about your code"}
            </span>
            <button
              onClick={() => { useAppStore.getState().setMode("chat"); onClose(); }}
              className="mt-1 text-[10px] text-gray-500 transition-colors hover:text-blue-600 dark:hover:text-blue-400"
            >
              Open full Chat for threads, / commands &amp; history
            </button>
            <span className="text-[10px] text-gray-400 dark:text-gray-600">
              Drag files here or paste an image/file into the composer
            </span>
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
                  ? "max-w-[85%] rounded-lg bg-blue-600 px-3 py-2 text-[13px] leading-relaxed text-white"
                  : "max-w-[85%] rounded-lg bg-gray-100 px-3 py-2 text-[13px] leading-relaxed text-gray-800 dark:bg-gray-800 dark:text-gray-200"
              }
            >
              {msg.role === "user" ? (
                <span className="whitespace-pre-wrap break-words">{msg.content}</span>
              ) : msg.content ? (
                <div
                  data-mode-chat-copy-zone="true"
                  className="break-words [&>p]:my-1 [&>ul]:my-1 [&>ol]:my-1 [&>p:first-child]:mt-0 [&>p:last-child]:mb-0"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />
              ) : streaming ? (
                <span className="inline-flex items-center gap-1.5 text-gray-400">
                  <Loader2 size={12} className="animate-spin" />
                  Thinking…
                </span>
              ) : null}
              {msg.attachments && msg.attachments.length > 0 && (
                <div className="mt-2 space-y-1.5">
                  {msg.attachments.map((attachment) => (
                    <div
                      key={`${msg.id}-${attachment.path}`}
                      title={attachment.path || attachment.filename}
                      className="rounded-md border border-black/10 bg-black/5 px-2 py-1.5 text-[11px] dark:border-white/10 dark:bg-white/5"
                    >
                      <div className="flex min-w-0 items-center gap-1.5">
                        <FileText size={11} className="shrink-0" />
                        <span className="truncate font-medium">{attachment.filename}</span>
                      </div>
                      {attachment.path && attachment.path !== attachment.filename && (
                        <div className="mt-0.5 truncate text-[10px] text-gray-500 dark:text-gray-400">
                          {attachment.path}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {msg.toolCalls && msg.toolCalls.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {msg.toolCalls.map((toolCall) => (
                    <span
                      key={toolCall.id}
                      className="rounded-full border border-black/10 bg-black/5 px-2 py-0.5 text-[10px] dark:border-white/10 dark:bg-white/5"
                    >
                      {toolCall.status === "running" ? "Running" : toolCall.status === "error" ? "Tool error" : "Tool"}
                      {" "}
                      {toolCall.toolName}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <span className="mt-0.5 px-1 text-[10px] text-gray-500 dark:text-gray-600">
              {formatTime(msg.timestamp)}
            </span>
          </div>
        ))}
      </div>

      <div className="px-3 py-2 border-t border-gray-200 shrink-0 dark:border-gray-800">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileSelection}
        />
        {userAttachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {userAttachments.map((attachment) => (
              <div
                key={attachment.id}
                title={attachment.path || attachment.name}
                className="inline-flex max-w-full items-center gap-1 rounded-full border border-gray-300 bg-gray-100 px-2 py-1 text-[11px] text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
              >
                {attachment.kind === "figure" ? (
                  <ImageIcon size={11} className="shrink-0" />
                ) : (
                  <FileText size={11} className="shrink-0" />
                )}
                <span className="truncate max-w-[180px]">{attachment.name}</span>
                <button
                  onClick={() =>
                    setUserAttachments((prev) =>
                      prev.filter((entry) => entry.id !== attachment.id),
                    )
                  }
                  className="text-gray-500 transition-colors hover:text-gray-800 dark:hover:text-gray-200"
                  title={`Remove ${attachment.name}`}
                >
                  <X size={10} />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-end gap-2">
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={streaming}
            title="Append files"
            className="shrink-0 rounded-lg border border-gray-300 bg-white p-2 text-gray-600 transition-colors hover:border-blue-400 hover:text-blue-600 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:border-gray-600"
          >
            <Paperclip size={16} />
          </button>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder="Ask anything…"
            rows={1}
            disabled={streaming}
            className="flex-1 resize-none rounded-lg border border-gray-300 bg-white px-3 py-2 text-[13px] text-gray-900 placeholder-gray-500 focus:border-blue-500 focus:outline-none disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-white dark:focus:border-gray-600"
            style={{ maxHeight: 96 }}
          />
          <button
            onClick={() => doSend(input)}
            disabled={(!input.trim() && userAttachments.length === 0) || streaming}
            className="shrink-0 rounded-lg bg-blue-600 p-2 text-white transition-colors hover:bg-blue-500 disabled:bg-gray-300 disabled:text-gray-500 dark:disabled:bg-gray-700"
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
