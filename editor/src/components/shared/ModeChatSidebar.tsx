/**
 * ModeChatSidebar: reusable AI chat sidebar for any mode.
 * Persists compact chat session state per workspace + mode + workflow in localStorage.
 * Each mode provides its own context via the `contextProvider` prop.
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
import {
  X,
  Trash2,
  Send,
  Loader2,
  Sparkles,
  Maximize2,
  History,
  Paperclip,
  FileText,
  ImageIcon,
  Square,
  ArrowUp,
  ArrowLeft,
  GripVertical,
  PencilLine,
  HelpCircle,
  Bug,
  Plus,
} from "lucide-react";
import { useAppStore, type AppMode } from "../../store/useAppStore";
import { useGraphStore } from "../../store/useGraphStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import type { ChatMessage } from "../../types/chat";
import * as api from "../../lib/api";
import {
  type ComposerAttachmentDraft,
  cloneAttachmentDraft,
  fileToAttachmentDraft,
  resolveAttachmentName,
} from "../../lib/editorChat";
import ChatMessageBubble from "../ChatMessage";
import MentionAutocomplete from "../MentionAutocomplete";
import {
  findMentionQuery,
  insertMention,
  type MentionRef,
} from "../../lib/mentionParser";
import {
  toBackendMessage,
} from "../../lib/chatMessagePersistence";
import {
  deriveDraftThreadTitleFromMessage,
  getDisplayThreadTitle,
} from "../../lib/chatThreadTitle";
import {
  buildBranchedThreadTitle,
  getEditBranchTarget,
  getExploreBranchTarget,
  getRegenerateBranchTarget,
} from "../../lib/chatBranching";
import {
  formatCodeContextForChat,
  resolveClipboardCodeContext,
} from "../../lib/clipboardContext";
import {
  buildModeChatScopeKey,
  formatModeChatWorkflowLabel,
  loadModeChatSession,
  resolveModeChatWorkflowId,
  saveModeChatSession,
  type StoredModeChatSession,
} from "./modeChatSidebarSession";
import {
  type BranchType,
  type PendingQueueItem,
  type RewriteBranchState,
  type SidebarChatMode,
} from "./modeChatSidebarTypes";
import { useModeChatSidebarHistory } from "./useModeChatSidebarHistory";
import { useModeChatSidebarNativeActions } from "./useModeChatSidebarNativeActions";
import { useModeChatSidebarTransport } from "./useModeChatSidebarTransport";

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

interface ModeChatSidebarInnerProps extends ModeChatSidebarProps {
  workflowId: string;
  workspaceId: string | null;
  sidebarCopy: {
    surfaceLabel: string;
    emptyPrimary: string;
    emptySecondary: string;
  };
}

const MODE_SIDECAR_COPY: Record<
  AppMode,
  {
    surfaceLabel: string;
    emptyPrimary: string;
    emptySecondary: string;
  }
> = {
  chat: {
    surfaceLabel: "Chat sidecar",
    emptyPrimary: "Ask a question or reopen a saved conversation.",
    emptySecondary: "Use History to revisit threads, drag files in, paste images, type @ for mentions, or / for commands.",
  },
  research: {
    surfaceLabel: "Research sidecar",
    emptyPrimary: "Ask about the active paper, citations, notes, or training run.",
    emptySecondary: "Use History to reopen saved chats, or drag files here, paste files/images, use @ mentions, and type / for commands.",
  },
  development: {
    surfaceLabel: "Code sidecar",
    emptyPrimary: "Ask about the open file, workspace state, or pending code review.",
    emptySecondary: "Use History to reopen saved chats, or drag files here, paste files/images, use @ mentions, and type / for commands.",
  },
  operations: {
    surfaceLabel: "Workflow sidecar",
    emptyPrimary: "Ask about the current graph, run logs, or workflow edits.",
    emptySecondary: "Use History to revisit saved threads, or drag files here, paste files/images, use @ mentions, and type / for commands.",
  },
  analytics: {
    surfaceLabel: "Analytics sidecar",
    emptyPrimary: "Ask about dashboards, metrics, or recent telemetry.",
    emptySecondary: "Use History to revisit saved chats, or drag files here, paste files/images, use @ mentions, and type / for commands.",
  },
  content: {
    surfaceLabel: "Content sidecar",
    emptyPrimary: "Ask about drafts, notes, or structured writing tasks.",
    emptySecondary: "Use History to revisit saved chats, or drag files here, paste files/images, use @ mentions, and type / for commands.",
  },
};

/* ------------------------------------------------------------------ */
/*  Composer + command helpers                                         */
/* ------------------------------------------------------------------ */

const RECENT_COMMANDS_KEY = "dan-recent-commands";

const MODE_CONFIG: Record<
  SidebarChatMode,
  { label: string; icon: typeof Sparkles; accent: string }
> = {
  agent: { label: "Agent", icon: Sparkles, accent: "indigo" },
  ask: { label: "Ask", icon: HelpCircle, accent: "sky" },
  plan: { label: "Plan", icon: FileText, accent: "amber" },
  debug: { label: "Debug", icon: Bug, accent: "rose" },
  auto: { label: "Auto", icon: PencilLine, accent: "violet" },
};

const LOCAL_SLASH_COMMANDS: Array<{ cmd: string; desc: string }> = [
  { cmd: "/ask", desc: "Switch to Ask mode" },
  { cmd: "/agent", desc: "Switch to Agent mode" },
  { cmd: "/plan", desc: "Switch to Plan mode" },
  { cmd: "/debug", desc: "Switch to Debug mode" },
  { cmd: "/auto", desc: "Auto-detect the best mode" },
  { cmd: "/clear", desc: "Clear this sidebar conversation" },
  { cmd: "/chat", desc: "Open the current conversation in full Chat" },
];

function trackCommand(cmd: string) {
  try {
    const recent: string[] = JSON.parse(
      localStorage.getItem(RECENT_COMMANDS_KEY) ?? "[]",
    );
    const updated = [cmd, ...recent.filter((entry) => entry !== cmd)].slice(0, 20);
    localStorage.setItem(RECENT_COMMANDS_KEY, JSON.stringify(updated));
  } catch {
    /* ignore */
  }
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

function summarizeThreadTitle(messages: ChatMessage[]): string {
  const firstUser = messages.find(
    (message) => message.role === "user" && message.content.trim(),
  );
  if (!firstUser) return "New Chat";
  return deriveDraftThreadTitleFromMessage(firstUser.content, "New Chat");
}

function getRecentCommands(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_COMMANDS_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function describeQueuedItem(item: PendingQueueItem): string {
  if (item.content) return item.content;
  if (item.attachments.length === 1) {
    const attachment = item.attachments[0];
    return `Attached ${resolveAttachmentName(attachment.name, attachment.mimeType)}`;
  }
  if (item.attachments.length > 1) {
    return `Attached ${item.attachments.length} items`;
  }
  return "(empty message)";
}

function parseLeadingCommand(input: string): {
  command: string;
  remainder: string;
} | null {
  const trimmed = input.trim();
  if (!trimmed.startsWith("/")) return null;
  const [command, ...rest] = trimmed.split(/\s+/);
  return {
    command: command.toLowerCase(),
    remainder: rest.join(" ").trim(),
  };
}

function SlashCommandPopup({
  filter,
  onSelect,
}: {
  filter: string;
  onSelect: (command: string) => void;
}) {
  const filtered = LOCAL_SLASH_COMMANDS.filter((entry) =>
    entry.cmd.startsWith(filter.toLowerCase()),
  );
  if (filtered.length === 0) return null;

  return (
    <div className="mb-2 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800">
      {filtered.map((entry) => (
        <button
          key={entry.cmd}
          onMouseDown={(event) => {
            event.preventDefault();
            onSelect(entry.cmd);
          }}
          className="flex w-full items-center gap-3 px-3 py-2 text-left text-xs transition-colors hover:bg-indigo-50 dark:hover:bg-white/5"
        >
          <span className="w-16 shrink-0 font-mono font-semibold text-indigo-600 dark:text-indigo-300">
            {entry.cmd}
          </span>
          <span className="text-gray-500 dark:text-gray-400">{entry.desc}</span>
        </button>
      ))}
    </div>
  );
}

function RecentCommandsBar({ onSelect }: { onSelect: (command: string) => void }) {
  const recentCommands = useMemo(() => {
    const recent = getRecentCommands();
    return recent.length > 0
      ? recent
      : ["/ask", "/agent", "/plan", "/debug", "/clear", "/chat"];
  }, []);

  return (
    <div className="mb-2 flex items-center gap-1.5 overflow-x-auto px-0.5 py-0.5 scrollbar-hide">
      {recentCommands.slice(0, 8).map((command) => (
        <button
          key={command}
          onClick={() => onSelect(command + " ")}
          className="shrink-0 rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-[11px] text-gray-500 transition-colors hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400 dark:hover:border-indigo-500/40 dark:hover:bg-indigo-500/10 dark:hover:text-indigo-300"
        >
          {command}
        </button>
      ))}
    </div>
  );
}

function SmartPasteHint({
  hint,
  onAccept,
  onDismiss,
}: {
  hint: { type: "url" | "code"; value: string };
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const labels = {
    url: { text: "URL detected", action: "Fetch this URL?" },
    code: { text: "Code detected", action: "Wrap in code block?" },
  } as const;
  const label = labels[hint.type];

  return (
    <div className="mt-2 flex items-center gap-2 rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-1.5 text-xs text-indigo-700 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-200">
      <span>{label.text}</span>
      <span className="text-indigo-400 dark:text-indigo-300">-</span>
      <button
        onClick={onAccept}
        className="font-medium text-indigo-600 transition-colors hover:text-indigo-800 dark:text-indigo-300 dark:hover:text-indigo-100"
      >
        {label.action}
      </button>
      <button
        onClick={onDismiss}
        className="ml-auto text-indigo-300 transition-colors hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-200"
      >
        <X size={12} />
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

function ModeChatSidebarInner({
  mode,
  onClose,
  contextProvider,
  workflowId,
  workspaceId,
  sidebarCopy,
}: ModeChatSidebarInnerProps) {
  const initialSessionRef = useRef<StoredModeChatSession>(
    loadModeChatSession(workspaceId, mode, workflowId),
  );

  const [messages, setMessages] = useState<ChatMessage[]>(
    initialSessionRef.current.messages,
  );
  const [chatMode, setChatMode] = useState<SidebarChatMode>(
    initialSessionRef.current.chatMode,
  );
  const [threadId, setThreadId] = useState<string | null>(
    initialSessionRef.current.threadId,
  );
  const [threadTitle, setThreadTitle] = useState<string>(
    summarizeThreadTitle(initialSessionRef.current.messages),
  );
  const [detectedMode, setDetectedMode] = useState<SidebarChatMode | null>(null);
  const [input, setInput] = useState("");
  const [userAttachments, setUserAttachments] = useState<ComposerAttachmentDraft[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isComposerFocused, setIsComposerFocused] = useState(false);
  const [pasteHint, setPasteHint] = useState<
    { type: "url" | "code"; value: string } | null
  >(null);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionAnchor, setMentionAnchor] = useState<{
    top: number;
    left: number;
  } | null>(null);
  const [rewriteTarget, setRewriteTarget] = useState<RewriteBranchState | null>(
    null,
  );
  const {
    allowRunCodeBlocks,
    resetTurnState,
    onRunCodeBlock,
    onReviewMultiFileEdits,
    onToolCallStart,
    onToolCallResult,
  } = useModeChatSidebarNativeActions({ mode, setMessages });
  const workflowLabel = useMemo(
    () => formatModeChatWorkflowLabel(workflowId),
    [workflowId],
  );

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const threadIdRef = useRef<string | null>(threadId);
  const chatModeRef = useRef<SidebarChatMode>(chatMode);
  const messagesRef = useRef(messages);
  const historyActionsRef = useRef<{
    fetchThreads: () => Promise<void>;
    hideThreadList: () => void;
  }>({
    fetchThreads: async () => {},
    hideThreadList: () => {},
  });
  messagesRef.current = messages;
  threadIdRef.current = threadId;
  chatModeRef.current = chatMode;

  const applyMode = useCallback((nextMode: SidebarChatMode) => {
    setChatMode(nextMode);
    setDetectedMode(null);
  }, []);

  const refreshThreads = useCallback(() => historyActionsRef.current.fetchThreads(), []);
  const hideThreadList = useCallback(() => historyActionsRef.current.hideThreadList(), []);

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      saveModeChatSession(workspaceId, mode, workflowId, {
        messages,
        threadId,
        chatMode,
      });
      saveTimerRef.current = null;
    }, 500);
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [chatMode, messages, mode, threadId, workflowId, workspaceId]);

  const createBranchedThread = useCallback(
    async (
      seedMessages: ChatMessage[],
      nextUserContent: string,
      lineage?: { branchType: BranchType; branchPointMessageId?: string },
    ) => {
      const nextMode = chatModeRef.current;
      const branchTitle = buildBranchedThreadTitle(threadTitle, nextUserContent);
      const parentThreadId = threadIdRef.current || undefined;

      try {
        const created = await api.createChatThread(workflowId, {
          title: branchTitle,
          mode: nextMode,
          parent_thread_id: parentThreadId,
          branch_point_message_id: lineage?.branchPointMessageId,
          branch_type: lineage?.branchType,
        });
        const nextThreadId =
          typeof created.id === "string" && created.id.trim() ? created.id : null;
        if (!nextThreadId) {
          throw new Error("Missing branched thread id");
        }

        await api.updateChatThread(workflowId, nextThreadId, {
          title: branchTitle,
          messages: seedMessages.map(toBackendMessage),
          mode: nextMode,
        });

        setThreadId(nextThreadId);
        threadIdRef.current = nextThreadId;
        setThreadTitle(getDisplayThreadTitle(branchTitle, "New Chat"));
        setMessages(seedMessages);
        setPendingQueue([]);
        setRewriteTarget(null);
        hideThreadList();
        void refreshThreads();
        requestAnimationFrame(() => textareaRef.current?.focus());
        return nextThreadId;
      } catch (error) {
        console.warn("Failed to create branched sidebar chat thread:", error);
        return null;
      }
    },
    [hideThreadList, refreshThreads, threadTitle, workflowId],
  );

  const {
    activeChannelId,
    canPushIntoCurrentTurn,
    handleStop,
    injectPendingQueueItem,
    pendingQueue,
    queueMessage,
    resetTransportState,
    sendNow,
    setPendingQueue,
    streaming,
  } = useModeChatSidebarTransport({
    mode,
    workflowId,
    workspaceId,
    contextProvider,
    chatModeRef,
    messagesRef,
    threadIdRef,
    abortRef,
    rewriteTarget,
    setMessages,
    setThreadId,
    setThreadTitle,
    setDetectedMode,
    setRewriteTarget,
    resetTurnState,
    fetchThreads: refreshThreads,
    createBranchedThread,
    onToolCallStart,
    onToolCallResult,
  });

  const clearPendingQueue = useCallback(() => {
    setPendingQueue([]);
  }, [setPendingQueue]);

  const {
    threads,
    loadingThreads,
    showThreadList,
    pendingOpenFullChat,
    setShowThreadList,
    fetchThreads,
    loadThread,
    handleNewChat,
    handleClear,
    openFullChat,
  } = useModeChatSidebarHistory({
    mode,
    workflowId,
    workspaceId,
    onClose,
    streaming,
    pendingQueueLength: pendingQueue.length,
    messagesRef,
    threadIdRef,
    chatModeRef,
    abortRef,
    saveTimerRef,
    textareaRef,
    resetTransportState,
    resetTurnState,
    clearPendingQueue,
    setMessages,
    setChatMode,
    setThreadId,
    setThreadTitle,
    setDetectedMode,
    setInput,
    setUserAttachments,
    setRewriteTarget,
    setPasteHint,
    setMentionQuery,
    setMentionAnchor,
  });
  historyActionsRef.current = {
    fetchThreads,
    hideThreadList: () => setShowThreadList(false),
  };

  // Flush any pending debounced save when the sidebar unmounts
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
        saveModeChatSession(workspaceId, mode, workflowId, {
          messages: messagesRef.current,
          threadId: threadIdRef.current,
          chatMode: chatModeRef.current,
        });
        saveTimerRef.current = null;
      }
    };
  }, [mode, workflowId, workspaceId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pendingQueue]);

  const adjustTextarea = useCallback((textarea?: HTMLTextAreaElement | null) => {
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 120)}px`;
  }, []);

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

  const checkMention = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const result = findMentionQuery(textarea.value, textarea.selectionStart);
    if (result) {
      setMentionQuery(result.query);
      const rect = textarea.getBoundingClientRect();
      setMentionAnchor({ top: rect.bottom, left: rect.left });
      return;
    }
    setMentionQuery(null);
    setMentionAnchor(null);
  }, []);

  const handleMentionSelect = useCallback((mention: {
    type: string;
    id: string;
    name: string;
  }) => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const mentionRef: MentionRef = {
      name: mention.name,
      type: mention.type as MentionRef["type"],
      id: mention.id,
    };
    const { newText, newCursorPos } = insertMention(
      textarea.value,
      textarea.selectionStart,
      mentionRef,
    );
    setInput(newText);
    setMentionQuery(null);
    setMentionAnchor(null);
    requestAnimationFrame(() => {
      adjustTextarea(textarea);
      textarea.focus();
      textarea.setSelectionRange(newCursorPos, newCursorPos);
    });
  }, [adjustTextarea]);

  const dismissMention = useCallback(() => {
    setMentionQuery(null);
    setMentionAnchor(null);
  }, []);

  const handleInputChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      setInput(e.target.value);
      setPasteHint(null);
      adjustTextarea(e.target);
      requestAnimationFrame(checkMention);
    },
    [adjustTextarea, checkMention],
  );

  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData.files ?? []);
    if (files.length > 0) {
      e.preventDefault();
      setPasteHint(null);
      setUserAttachments((prev) => [...prev, ...files.map(fileToAttachmentDraft)]);
      return;
    }

    const text = e.clipboardData.getData("text/plain");
    const html = e.clipboardData.getData("text/html");

    const codeCtx = resolveClipboardCodeContext(text, html || undefined);
    if (codeCtx) {
      e.preventDefault();
      const formatted = formatCodeContextForChat(codeCtx);
      setInput((prev) => {
        const before = prev;
        return before ? `${before}\n${formatted}` : formatted;
      });
      setPasteHint(null);
      requestAnimationFrame(() => adjustTextarea(textareaRef.current));
      return;
    }

    if (/^https?:\/\/\S+$/.test(text.trim())) {
      setPasteHint({ type: "url", value: text.trim() });
      return;
    }

    if (
      text.includes("\n") &&
      (text.includes("function") ||
        text.includes("class") ||
        text.includes("def ") ||
        text.includes("import "))
    ) {
      setPasteHint({ type: "code", value: text });
    }
  }, [adjustTextarea]);

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

  const handleCopyMessage = useCallback((message: ChatMessage) => {
    const role = message.role.charAt(0).toUpperCase() + message.role.slice(1);
    const markdown = `### ${role}\n\n${message.content}`;
    navigator.clipboard.writeText(markdown).catch(() => {});
  }, []);

  const processLocalCommand = useCallback(
    (
      rawText: string,
      attachments: ComposerAttachmentDraft[],
    ): { handled: boolean; messageText: string; modeOverride?: SidebarChatMode } => {
      const parsed = parseLeadingCommand(rawText);
      if (!parsed) return { handled: false, messageText: rawText };

      const modeMap: Record<string, SidebarChatMode | undefined> = {
        "/ask": "ask",
        "/agent": "agent",
        "/plan": "plan",
        "/debug": "debug",
        "/auto": "auto",
      };

      const nextMode = modeMap[parsed.command];
      if (nextMode) {
        trackCommand(parsed.command);
        applyMode(nextMode);
        if (!parsed.remainder) {
          return { handled: true, messageText: "", modeOverride: nextMode };
        }
        return {
          handled: false,
          messageText: parsed.remainder,
          modeOverride: nextMode,
        };
      }

      if (parsed.command === "/clear" && !parsed.remainder && attachments.length === 0) {
        trackCommand(parsed.command);
        handleClear();
        return { handled: true, messageText: "" };
      }

      if (parsed.command === "/chat" && !parsed.remainder && attachments.length === 0) {
        trackCommand(parsed.command);
        openFullChat();
        return { handled: true, messageText: "" };
      }

      return { handled: false, messageText: rawText };
    },
    [applyMode, handleClear, openFullChat],
  );

  const submitComposer = useCallback(() => {
    const processed = processLocalCommand(input, userAttachments);
    if (processed.handled) {
      if (input.trim().startsWith("/")) {
        setInput("");
        requestAnimationFrame(() => adjustTextarea(textareaRef.current));
      }
      return;
    }
    const nextText = processed.messageText;
    const nextAttachments = userAttachments.map(cloneAttachmentDraft);
    if (!nextText.trim() && nextAttachments.length === 0) return;

    if (streaming) {
      queueMessage(nextText, nextAttachments, processed.modeOverride);
    } else {
      void sendNow(nextText, nextAttachments, processed.modeOverride);
    }

    setInput("");
    setUserAttachments([]);
    setPasteHint(null);
    dismissMention();
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      requestAnimationFrame(() => textareaRef.current?.focus());
    }
  }, [
    dismissMention,
    input,
    adjustTextarea,
    processLocalCommand,
    queueMessage,
    sendNow,
    streaming,
    userAttachments,
  ]);

  const handleInjectedSend = useCallback(
    (text: string) => {
      const processed = processLocalCommand(text, []);
      if (processed.handled) return;
      const nextText = processed.messageText;
      if (!nextText.trim()) return;
      if (streaming) {
        queueMessage(nextText, [], processed.modeOverride);
        return;
      }
      void sendNow(nextText, [], processed.modeOverride);
    },
    [processLocalCommand, queueMessage, sendNow, streaming],
  );

  const handleEditAndResend = useCallback((message: ChatMessage) => {
    const target = getEditBranchTarget(messagesRef.current, message.id);
    if (!target) return;
    setRewriteTarget({
      sourceMessageId: message.id,
      historyBefore: target.historyBefore,
      branchType: "edit",
    });
    setInput(target.content);
    setUserAttachments(target.attachments);
    requestAnimationFrame(() => {
      adjustTextarea(textareaRef.current);
      textareaRef.current?.focus();
    });
  }, [adjustTextarea]);

  const handleRegenerate = useCallback(
    async (message: ChatMessage) => {
      const target = getRegenerateBranchTarget(messagesRef.current, message.id);
      if (!target) return;
      const branchedThreadId = await createBranchedThread(
        target.historyBefore,
        target.content,
        { branchType: "regenerate", branchPointMessageId: message.id },
      );
      if (!branchedThreadId) return;
      await sendNow(
        target.content,
        target.attachments,
        undefined,
        target.historyBefore,
        branchedThreadId,
      );
    },
    [createBranchedThread, sendNow],
  );

  const handleExploreFromHere = useCallback((message: ChatMessage) => {
    const target = getExploreBranchTarget(messagesRef.current, message.id);
    if (!target) return;
    setRewriteTarget({
      sourceMessageId: message.id,
      historyBefore: target.historyUpToHere,
      branchType: "explore",
    });
    setInput("");
    setUserAttachments([]);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (mentionQuery !== null) {
        if (["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(e.key)) return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitComposer();
      }
    },
    [submitComposer, mentionQuery],
  );

  useEffect(() => {
    registerModeChatSender(mode, handleInjectedSend);
    registerModeChatAttachmentReceiver(mode, appendAttachment);
    return () => {
      chatSenders.delete(mode);
      chatAttachmentReceivers.delete(mode);
    };
  }, [appendAttachment, handleInjectedSend, mode]);

  const modeSelector = (
    <div className="shrink-0 border-b border-gray-200 bg-gray-50/80 px-3 py-1.5 dark:border-gray-800 dark:bg-gray-900/40">
      <div className="flex flex-wrap items-center gap-1">
        {(["auto", "agent", "ask", "plan", "debug"] as const).map((entry) => {
          const config = MODE_CONFIG[entry];
          const Icon = config.icon;
          const active = chatMode === entry;
          return (
            <button
              key={entry}
              onClick={() => applyMode(entry)}
              className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors ${
                active
                  ? "border border-gray-200 bg-white text-gray-900 shadow-sm dark:border-gray-700 dark:bg-white/10 dark:text-gray-100"
                  : "text-gray-500 hover:bg-white hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-gray-200"
              }`}
            >
              <Icon size={11} />
              {config.label}
              {entry === "auto" && active && detectedMode && (
                <span className="text-[9px] font-normal text-violet-600 dark:text-violet-300">
                  -&gt; {MODE_CONFIG[detectedMode].label}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );

  return (
    <div
      className={`flex h-full w-full flex-col bg-white text-gray-800 dark:bg-gray-900 dark:text-gray-300 ${
        isDragOver ? "ring-2 ring-blue-500/50 ring-inset" : ""
      }`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-gray-200 px-3 py-2 dark:border-gray-800">
        <div className="min-w-0 flex-1">
          <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
            AI Chat
          </span>
          {pendingOpenFullChat ? (
            <div className="truncate text-[10px] text-blue-500 dark:text-blue-400">
              Opening full Chat after the active response queue finishes
            </div>
          ) : showThreadList ? (
            <div className="truncate text-[10px] text-gray-400 dark:text-gray-500">
              History for {workflowLabel}
            </div>
          ) : threadId ? (
            <div className="truncate text-[10px] text-gray-400 dark:text-gray-500">
              {workflowLabel} · {getDisplayThreadTitle(threadTitle, "Scratch chat")}
            </div>
          ) : (
            <div className="truncate text-[10px] text-gray-400 dark:text-gray-500">
              {sidebarCopy.surfaceLabel} · {workflowLabel}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setShowThreadList((prev) => !prev)}
            disabled={streaming}
            title={showThreadList ? "Back to conversation" : "Chat history"}
            className="rounded p-1 text-gray-700 transition-colors hover:text-indigo-600 disabled:cursor-not-allowed disabled:opacity-40 dark:text-gray-300 dark:hover:text-indigo-400"
          >
            {showThreadList ? <ArrowLeft size={14} /> : <History size={14} />}
          </button>
          <button
            onClick={handleNewChat}
            disabled={streaming}
            title="New chat"
            className="rounded p-1 text-gray-700 transition-colors hover:text-indigo-600 disabled:cursor-not-allowed disabled:opacity-40 dark:text-gray-300 dark:hover:text-indigo-400"
          >
            <Plus size={14} />
          </button>
          <button
            onClick={openFullChat}
            title={
              streaming || abortRef.current || pendingQueue.length > 0
                ? "Open full Chat after the active response queue finishes"
                : "Open full Chat mode"
            }
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

      {modeSelector}

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-3 py-3">
        {showThreadList ? (
          <div className="space-y-2">
            <button
              onClick={handleNewChat}
              disabled={streaming}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <Plus size={13} />
              New chat
            </button>
            {loadingThreads ? (
              <div className="flex items-center justify-center py-8 text-xs text-gray-500 dark:text-gray-400">
                <Loader2 size={14} className="mr-2 animate-spin" />
                Loading chat history…
              </div>
            ) : threads.length === 0 ? (
              <div className="rounded-lg border border-dashed border-gray-200 px-3 py-6 text-center text-xs text-gray-500 dark:border-gray-700 dark:text-gray-400">
                No saved chats yet for {workflowLabel}.
              </div>
            ) : (
              <div className="space-y-1">
                {threads.map((thread) => (
                  <button
                    key={thread.id}
                    onClick={() => void loadThread(thread.id)}
                    disabled={streaming}
                    className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left transition-colors ${
                      thread.id === threadId
                        ? "border-indigo-200 bg-indigo-50 text-indigo-900 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-100"
                        : "border-gray-200 bg-white hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:hover:bg-gray-700/70"
                    } disabled:cursor-not-allowed disabled:opacity-50`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-medium">
                        {getDisplayThreadTitle(thread.title, "Untitled chat")}
                      </div>
                      <div className="mt-0.5 text-[10px] text-gray-500 dark:text-gray-400">
                        {relativeTimeShort(thread.updated_at)} · {thread.message_count} msgs
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : messages.length === 0 ? (
          <div className="flex h-full select-none flex-col items-center justify-center gap-2 px-2 text-center text-xs text-gray-500 dark:text-gray-600">
            <Sparkles size={24} className="text-gray-400 dark:text-gray-700" />
            <span>{sidebarCopy.emptyPrimary}</span>
            <span className="text-[10px] text-gray-400 dark:text-gray-600">
              Working inside {workflowLabel}.
            </span>
            <button
              onClick={openFullChat}
              className="mt-1 text-[10px] text-gray-500 transition-colors hover:text-blue-600 dark:hover:text-blue-400"
            >
              Open full Chat for the larger workspace view
            </button>
            <span className="text-[10px] text-gray-400 dark:text-gray-600">
              {sidebarCopy.emptySecondary}
            </span>
          </div>
        ) : (
          <div className="space-y-0.5">
            {messages.map((message, index) => (
              <ChatMessageBubble
                key={message.id}
                message={message}
                isStreaming={
                  streaming &&
                  index === messages.length - 1 &&
                  message.role === "assistant"
                }
                onEditAndResend={
                  message.role === "user" ? () => handleEditAndResend(message) : undefined
                }
                onRegenerate={
                  message.role === "assistant"
                    ? () => void handleRegenerate(message)
                    : undefined
                }
                onExploreFromHere={
                  message.role === "assistant"
                    ? () => handleExploreFromHere(message)
                    : undefined
                }
                disableHistoryActions={streaming}
                onCopyMarkdown={
                  message.content ? () => handleCopyMessage(message) : undefined
                }
                allowRunCodeBlocks={allowRunCodeBlocks}
                onRunCodeBlock={onRunCodeBlock}
                onReviewMultiFileEdits={onReviewMultiFileEdits}
              />
            ))}
          </div>
        )}
      </div>

      {pendingQueue.length > 0 && (
        <div className="max-h-36 shrink-0 overflow-y-auto border-t border-gray-200 bg-gray-50/60 px-3 py-2 dark:border-gray-800 dark:bg-gray-900/40">
          <div className="mb-1 px-1 text-[10px] font-medium text-gray-500 dark:text-gray-400">
            Queued messages ({pendingQueue.length})
          </div>
          <div className="space-y-1">
            {pendingQueue.map((item, index) => (
              <div
                key={item.id}
                className="group flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-800"
              >
                <GripVertical size={12} className="shrink-0 text-gray-400" />
                <span className="min-w-0 flex-1 truncate text-xs text-gray-700 dark:text-gray-200">
                  {describeQueuedItem(item)}
                </span>
                {item.attachments.length > 0 && (
                  <span className="shrink-0 rounded-full border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-600 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-300">
                    {item.attachments.length === 1
                      ? "1 attachment"
                      : `${item.attachments.length} attachments`}
                  </span>
                )}
                {item.mode !== "auto" && (
                  <span className="shrink-0 rounded-full border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] text-gray-500 dark:border-gray-700 dark:bg-gray-900/60 dark:text-gray-300">
                    {MODE_CONFIG[item.mode].label}
                  </span>
                )}
                <div className="flex shrink-0 items-center gap-0.5">
                  {index === 0 && canPushIntoCurrentTurn(item) && (
                    <button
                      onClick={() => void injectPendingQueueItem(item)}
                      className="rounded bg-indigo-50 px-1.5 py-0.5 text-xs font-medium text-indigo-600 transition-colors hover:bg-indigo-100 hover:text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300 dark:hover:bg-indigo-500/20"
                      title="Inject into the current turn"
                    >
                      Push
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setPendingQueue((prev) =>
                        prev.filter((entry) => entry.id !== item.id),
                      );
                      setInput(item.content);
                      setUserAttachments(item.attachments.map(cloneAttachmentDraft));
                      applyMode(item.mode);
                      requestAnimationFrame(() => {
                        adjustTextarea(textareaRef.current);
                        textareaRef.current?.focus();
                      });
                    }}
                    className="rounded p-0.5 text-gray-500 transition-colors hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-300"
                    title="Edit queued message"
                  >
                    <PencilLine size={11} />
                  </button>
                  {index > 0 && (
                    <button
                      onClick={() => {
                        setPendingQueue((prev) => {
                          const next = [...prev];
                          [next[index - 1], next[index]] = [
                            next[index],
                            next[index - 1],
                          ];
                          return next;
                        });
                      }}
                      className="rounded p-0.5 text-gray-500 transition-colors hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-300"
                      title="Move up in queue"
                    >
                      <ArrowUp size={11} />
                    </button>
                  )}
                  <button
                    onClick={() =>
                      setPendingQueue((prev) =>
                        prev.filter((entry) => entry.id !== item.id),
                      )
                    }
                    className="rounded p-0.5 text-gray-500 transition-colors hover:text-red-500 dark:text-gray-400"
                    title="Remove queued message"
                  >
                    <X size={11} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="shrink-0 border-t border-gray-200 px-3 py-2 dark:border-gray-800">
        {rewriteTarget && (
          <div className="mb-2 flex items-start gap-3 rounded-xl border border-indigo-200 bg-indigo-50/80 px-3 py-2 text-xs text-indigo-700 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-200">
            <span className="flex-1">
              {rewriteTarget.branchType === "explore"
                ? "Exploring from a previous result. Sending will start a new branched thread from that point."
                : "Editing an earlier turn. Sending will create a new branched thread and keep the current thread unchanged."}
            </span>
            <button
              onClick={() => setRewriteTarget(null)}
              className="rounded p-0.5 text-indigo-500 transition-colors hover:text-indigo-700 dark:text-indigo-300 dark:hover:text-indigo-200"
              title="Cancel branch edit"
            >
              <X size={12} />
            </button>
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileSelection}
        />

        {isComposerFocused && !streaming && (
          <RecentCommandsBar
            onSelect={(command) => {
              setInput(command);
              requestAnimationFrame(() => {
                adjustTextarea(textareaRef.current);
                textareaRef.current?.focus();
              });
            }}
          />
        )}

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
                <span className="max-w-[180px] truncate">
                  {resolveAttachmentName(attachment.name, attachment.mimeType)}
                </span>
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

        {input.startsWith("/") && input.length < 20 && (
          <SlashCommandPopup
            filter={input}
            onSelect={(command) => {
              setInput(command + " ");
              requestAnimationFrame(() => {
                adjustTextarea(textareaRef.current);
                textareaRef.current?.focus();
              });
            }}
          />
        )}

        <div className="flex items-end gap-2 rounded-xl border border-gray-300 bg-white px-3 py-2 shadow-sm focus-within:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:focus-within:border-gray-600">
          <button
            onClick={() => fileInputRef.current?.click()}
            title="Append files"
            className="shrink-0 rounded-lg border border-gray-300 bg-white p-2 text-gray-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:border-gray-600"
          >
            <Paperclip size={16} />
          </button>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            onKeyUp={checkMention}
            onClick={checkMention}
            onPaste={handlePaste}
            onFocus={() => setIsComposerFocused(true)}
            onBlur={() => setIsComposerFocused(false)}
            placeholder={
              streaming
                ? "Type to queue next message..."
                : chatMode === "ask"
                ? "Ask about this workspace..."
                : chatMode === "plan"
                ? "Describe what to plan..."
                : chatMode === "debug"
                ? "Describe the problem to diagnose..."
                : chatMode === "auto"
                ? "Message DAN... (@ to mention, / for commands)"
                : "Ask DAN to work on this..."
            }
            rows={1}
            className="min-h-[28px] max-h-[120px] flex-1 resize-none bg-transparent text-[13px] leading-snug text-gray-900 outline-none placeholder-gray-500 dark:text-white dark:placeholder:text-gray-500"
          />
          {mentionQuery !== null && (
            <MentionAutocomplete
              query={mentionQuery}
              anchorRect={mentionAnchor}
              onSelect={handleMentionSelect}
              onDismiss={dismissMention}
            />
          )}
          {streaming ? (
            <div className="flex shrink-0 items-center gap-1">
              {(input.trim() || userAttachments.length > 0) && (
                <button
                  onClick={submitComposer}
                  className="rounded p-0.5 text-indigo-500 transition-colors hover:text-indigo-700 dark:hover:text-indigo-300"
                  title="Queue message"
                >
                  <ArrowUp size={16} />
                </button>
              )}
              {activeChannelId ? (
                <button
                  onClick={handleStop}
                  className="rounded p-0.5 text-red-500 transition-colors hover:text-red-700"
                  title="Stop generation"
                >
                  <Square size={16} />
                </button>
              ) : (
                <Loader2 size={16} className="animate-spin text-gray-400" />
              )}
            </div>
          ) : (
            <button
              onClick={submitComposer}
              disabled={!input.trim() && userAttachments.length === 0}
              className="shrink-0 rounded-lg bg-blue-600 p-2 text-white transition-colors hover:bg-blue-500 disabled:bg-gray-300 disabled:text-gray-500 dark:disabled:bg-gray-700"
            >
              <Send size={16} />
            </button>
          )}
        </div>

        <div className="mt-1.5 px-1 text-[10px] text-gray-400 dark:text-gray-500">
          {streaming
            ? "Enter to queue - Shift+Enter for newline"
            : "Enter to send - Shift+Enter for newline"}
        </div>

        {pasteHint && (
          <SmartPasteHint
            hint={pasteHint}
            onAccept={() => {
              if (pasteHint.type === "url") {
                setInput((prev) =>
                  prev.replace(
                    pasteHint.value,
                    `Fetch and summarize: ${pasteHint.value}`,
                  ),
                );
              } else {
                setInput((prev) =>
                  prev.replace(pasteHint.value, `\`\`\`\n${pasteHint.value}\n\`\`\``),
                );
              }
              setPasteHint(null);
              requestAnimationFrame(() => adjustTextarea(textareaRef.current));
            }}
            onDismiss={() => setPasteHint(null)}
          />
        )}
      </div>
    </div>
  );
}

export default function ModeChatSidebar(props: ModeChatSidebarProps) {
  const graphWorkflowId = useGraphStore((state) => state.graphId);
  const activeChatWorkflowId = useAppStore((state) => state.activeChatWorkflowId);
  const workspaceId = useWorkspaceStore((state) => state.activeWorkspaceId);
  const workflowId = resolveModeChatWorkflowId(
    graphWorkflowId,
    activeChatWorkflowId,
  );
  const sidebarCopy = MODE_SIDECAR_COPY[props.mode] ?? MODE_SIDECAR_COPY.chat;
  const scopeKey = buildModeChatScopeKey(workspaceId, props.mode, workflowId);

  return (
    <ModeChatSidebarInner
      key={scopeKey}
      {...props}
      workflowId={workflowId}
      workspaceId={workspaceId}
      sidebarCopy={sidebarCopy}
    />
  );
}
