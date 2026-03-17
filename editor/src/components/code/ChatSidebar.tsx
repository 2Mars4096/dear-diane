/**
 * Lightweight AI chat sidebar for Code mode.
 * Sends workspace context (active file, open files, pinned roots) alongside
 * user messages and streams the assistant response progressively.
 */
import {
  useState,
  useRef,
  useEffect,
  useCallback,
  useMemo,
  type KeyboardEvent,
  type ChangeEvent,
} from "react";
import { X, Trash2, Send, Loader2, Sparkles, Bug, RefreshCw, TestTube2 } from "lucide-react";
import { Marked, Renderer } from "marked";
import hljs from "../../lib/hljs";
import { useCodeStore } from "../../store/useCodeStore";
import { useAppStore } from "../../store/useAppStore";
import { buildApiWebSocketUrl } from "../../lib/api";
import { nativeFs, nativeTerminal } from "../../lib/electronBridge";

/* ------------------------------------------------------------------ */
/*  Chat sender registration (for cross-component message injection)   */
/* ------------------------------------------------------------------ */

import { sendToModeChat } from "../shared/ModeChatSidebar";

let chatSendFunction: ((text: string) => void) | null = null;

export function registerChatSender(fn: (text: string) => void) {
  chatSendFunction = fn;
}

export function sendToChat(text: string) {
  if (chatSendFunction) {
    chatSendFunction(text);
  } else {
    sendToModeChat("development", text);
  }
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

/* ------------------------------------------------------------------ */
/*  Markdown renderer (lightweight — no katex / mentions)              */
/* ------------------------------------------------------------------ */

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const SHELL_LANGS = new Set(["sh", "bash", "shell", "zsh", "terminal", "console"]);

/**
 * Post-process rendered HTML to make file-path-like strings clickable.
 * Preserves <pre> blocks (code fences) and existing tags so the regex
 * only touches visible text nodes.
 */
function linkifyFilePaths(html: string): string {
  const preserved: string[] = [];
  const preserve = (m: string) => {
    preserved.push(m);
    return `\x00P${preserved.length - 1}\x00`;
  };

  let out = html;

  // 1. Protect <pre>…</pre> blocks (fenced code)
  out = out.replace(/<pre[\s\S]*?<\/pre>/gi, preserve);

  // 2. Wrap file paths inside inline <code> tags
  const fpTest = /^(?:~\/|\.{0,2}\/)?(?:[\w@.-]+\/)+[\w@.-]+\.\w{1,10}$/;
  out = out.replace(/<code([^>]*)>([^<]+)<\/code>/g, (full, attrs, text) => {
    const t = text.trim();
    if (fpTest.test(t)) {
      return (
        `<code${attrs}><span class="file-path-link cursor-pointer text-blue-400 hover:text-blue-300 hover:underline" ` +
        `data-file-path="${escapeHtml(t)}">${text}</span></code>`
      );
    }
    return full;
  });

  // 3. Protect all remaining HTML tags so the bare-path regex only hits text
  out = out.replace(/<[^>]+>/g, preserve);

  // 4. Linkify bare file paths in plain text
  out = out.replace(
    /(?:~\/|\.{0,2}\/)?(?:[\w@.-]+\/)+[\w@.-]+\.\w{1,10}/g,
    (m) =>
      `<span class="file-path-link cursor-pointer text-blue-400 hover:text-blue-300 hover:underline" ` +
      `data-file-path="${escapeHtml(m)}">${escapeHtml(m)}</span>`,
  );

  // 5. Restore preserved fragments
  out = out.replace(/\x00P(\d+)\x00/g, (_, i) => preserved[+i]);
  return out;
}

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
    const insertSvg = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>`;
    const runBtn = isShell
      ? `<button data-action="run" class="text-green-400 hover:text-green-300 px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors" title="Run in terminal">\u25B6 Run</button>`
      : "";
    const buttons =
      `<div class="flex items-center gap-1">` +
      `<button data-action="copy" class="text-gray-500 hover:text-gray-300 p-0.5 rounded transition-colors" title="Copy">${copySvg}</button>` +
      `<button data-action="insert" class="text-gray-500 hover:text-gray-300 p-0.5 rounded transition-colors" title="Insert at cursor">${insertSvg}</button>` +
      `<button data-action="apply" class="text-blue-400 hover:text-blue-300 px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors" title="Apply to active file">Apply</button>` +
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
    // Pre-process: mark file_write / "Wrote to `path`" patterns with HTML
    // comment markers that survive markdown parsing.
    const annotated = src.replace(
      /(?:Wrote|Written|Created|Updated|Saved|Modified|file_write)\s*(?:to\s+)?`([^`]+\.\w{1,10})`/gi,
      (m, path) => m + ` <!--DIFF:${btoa(encodeURIComponent(path))}-->`,
    );

    let html = markedInstance.parse(annotated) as string;

    // Replace markers with clickable View Diff buttons
    html = html.replace(/<!--DIFF:([A-Za-z0-9+/=]+)-->/g, (_, enc) => {
      try {
        const p = decodeURIComponent(atob(enc));
        return (
          ` <button class="inline-flex items-center gap-0.5 text-[10px] text-purple-400 hover:text-purple-300 ` +
          `bg-purple-900/30 hover:bg-purple-900/50 rounded px-1.5 py-0.5 transition-colors font-medium align-middle" ` +
          `data-diff-path="${escapeHtml(p)}" title="View diff for this file">View Diff</button>`
        );
      } catch {
        return "";
      }
    });

    html = linkifyFilePaths(html);
    return html;
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
/*  Workspace context builder                                          */
/* ------------------------------------------------------------------ */

function buildWorkspaceContext(
  activeFilePath: string | null,
  openFiles: { path: string; content: string; language: string }[],
  pinnedRoots: string[],
): string {
  const activeFile = openFiles.find((f) => f.path === activeFilePath);
  const lines: string[] = ["[Workspace Context]"];

  if (activeFile) {
    const lineCount = activeFile.content.split("\n").length;
    lines.push(
      `Active file: ${activeFile.path} (${activeFile.language}, ${lineCount} lines)`,
    );
  }

  if (openFiles.length > 0) {
    const names = openFiles.map((f) => f.path.split("/").pop()).join(", ");
    lines.push(`Open files: ${names}`);
  }

  if (pinnedRoots.length > 0) {
    lines.push(`Workspace roots: ${pinnedRoots.join(", ")}`);
  }

  if (activeFile) {
    const preview = activeFile.content.split("\n").slice(0, 200).join("\n");
    lines.push("", `[Active File Content (first 200 lines)]`, preview);
  }

  return lines.join("\n");
}

/* ------------------------------------------------------------------ */
/*  File path extraction from AI messages                              */
/* ------------------------------------------------------------------ */

function extractFilePaths(text: string): string[] {
  const paths: string[] = [];
  const backtickPattern = /`((?:\/[\w.-]+)+(?:\.\w+))`/g;
  let match;
  while ((match = backtickPattern.exec(text)) !== null) {
    paths.push(match[1]);
  }
  const writePattern =
    /(?:Wrote to|Created|Modified|Updated|Saved)\s+`([^`]+)`/gi;
  while ((match = writePattern.exec(text)) !== null) {
    if (!paths.includes(match[1])) paths.push(match[1]);
  }
  return [...new Set(paths)];
}

function pushSuggestedFiles(text: string): void {
  const filePaths = extractFilePaths(text);
  if (filePaths.length === 0) return;
  const store = useCodeStore.getState();
  const isNewFile = /\b(?:created|new file|wrote to)\b/i.test(text);
  for (const fp of filePaths) {
    store.addAgentSuggestedFile(fp, isNewFile);
  }
}

/* ------------------------------------------------------------------ */
/*  Shell command dispatch (for cross-component terminal streaming)     */
/* ------------------------------------------------------------------ */

export function dispatchShellCommand(command: string, cwd?: string) {
  window.dispatchEvent(
    new CustomEvent("chat:shellCommand", { detail: { command, cwd } }),
  );
}

/* ------------------------------------------------------------------ */
/*  Quick actions                                                      */
/* ------------------------------------------------------------------ */

interface QuickAction {
  label: string;
  icon: React.ReactNode;
  buildPrompt: (filename: string) => string;
}

const QUICK_ACTIONS: QuickAction[] = [
  {
    label: "Explain",
    icon: <Sparkles size={12} />,
    buildPrompt: (f) => `Explain the active file: ${f}`,
  },
  {
    label: "Find bugs",
    icon: <Bug size={12} />,
    buildPrompt: (f) => `Review this code for bugs: ${f}`,
  },
  {
    label: "Refactor",
    icon: <RefreshCw size={12} />,
    buildPrompt: (f) => `Suggest refactoring for: ${f}`,
  },
  {
    label: "Write tests",
    icon: <TestTube2 size={12} />,
    buildPrompt: (f) => `Write tests for: ${f}`,
  },
];

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

interface ChatSidebarProps {
  onClose: () => void;
}

export default function ChatSidebar({ onClose }: ChatSidebarProps) {
  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const openFiles = useCodeStore((s) => s.openFiles);
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const chatThreadId = useAppStore((s) => s.activeChatThreadId);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [threadId, setThreadId] = useState<string | undefined>(() => chatThreadId ?? undefined);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  /* Auto-scroll on new content */
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  /* Auto-resize textarea */
  const handleInputChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      setInput(e.target.value);
      const ta = e.target;
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 96)}px`;
    },
    [],
  );

  /* Derived values */
  const activeFileName = useMemo(
    () => (activeFilePath ? activeFilePath.split("/").pop() ?? null : null),
    [activeFilePath],
  );

  const contextSummary = useMemo(() => {
    const parts: string[] = [];
    if (activeFileName) parts.push(activeFileName);
    if (openFiles.length > 0) parts.push(`${openFiles.length} open`);
    if (pinnedRoots.length > 0) {
      const rootName = pinnedRoots[0].split("/").pop() ?? pinnedRoots[0];
      parts.push(`Workspace: ${rootName}`);
    }
    return parts.join(" · ");
  }, [activeFileName, openFiles.length, pinnedRoots]);

  /* ------ Code block action handlers ----------------------------------- */

  const handleApplyCode = useCallback((code: string) => {
    const { activeFilePath: afp, openFiles: of, openDiff, openFile } = useCodeStore.getState();
    if (!afp) {
      openFile("untitled", code, "plaintext");
      return;
    }
    const activeFile = of.find((f) => f.path === afp);
    if (!activeFile) return;
    openDiff(activeFile.content, code, afp, afp + " (AI suggestion)");
  }, []);

  const handleInsertCode = useCallback((code: string) => {
    window.dispatchEvent(new CustomEvent("editor:insertCode", { detail: { code } }));
  }, []);

  /* ------ Chat→editor bridge handlers ----------------------------------- */

  const handleOpenFilePath = useCallback(async (rawPath: string) => {
    const { pinnedRoots, openFile } = useCodeStore.getState();
    const cleanPath = rawPath.startsWith("./") ? rawPath.slice(2) : rawPath;

    if (!cleanPath.startsWith("/")) {
      for (const root of pinnedRoots) {
        const candidate = `${root}/${cleanPath}`;
        try {
          const content = await nativeFs.readFile(candidate);
          if (content !== null) {
            openFile(candidate, content);
            return;
          }
        } catch {
          /* try next root */
        }
      }
    }

    try {
      const content = await nativeFs.readFile(cleanPath);
      if (content !== null) {
        openFile(cleanPath, content);
      }
    } catch {
      /* file not found */
    }
  }, []);

  const handleViewDiff = useCallback(async (rawPath: string) => {
    const { pinnedRoots, openFiles: ofs, openDiff } = useCodeStore.getState();
    let filePath = rawPath.startsWith("./") ? rawPath.slice(2) : rawPath;

    if (!filePath.startsWith("/")) {
      for (const root of pinnedRoots) {
        const candidate = `${root}/${filePath}`;
        if (await nativeFs.exists(candidate)) {
          filePath = candidate;
          break;
        }
      }
    }

    try {
      const diskContent = await nativeFs.readFile(filePath);
      if (diskContent !== null) {
        const match = ofs.find((f) => f.path === filePath);
        const original = match?.originalContent ?? "";
        openDiff(original, diskContent, filePath + " (before)", filePath);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const handleRunInTerminal = useCallback(async (command: string) => {
    const { pinnedRoots, setShowTerminal, addTerminal, setActiveTerminal } =
      useCodeStore.getState();
    setShowTerminal(true);

    const cwd = pinnedRoots[0] || undefined;
    const id = await nativeTerminal.create({ shell: "/bin/zsh", cwd });
    if (id) {
      const shortCmd =
        command.length > 40 ? command.slice(0, 37) + "..." : command;
      addTerminal(id, `\u26A1 ${shortCmd}`);
      setActiveTerminal(id);
      setTimeout(() => nativeTerminal.write(id, command + "\n"), 300);
    }
  }, []);

  /* ------ Unified click delegation for rendered messages ----------------- */

  const handleMessageClick = useCallback(
    async (e: React.MouseEvent<HTMLDivElement>) => {
      const target = e.target as HTMLElement;

      // Clickable file path
      const filePathEl = target.closest("[data-file-path]") as HTMLElement | null;
      if (filePathEl) {
        const fp = filePathEl.dataset.filePath;
        if (fp) handleOpenFilePath(fp);
        return;
      }

      // View Diff button (from file_write detection)
      const diffBtn = target.closest("[data-diff-path]") as HTMLElement | null;
      if (diffBtn) {
        const dp = diffBtn.dataset.diffPath;
        if (dp) await handleViewDiff(dp);
        return;
      }

      // Code block toolbar actions
      const btn = target.closest("[data-action]") as HTMLElement | null;
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
      } else if (action === "apply") {
        handleApplyCode(code);
      } else if (action === "insert") {
        handleInsertCode(code);
      } else if (action === "run") {
        await handleRunInTerminal(code);
      }
    },
    [handleApplyCode, handleInsertCode, handleOpenFilePath, handleViewDiff, handleRunInTerminal],
  );

  /* ------ Send message ------------------------------------------------ */

  const doSend = useCallback(
    async (text: string) => {
      if (!text.trim() || streaming) return;

      const wsContext = buildWorkspaceContext(
        activeFilePath,
        openFiles,
        pinnedRoots,
      );
      const fullMessage = `${wsContext}\n\n${text.trim()}`;

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

      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const resp = await sendChatRequest(fullMessage, threadId);
        if (!resp.ok) {
          const errText = await resp.text().catch(() => "Unknown error");
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id
                ? { ...m, content: `Failed to connect to DAN server: ${errText}` }
                : m,
            ),
          );
          setStreaming(false);
          return;
        }

        const data = await resp.json();
        if (data.thread_id) setThreadId(data.thread_id);
        const channelId: string | undefined = data.stream_channel_id;

        if (channelId) {
          const ws = new WebSocket(
            buildApiWebSocketUrl(`/api/chat/${channelId}/events`),
          );

          ws.onmessage = (e) => {
            if (controller.signal.aborted) {
              ws.close();
              return;
            }
            try {
              const evt = JSON.parse(e.data);
              if (evt.type === "token" && typeof evt.token === "string") {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsg.id
                      ? { ...m, content: m.content + evt.token }
                      : m,
                  ),
                );
              } else if (evt.type === "content" && typeof evt.content === "string") {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsg.id
                      ? { ...m, content: m.content + evt.content }
                      : m,
                  ),
                );
              } else if (evt.type === "done" || evt.type === "end") {
                ws.close();
              }
            } catch {
              /* ignore parse errors */
            }
          };

          ws.onclose = () => {
            setStreaming(false);
            abortRef.current = null;
            setMessages((prev) => {
              const completed = prev.find((m) => m.id === assistantMsg.id);
              if (completed?.content) pushSuggestedFiles(completed.content);
              return prev;
            });
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
          /* Non-streaming fallback: use response body directly */
          const text =
            typeof data.response === "string"
              ? data.response
              : typeof data.content === "string"
                ? data.content
                : JSON.stringify(data);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id ? { ...m, content: text } : m,
            ),
          );
          pushSuggestedFiles(text);
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
    [activeFilePath, openFiles, pinnedRoots, streaming, threadId],
  );

  /* ------ Key handler ------------------------------------------------- */

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        doSend(input);
      }
    },
    [doSend, input],
  );

  /* ------ Quick action ------------------------------------------------ */

  const handleQuickAction = useCallback(
    (action: QuickAction) => {
      if (!activeFileName) return;
      doSend(action.buildPrompt(activeFileName));
    },
    [activeFileName, doSend],
  );

  /* ------ Register chat sender for cross-component access -------------- */

  useEffect(() => {
    registerChatSender(doSend);
    return () => { chatSendFunction = null; };
  }, [doSend]);

  /* ------ Clear chat -------------------------------------------------- */

  const handleClear = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
    setMessages([]);
    setThreadId(undefined);
    setStreaming(false);
  }, []);

  /* ------ Format time ------------------------------------------------- */

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  /* ------------------------------------------------------------------ */
  /*  Render                                                             */
  /* ------------------------------------------------------------------ */

  return (
    <div className="h-full w-full flex flex-col bg-gray-900 text-gray-300">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800 shrink-0">
        <span className="text-[11px] font-semibold tracking-widest text-gray-400 uppercase">
          AI Chat
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={handleClear}
            title="Clear chat"
            className="p-1 text-gray-500 hover:text-gray-300 transition-colors rounded"
          >
            <Trash2 size={14} />
          </button>
          <button
            onClick={onClose}
            title="Close chat"
            className="p-1 text-gray-500 hover:text-gray-300 transition-colors rounded"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} onClick={handleMessageClick} className="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-3">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-600 text-xs gap-2 select-none">
            <Sparkles size={24} className="text-gray-700" />
            <span>Ask anything about your code</span>
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
                  : "bg-gray-800 text-gray-200 rounded-lg px-3 py-2 max-w-[85%] text-[13px] leading-relaxed chat-sidebar-markdown"
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

      {/* Quick actions */}
      {activeFileName && !streaming && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 border-t border-gray-800/50 overflow-x-auto shrink-0">
          {QUICK_ACTIONS.map((action) => (
            <button
              key={action.label}
              onClick={() => handleQuickAction(action)}
              className="flex items-center gap-1 bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs rounded px-2 py-1 whitespace-nowrap transition-colors"
            >
              {action.icon}
              {action.label}
            </button>
          ))}
        </div>
      )}

      {/* Context indicator */}
      {contextSummary && (
        <div className="bg-gray-800/50 text-[10px] text-gray-500 px-3 py-1 border-t border-gray-800/30 truncate shrink-0">
          {contextSummary}
        </div>
      )}

      {/* Input */}
      <div className="px-3 py-2 border-t border-gray-800 shrink-0">
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your code…"
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
