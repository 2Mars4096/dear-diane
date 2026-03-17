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
  Paperclip,
  FileText,
  ImageIcon,
  Square,
  ArrowUp,
  GripVertical,
  PencilLine,
  HelpCircle,
  Bug,
} from "lucide-react";
import { useAppStore, type AppMode } from "../../store/useAppStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import { nativeTerminal } from "../../lib/electronBridge";
import { useCodeStore } from "../../store/useCodeStore";
import type { ChatMessage } from "../../types/chat";
import * as api from "../../lib/api";
import {
  type ComposerAttachmentDraft,
  cloneAttachmentDraft,
  type EditorChatMode,
  fileToAttachmentDraft,
  normalizeAttachmentDrafts,
  resolveAttachmentName,
  sanitizeChatHistory,
  startEditorChat,
  streamEditorChatResponse,
} from "../../lib/editorChat";
import ChatMessageBubble from "../ChatMessage";
import MentionAutocomplete from "../MentionAutocomplete";
import {
  findMentionQuery,
  insertMention,
  parseMentions,
  type MentionRef,
} from "../../lib/mentionParser";
import {
  safeTokenUsage,
  toBackendMessage,
} from "../../lib/chatMessagePersistence";
import { deriveDraftThreadTitleFromMessage } from "../../lib/chatThreadTitle";
import { describeLatestToolProgress } from "../../lib/toolCallPresentation";

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

type SidebarChatMode = Exclude<EditorChatMode, "conversation">;

interface PendingQueueItem {
  id: string;
  content: string;
  timestamp: number;
  attachments: ComposerAttachmentDraft[];
  mode: SidebarChatMode;
  mentions: Array<{ type: string; identifier: string }>;
}

interface StoredModeChatSession {
  messages: ChatMessage[];
  threadId: string | null;
  chatMode: SidebarChatMode;
}

/* ------------------------------------------------------------------ */
/*  Persistence                                                        */
/* ------------------------------------------------------------------ */

function chatStorageKey(workspaceId: string, mode: AppMode): string {
  return `dan-chat-${workspaceId}-${mode}`;
}

function defaultStoredSession(): StoredModeChatSession {
  return {
    messages: [],
    threadId: null,
    chatMode: "auto",
  };
}

function loadChatSession(
  workspaceId: string | null,
  mode: AppMode,
): StoredModeChatSession {
  if (!workspaceId) return defaultStoredSession();
  try {
    const raw = localStorage.getItem(chatStorageKey(workspaceId, mode));
    if (!raw) return defaultStoredSession();
    const parsed = JSON.parse(raw);

    // Legacy format stored the message array directly.
    if (Array.isArray(parsed)) {
      return {
        ...defaultStoredSession(),
        messages: parsed as ChatMessage[],
      };
    }

    if (!parsed || typeof parsed !== "object") {
      return defaultStoredSession();
    }

    return {
      messages: Array.isArray((parsed as { messages?: unknown }).messages)
        ? ((parsed as { messages: ChatMessage[] }).messages ?? [])
        : [],
      threadId:
        typeof (parsed as { threadId?: unknown }).threadId === "string"
          ? ((parsed as { threadId: string }).threadId ?? null)
          : null,
      chatMode:
        typeof (parsed as { chatMode?: unknown }).chatMode === "string"
          ? (((parsed as { chatMode: SidebarChatMode }).chatMode ?? "auto") as SidebarChatMode)
          : "auto",
    };
  } catch {
    return defaultStoredSession();
  }
}

const MAX_PERSISTED_MESSAGES = 200;

function saveChatSession(
  workspaceId: string | null,
  mode: AppMode,
  session: StoredModeChatSession,
) {
  if (!workspaceId) return;
  try {
    localStorage.setItem(
      chatStorageKey(workspaceId, mode),
      JSON.stringify({
        ...session,
        messages: session.messages.slice(-MAX_PERSISTED_MESSAGES),
      }),
    );
  } catch { /* quota */ }
}

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

function collectStructuredMentions(text: string): Array<{ type: string; identifier: string }> {
  return parseMentions(text).segments
    .filter((segment) => segment.type === "mention")
    .map((segment) => ({
      type: (segment as { type: "mention"; mention: MentionRef }).mention.type,
      identifier: (segment as { type: "mention"; mention: MentionRef }).mention.id,
    }));
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

export default function ModeChatSidebar({ mode, onClose, contextProvider }: ModeChatSidebarProps) {
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const initialSessionRef = useRef<StoredModeChatSession>(
    loadChatSession(workspaceId, mode),
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
  const [detectedMode, setDetectedMode] = useState<SidebarChatMode | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [userAttachments, setUserAttachments] = useState<ComposerAttachmentDraft[]>([]);
  const [pendingQueue, setPendingQueue] = useState<PendingQueueItem[]>([]);
  const [pendingOpenFullChat, setPendingOpenFullChat] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const [activeChannelId, setActiveChannelId] = useState<string | null>(null);
  const [isComposerFocused, setIsComposerFocused] = useState(false);
  const [pasteHint, setPasteHint] = useState<
    { type: "url" | "code"; value: string } | null
  >(null);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionAnchor, setMentionAnchor] = useState<{
    top: number;
    left: number;
  } | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const threadIdRef = useRef<string | null>(threadId);
  const activeChannelIdRef = useRef<string | null>(activeChannelId);
  const chatModeRef = useRef<SidebarChatMode>(chatMode);
  const activeRequestModeRef = useRef<SidebarChatMode | null>(null);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  threadIdRef.current = threadId;
  activeChannelIdRef.current = activeChannelId;
  chatModeRef.current = chatMode;

  const applyMode = useCallback((nextMode: SidebarChatMode) => {
    setChatMode(nextMode);
    setDetectedMode(null);
  }, []);

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      saveChatSession(workspaceId, mode, {
        messages,
        threadId,
        chatMode,
      });
      saveTimerRef.current = null;
    }, 500);
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [messages, workspaceId, mode, threadId, chatMode]);

  // Flush any pending debounced save when the sidebar unmounts
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
        const wsId = useWorkspaceStore.getState().activeWorkspaceId;
        saveChatSession(wsId, mode, {
          messages: messagesRef.current,
          threadId: threadIdRef.current,
          chatMode,
        });
        saveTimerRef.current = null;
      }
    };
  }, [chatMode, mode]);

  useEffect(() => {
    const session = loadChatSession(workspaceId, mode);
    abortRef.current?.abort();
    setMessages(session.messages);
    setChatMode(session.chatMode);
    setThreadId(session.threadId);
    threadIdRef.current = session.threadId;
    setDetectedMode(null);
    setInput("");
    setStreaming(false);
    setPendingQueue([]);
    setPendingOpenFullChat(false);
    setUserAttachments([]);
    setActiveChannelId(null);
    activeChannelIdRef.current = null;
    activeRequestModeRef.current = null;
    setPasteHint(null);
    setMentionQuery(null);
    setMentionAnchor(null);
  }, [workspaceId, mode]);

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

  const persistThreadSnapshot = useCallback(async (options?: { forceCreate?: boolean }) => {
    const snapshot = messagesRef.current;
    const currentMode = chatModeRef.current;
    const existingThreadId = threadIdRef.current;
    const forceCreate = options?.forceCreate ?? false;

    if (snapshot.length === 0) {
      if (existingThreadId) {
        try {
          await api.updateChatThread("_scratch", existingThreadId, {
            mode: currentMode,
          });
        } catch {
          /* ignore */
        }
      }
      if (!forceCreate) {
        return existingThreadId;
      }
      const created = await api.createChatThread("_scratch", {
        title: "New Chat",
        mode: currentMode,
      });
      const nextThreadId =
        typeof created.id === "string" && created.id.trim() ? created.id : null;
      if (!nextThreadId) return existingThreadId;
      setThreadId(nextThreadId);
      threadIdRef.current = nextThreadId;
      return nextThreadId;
    }

    const payload = {
      messages: snapshot.map(toBackendMessage),
      mode: currentMode,
    };

    if (existingThreadId) {
      try {
        await api.updateChatThread("_scratch", existingThreadId, payload);
        return existingThreadId;
      } catch (error) {
        console.warn("Failed to sync mode chat thread, creating a fresh one:", error);
      }
    }

    const firstUserMessage =
      snapshot.find((message) => message.role === "user" && message.content.trim())?.content ??
      "New Chat";
    const created = await api.createChatThread("_scratch", {
      title: deriveDraftThreadTitleFromMessage(firstUserMessage),
      mode: currentMode,
    });
    const nextThreadId =
      typeof created.id === "string" && created.id.trim() ? created.id : null;
    if (!nextThreadId) return null;

    await api.updateChatThread("_scratch", nextThreadId, payload);
    setThreadId(nextThreadId);
    threadIdRef.current = nextThreadId;
    return nextThreadId;
  }, []);

  const completeOpenFullChat = useCallback(async () => {
    let targetThreadId = threadIdRef.current;
    try {
      targetThreadId =
        (await persistThreadSnapshot({ forceCreate: true })) ?? targetThreadId;
    } catch (error) {
      console.warn("Failed to prepare sidebar thread for full chat handoff:", error);
    }

    if (targetThreadId) {
      useWorkspaceStore.getState().setActiveThread(targetThreadId);
      useAppStore.getState().setActiveChatThread(targetThreadId, "_scratch");
    } else {
      useAppStore.getState().setActiveChatThread(null, "_scratch");
    }
    useAppStore.getState().setMode("chat");
    onClose();
  }, [onClose, persistThreadSnapshot]);

  const openFullChat = useCallback(() => {
    if (streaming || abortRef.current || pendingQueue.length > 0) {
      setPendingOpenFullChat(true);
      return;
    }
    void completeOpenFullChat();
  }, [completeOpenFullChat, pendingQueue.length, streaming]);

  const handleClear = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    setMessages([]);
    messagesRef.current = [];
    setUserAttachments([]);
    setPendingQueue([]);
    setPendingOpenFullChat(false);
    setInput("");
    setStreaming(false);
    setThreadId(null);
    threadIdRef.current = null;
    setActiveChannelId(null);
    activeChannelIdRef.current = null;
    activeRequestModeRef.current = null;
    setDetectedMode(null);
    setPasteHint(null);
    setMentionQuery(null);
    setMentionAnchor(null);
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    if (workspaceId) {
      try {
        localStorage.removeItem(chatStorageKey(workspaceId, mode));
      } catch {
        /* ignore */
      }
    }
  }, [workspaceId, mode]);

  const handleCopyMessage = useCallback((message: ChatMessage) => {
    const role = message.role.charAt(0).toUpperCase() + message.role.slice(1);
    const markdown = `### ${role}\n\n${message.content}`;
    navigator.clipboard.writeText(markdown).catch(() => {});
  }, []);

  const finishStream = useCallback(() => {
    setStreaming(false);
    setActiveChannelId(null);
    activeChannelIdRef.current = null;
    activeRequestModeRef.current = null;
    abortRef.current = null;
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

  const queueMessage = useCallback(
    (
      text: string,
      attachments: ComposerAttachmentDraft[],
      modeOverride?: SidebarChatMode,
    ) => {
      const trimmed = text.trim();
      if (!trimmed && attachments.length === 0) return;
      setPendingQueue((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          content: trimmed,
          timestamp: Date.now(),
          attachments: attachments.map(cloneAttachmentDraft),
          mode: modeOverride ?? chatModeRef.current,
          mentions: collectStructuredMentions(trimmed),
        },
      ]);
    },
    [],
  );

  const sendNow = useCallback(
    async (
      rawText: string,
      attachmentDrafts: ComposerAttachmentDraft[],
      modeOverride?: SidebarChatMode,
    ) => {
      const trimmed = rawText.trim();
      if (!trimmed && attachmentDrafts.length === 0) return;
      const effectiveMode = modeOverride ?? chatModeRef.current;

      if (trimmed.startsWith("/")) {
        const parsed = parseLeadingCommand(trimmed);
        if (parsed) trackCommand(parsed.command);
      }

      const attachments = await normalizeAttachmentDrafts(attachmentDrafts);
      const ctx = contextProvider?.() ?? "";
      const fullMessage = ctx ? (trimmed ? `${ctx}\n\n${trimmed}` : ctx) : trimmed;
      const structuredMentions = collectStructuredMentions(trimmed);

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content:
          trimmed ||
          (attachments.length === 1
            ? `Attached ${resolveAttachmentName(
                attachments[0].name,
                attachments[0].mimeType,
              )}`
            : `Attached ${attachments.length} items`),
        timestamp: Date.now(),
        attachments:
          attachments.length > 0
            ? attachments.map((attachment) => ({
                path: attachment.path ?? attachment.source ?? attachment.name,
                filename: attachment.name,
                size: attachment.size,
                mimeType: attachment.mimeType,
                kind: attachment.kind,
                caption: attachment.caption,
                source: attachment.source,
              }))
            : undefined,
      };

      const assistantId = crypto.randomUUID();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);
      setDetectedMode(null);

      const controller = new AbortController();
      abortRef.current = controller;
      activeRequestModeRef.current = effectiveMode;

      try {
        const { threadId: nextThreadId, response } = await startEditorChat({
          message: fullMessage,
          history: sanitizeChatHistory(
            messagesRef.current.flatMap((message) =>
              message.role === "user" || message.role === "assistant"
                ? [{ role: message.role, content: message.content }]
                : [],
            ),
          ),
          threadId: threadIdRef.current,
          mode: effectiveMode,
          scope: `mode-chat:${mode}`,
          attachments,
          mentions: structuredMentions,
          signal: controller.signal,
          surfaceContext: {
            mode,
            workspace_id: workspaceId,
          },
        });
        setThreadId(nextThreadId);
        threadIdRef.current = nextThreadId;
        const nextChatChannel =
          typeof response.stream_channel_id === "string" &&
          response.stream_channel_id.startsWith("chat-")
            ? response.stream_channel_id
            : null;
        setActiveChannelId(nextChatChannel);
        activeChannelIdRef.current = nextChatChannel;
        if (
          response.type === "run_started" &&
          typeof response.run_id === "string"
        ) {
          const runId = response.run_id;
          const runScope = response.scope ?? "full";
          setMessages((prev) =>
            prev.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    runRef: {
                      runId,
                      scope: runScope,
                      status: "running",
                    },
                  }
                : message,
            ),
          );
        }

        const ws = streamEditorChatResponse(response, {
          onQueued: (position) => {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId
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
                message.id === assistantId
                  ? {
                      ...message,
                      content,
                      progressStatus: undefined,
                      progressFilePath: undefined,
                    }
                  : message,
              ),
            );
          },
          onToolCallStart: (toolCall) => {
            setMessages((prev) =>
              prev.map((message) => {
                if (message.id !== assistantId) return message;
                const nextToolCalls = [
                  ...(message.toolCalls ?? []),
                  {
                    id: toolCall.id,
                    toolName: toolCall.toolName,
                    argsPreview: toolCall.argsPreview,
                    status: "running" as const,
                  },
                ];
                const progress = describeLatestToolProgress(nextToolCalls);
                return {
                  ...message,
                  toolCalls: nextToolCalls,
                  progressStatus: progress?.text,
                  progressFilePath: progress?.filePath,
                };
              }),
            );
          },
          onToolCallResult: (toolCall) => {
            setMessages((prev) =>
              prev.map((message) => {
                if (message.id !== assistantId) return message;
                const nextStatus =
                  toolCall.status === "running"
                    ? ("running" as const)
                    : toolCall.status === "error"
                      ? ("error" as const)
                      : ("success" as const);
                const nextToolCalls = (message.toolCalls ?? []).map((existing) =>
                  existing.id === toolCall.id
                    ? {
                        ...existing,
                        status: nextStatus,
                        outputPreview: toolCall.outputPreview,
                        durationMs: toolCall.durationMs,
                      }
                    : existing,
                );
                const progress = describeLatestToolProgress(nextToolCalls);
                return {
                  ...message,
                  toolCalls: nextToolCalls,
                  progressStatus: progress?.text,
                  progressFilePath: progress?.filePath,
                };
              }),
            );
          },
          onRunEvent: (runEvent) => {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      runEvents: [...(message.runEvents ?? []), runEvent],
                      runRef: (() => {
                        const detail = runEvent.detail ?? {};
                        const detailRunId =
                          typeof detail.run_id === "string" ? detail.run_id : null;
                        const detailScope =
                          typeof detail.scope === "string" ? detail.scope : "full";
                        const terminalStatus =
                          runEvent.event_type === "run_completed"
                            ? "completed"
                            : runEvent.event_type === "run_failed"
                              ? "failed"
                              : runEvent.event_type === "run_cancelled"
                                ? "cancelled"
                                : null;
                        if (message.runRef) {
                          return terminalStatus
                            ? { ...message.runRef, status: terminalStatus }
                            : message.runRef;
                        }
                        if (!detailRunId) return message.runRef ?? null;
                        const nextRunRef: ChatMessage["runRef"] = {
                          runId: detailRunId,
                          scope: detailScope,
                          status: terminalStatus ?? "running",
                        };
                        return nextRunRef;
                      })(),
                    }
                  : message,
              ),
            );
            if (
              runEvent.event_type === "run_completed" ||
              runEvent.event_type === "run_failed" ||
              runEvent.event_type === "run_cancelled"
            ) {
              finishStream();
            }
          },
          onInjectedMessage: (injectedMessage) => {
            const injectedUserMsg: ChatMessage = {
              id: injectedMessage.id,
              role: "user",
              content: injectedMessage.content,
              timestamp: Date.now(),
            };
            setMessages((prev) => {
              const assistantIndex = prev.findIndex(
                (message) => message.id === assistantId,
              );
              if (assistantIndex === -1) return [...prev, injectedUserMsg];
              return [
                ...prev.slice(0, assistantIndex),
                injectedUserMsg,
                ...prev.slice(assistantIndex),
              ];
            });
          },
          onChannelChange: (nextChannelId) => {
            setActiveChannelId(nextChannelId);
            activeChannelIdRef.current = nextChannelId;
          },
          onFileAttachment: (attachment) => {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId
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
          onComplete: (content, event) => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content,
                      tokenUsage:
                        "token_usage" in event
                          ? safeTokenUsage(event.token_usage) ?? message.tokenUsage ?? null
                          : message.tokenUsage ?? null,
                      estimatedCost:
                        "estimated_cost" in event &&
                        typeof event.estimated_cost === "number"
                          ? event.estimated_cost
                          : message.estimatedCost ?? null,
                      runRef:
                        "run_id" in event && typeof event.run_id === "string"
                          ? {
                              runId: event.run_id,
                              scope:
                                "scope" in event && typeof event.scope === "string"
                                  ? event.scope
                                  : "full",
                              status:
                                "type" in event && event.type === "run_error"
                                  ? "failed"
                                  : "running",
                            }
                          : message.runRef ?? null,
                      progressStatus: undefined,
                      progressFilePath: undefined,
                    }
                  : message,
              ),
            );
            if (
              chatMode === "auto" &&
              "detected_mode" in event &&
              typeof event.detected_mode === "string" &&
              event.detected_mode !== "progress_ack" &&
              event.detected_mode in MODE_CONFIG
            ) {
              setDetectedMode(event.detected_mode as SidebarChatMode);
            }
            const nextChannel =
              "stream_channel_id" in event && typeof event.stream_channel_id === "string"
                ? event.stream_channel_id.trim()
                : "";
            if (!nextChannel) {
              finishStream();
            }
          },
          onError: (message) => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              prev.map((entry) =>
                entry.id === assistantId
                  ? {
                      ...entry,
                      content: message || "Failed to connect to DAN server.",
                      progressStatus: undefined,
                      progressFilePath: undefined,
                    }
                  : entry,
              ),
            );
            finishStream();
          },
          onCloseWithoutTerminalEvent: () => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              prev.map((entry) =>
                entry.id === assistantId && !entry.content
                  ? {
                      ...entry,
                      content: "Connection lost. Please try again.",
                      progressStatus: undefined,
                      progressFilePath: undefined,
                    }
                  : entry,
              ),
            );
            finishStream();
          },
        });

        if (ws) {
          controller.signal.addEventListener(
            "abort",
            () => {
              ws.close();
              finishStream();
            },
            { once: true },
          );
        }
      } catch {
        setMessages((prev) =>
          prev.map((entry) =>
            entry.id === assistantId && !entry.content
              ? { ...entry, content: "Failed to connect to DAN server." }
              : entry,
          ),
        );
        finishStream();
      }
    },
    [contextProvider, finishStream, mode, workspaceId],
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

  useEffect(() => {
    if (streaming || pendingQueue.length === 0) return;
    const [next] = pendingQueue;
    setPendingQueue((prev) => prev.slice(1));
    void sendNow(next.content, next.attachments, next.mode);
  }, [pendingQueue, sendNow, streaming]);

  useEffect(() => {
    if (
      !pendingOpenFullChat ||
      streaming ||
      abortRef.current ||
      pendingQueue.length > 0
    ) {
      return;
    }
    setPendingOpenFullChat(false);
    void completeOpenFullChat();
  }, [completeOpenFullChat, pendingOpenFullChat, pendingQueue.length, streaming]);

  const handleStop = useCallback(async () => {
    const channelId = activeChannelIdRef.current;
    if (!channelId) return;
    try {
      await api.stopChatStream(channelId);
    } catch (error) {
      console.warn("Failed to stop mode chat stream:", error);
    }
  }, []);

  const canPushIntoCurrentTurn = useCallback(
    (item: PendingQueueItem) =>
      streaming &&
      Boolean(activeChannelId) &&
      item.attachments.length === 0 &&
      item.mentions.length === 0 &&
      item.mode === activeRequestModeRef.current,
    [activeChannelId, streaming],
  );

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
        <div className="min-w-0">
          <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
            AI Chat
          </span>
          {pendingOpenFullChat ? (
            <div className="truncate text-[10px] text-blue-500 dark:text-blue-400">
              Opening full Chat after the active response queue finishes
            </div>
          ) : threadId ? (
            <div className="truncate text-[10px] text-gray-400 dark:text-gray-500">
              Scratch thread ready for full Chat handoff
            </div>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
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
        {messages.length === 0 ? (
          <div className="flex h-full select-none flex-col items-center justify-center gap-2 px-2 text-center text-xs text-gray-500 dark:text-gray-600">
            <Sparkles size={24} className="text-gray-400 dark:text-gray-700" />
            <span>
              {mode === "research"
                ? "Ask about the active paper, notes, or figures"
                : "Ask anything about your code"}
            </span>
            <button
              onClick={openFullChat}
              className="mt-1 text-[10px] text-gray-500 transition-colors hover:text-blue-600 dark:hover:text-blue-400"
            >
              Open full Chat for history, branching, and thread controls
            </button>
            <span className="text-[10px] text-gray-400 dark:text-gray-600">
              Drag files here, paste files/images, use `@` mentions, or type `/` for commands
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
                onCopyMarkdown={
                  message.content ? () => handleCopyMessage(message) : undefined
                }
                allowRunCodeBlocks
                onRunCodeBlock={handleRunInTerminal}
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
                      onClick={() => {
                        const channelId = activeChannelIdRef.current;
                        if (!channelId) return;
                        api
                          .injectChatMessage(channelId, item.content, item.id)
                          .then(() => {
                            setPendingQueue((prev) =>
                              prev.filter((entry) => entry.id !== item.id),
                            );
                          })
                          .catch((error) => {
                            console.warn(
                              "Failed to inject queued mode-chat message:",
                              error,
                            );
                          });
                      }}
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
