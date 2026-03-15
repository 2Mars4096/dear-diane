import {
  useState,
  useRef,
  useEffect,
  useCallback,
  useMemo,
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
  Sparkles,
  Play,
  Square,
  Download,
  Search,
  Pin,
  HelpCircle,
  FileText,
  Bug,
  CheckCircle2,
  PencilLine,
  ArrowUp,
  GripVertical,
  MoreVertical,
  Folder,
  Clock,
  Info,
  Upload,
  Link2,
  Code2,
} from "lucide-react";
import { useGraphStore } from "../store/useGraphStore";
import { useAppStore } from "../store/useAppStore";
import { useWorkspaceStore } from "../store/useWorkspaceStore";
import type { ChatMessage, ChatStreamEvent, ToolCallInfo } from "../types/chat";
import type { ChatThreadSummary } from "../lib/api";
import * as api from "../lib/api";
import type { ApplyMutationResult } from "../lib/api";
import ChatMessageBubble from "./ChatMessage";
import EscalationBanner, { detectEscalation } from "./EscalationBanner";
import ModePreview from "./chat/ModePreview";
import VoiceInput from "./chat/VoiceInput";
import { getSwitchPreference, setSwitchPreference, type SwitchAction } from "../lib/switchPreference";
import GraphDiffPreview from "./GraphDiffPreview";
import MentionAutocomplete from "./MentionAutocomplete";
import ConfirmDialog from "./shell/ConfirmDialog";
import {
  buildAutoApplyPreviewMessage,
  parseRunIdFromStreamChannel,
  readMutationConfirmPreference,
  shouldAutoApplyMutation,
  summarizeMutationPlan,
  writeMutationConfirmPreference,
} from "../lib/chatMutation";
import { computeGraphDiff } from "../lib/graphDiff";
import {
  findMentionQuery,
  insertMention,
  parseMentions,
  type MentionRef,
} from "../lib/mentionParser";
import {
  deriveDraftThreadTitleFromMessage,
  getDisplayThreadTitle,
  normalizeThreadTitleInput,
} from "../lib/chatThreadTitle";
import {
  fromBackendMessage,
  safeTokenUsage,
  toBackendMessage,
} from "../lib/chatMessagePersistence";
import { createThreadPersistenceCoordinator } from "../lib/threadPersistenceCoordinator";
import { describeLatestToolProgress } from "../lib/toolCallPresentation";
import {
  detachToBackground,
  getBackgroundThreadIds,
  isStreamingInBackground,
  subscribe as subscribeBackgroundStreams,
  shutdownAll as shutdownAllBackgroundStreams,
} from "../lib/backgroundStreamRegistry";
import {
  getStreamDisconnectError,
  getStreamReconnectDelayMs,
  isAssistantBubbleStreaming,
  shouldReconnectStream,
} from "../lib/chatStreamLifecycle";
import { deriveGraphRevisionSource, getClientGraphRevision } from "../lib/chatGraphRevision";

const DEFAULT_WIDTH = 380;
const MIN_WIDTH = 280;
const MAX_WIDTH = 640;

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}k`;
  return `${n}`;
}

const EXAMPLE_PROMPTS = [
  "Add a reviewer node after the writer",
  "Connect the output of Planner to Drafter",
  "Create a 3-node research pipeline",
];

const BUILD_PROMPTS = [
  "Create a research pipeline with planner, researcher, and writer",
  "Build a review loop for paper writing with feedback",
  "Design a RAG QA workflow with retrieval and answering",
];

const ASK_PROMPTS = [
  "What does this workflow do?",
  "How does data flow from input to output?",
  "Explain the review loop structure",
];

const PLAN_PROMPTS = [
  "Plan adding a validation step before output",
  "Design a review loop with 3 iterations",
  "Plan restructuring the data pipeline",
];

const DEBUG_PROMPTS = [
  "Why did my last run fail?",
  "Check for missing connections",
  "Diagnose errors in my workflow",
];

type ChatMode = "ask" | "agent" | "plan" | "debug" | "auto";

const MODE_CONFIG: Record<ChatMode, { label: string; icon: typeof Sparkles; color: string }> = {
  agent: { label: "Agent", icon: Sparkles, color: "indigo" },
  ask: { label: "Ask", icon: HelpCircle, color: "sky" },
  plan: { label: "Plan", icon: FileText, color: "amber" },
  debug: { label: "Debug", icon: Bug, color: "rose" },
  auto: { label: "Auto", icon: PencilLine, color: "violet" },
};

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
// Quick-action helpers & sub-components
// ---------------------------------------------------------------------------

function generateFollowups(assistantMessage: string, _userMessage: string): string[] {
  const suggestions: string[] = [];

  if (assistantMessage.includes("error") || assistantMessage.includes("bug")) {
    suggestions.push("How do I fix this?", "Show me the relevant code");
  }
  if (assistantMessage.includes("```")) {
    suggestions.push("Explain this code", "Are there any issues?", "Can you optimize this?");
  }
  if (assistantMessage.includes("table") || assistantMessage.includes("data")) {
    suggestions.push("Create a chart from this data", "Export as CSV");
  }
  if (assistantMessage.includes("file") || assistantMessage.includes("wrote")) {
    suggestions.push("Show the full file", "What else needs to change?");
  }

  if (suggestions.length === 0) {
    suggestions.push("Tell me more", "Can you elaborate?", "What's the next step?");
  }

  return suggestions.slice(0, 3);
}

function trackCommand(cmd: string) {
  try {
    const recent: string[] = JSON.parse(localStorage.getItem("dan-recent-commands") ?? "[]");
    const updated = [cmd, ...recent.filter((c) => c !== cmd)].slice(0, 20);
    localStorage.setItem("dan-recent-commands", JSON.stringify(updated));
  } catch { /* ignore */ }
}

function SuggestedFollowups({
  suggestions,
  onSelect,
}: {
  suggestions: string[];
  onSelect: (text: string) => void;
}) {
  if (suggestions.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2 mt-3 mb-1">
      {suggestions.map((s, i) => (
        <button
          key={i}
          onClick={() => onSelect(s)}
          className="px-3 py-1.5 text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-full hover:bg-indigo-50 hover:text-indigo-600 hover:border-indigo-200 transition-colors"
        >
          {s}
        </button>
      ))}
    </div>
  );
}

const SLASH_COMMANDS = [
  { cmd: "/run", desc: "Run the current workflow" },
  { cmd: "/show", desc: "Show workflow details" },
  { cmd: "/undo", desc: "Undo last change" },
  { cmd: "/help", desc: "Show available commands" },
  { cmd: "/build", desc: "Build a workflow from description" },
  { cmd: "/plan", desc: "Plan changes before applying" },
  { cmd: "/export", desc: "Export thread or workflow" },
];

function SlashCommandPopup({
  filter,
  onSelect,
}: {
  filter: string;
  onSelect: (cmd: string) => void;
}) {
  const filtered = SLASH_COMMANDS.filter((c) =>
    c.cmd.startsWith(filter.toLowerCase()),
  );
  if (filtered.length === 0) return null;

  return (
    <div className="mb-1 border border-gray-200 rounded-lg bg-white shadow-md overflow-hidden">
      {filtered.map((c) => (
        <button
          key={c.cmd}
          onMouseDown={(e) => {
            e.preventDefault();
            onSelect(c.cmd);
          }}
          className="flex items-center gap-3 w-full text-left px-3 py-2 text-sm hover:bg-indigo-50 transition-colors"
        >
          <span className="font-mono font-semibold text-indigo-600 text-xs w-16 shrink-0">
            {c.cmd}
          </span>
          <span className="text-gray-500 text-xs">{c.desc}</span>
        </button>
      ))}
    </div>
  );
}

function RecentCommandsBar({ onSelect }: { onSelect: (cmd: string) => void }) {
  const [recentCommands] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem("dan-recent-commands") ?? "[]");
    } catch {
      return [];
    }
  });

  const defaultCommands = ["/ask", "/agent", "/plan", "/debug", "/search", "/project"];
  const commands = recentCommands.length > 0 ? recentCommands : defaultCommands;

  return (
    <div className="flex items-center gap-1.5 px-1 py-1 overflow-x-auto scrollbar-hide">
      {commands.slice(0, 8).map((cmd) => (
        <button
          key={cmd}
          onClick={() => onSelect(cmd + " ")}
          className="shrink-0 px-2.5 py-0.5 text-[11px] text-gray-500 bg-gray-50 border border-gray-100 rounded-full hover:text-indigo-600 hover:bg-indigo-50 hover:border-indigo-200 transition-colors"
        >
          {cmd}
        </button>
      ))}
    </div>
  );
}

function TypingIndicator({ phase }: { phase: "thinking" | "executing" | "writing" }) {
  const labels = {
    thinking: "DAN is thinking…",
    executing: "Running tools…",
    writing: "Composing response…",
  };

  return (
    <div className="flex items-center gap-2 px-4 py-2 text-xs text-gray-400">
      <div className="flex gap-1">
        <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
        <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
        <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
      </div>
      <span>{labels[phase]}</span>
    </div>
  );
}

function SmartPasteHint({
  hint,
  onAccept,
  onDismiss,
}: {
  hint: { type: "url" | "code" | "image"; value?: string; file?: File };
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const labels: Record<string, { icon: typeof Link2; text: string; action: string }> = {
    url: { icon: Link2, text: "URL detected", action: "Fetch this URL?" },
    code: { icon: Code2, text: "Code detected", action: "Wrap in code block?" },
    image: { icon: Upload, text: "Image pasted", action: "Attach as image?" },
  };
  const cfg = labels[hint.type];
  const Icon = cfg.icon;

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 mt-1 bg-indigo-50 border border-indigo-100 rounded-lg text-xs animate-in fade-in slide-in-from-bottom-1 duration-200">
      <Icon size={12} className="text-indigo-500 flex-shrink-0" />
      <span className="text-indigo-700">{cfg.text}</span>
      <span className="text-indigo-400">—</span>
      <button onClick={onAccept} className="font-medium text-indigo-600 hover:text-indigo-800 transition-colors">
        {cfg.action}
      </button>
      <button onClick={onDismiss} className="text-indigo-300 hover:text-indigo-500 ml-auto transition-colors">
        <X size={12} />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface ChatPanelProps {
  fullScreen?: boolean;
  workspaceId?: string;
  onThreadOpen?: (threadId: string, threadTitle?: string) => void;
  onThreadTitleUpdate?: (threadId: string, title: string) => void;
}

export default function ChatPanel({
  fullScreen = false,
  workspaceId: _workspaceId,
  onThreadOpen,
  onThreadTitleUpdate,
}: ChatPanelProps) {
  const rawGraphId = useGraphStore((s) => s.graphId);
  const graphId = rawGraphId || (fullScreen ? "_scratch" : null);
  const danGraph = useGraphStore((s) => s.danGraph);
  const graphRevision = useGraphStore((s) => s.graphRevision);
  const isGraphDirty = useGraphStore((s) => s.dirty);
  const loadGraph = useGraphStore((s) => s.loadGraph);
  const pushSnapshot = useGraphStore((s) => s.pushSnapshot);
  const chatMode = useGraphStore((s) => s.chatMode);
  const chatFocusTrigger = useGraphStore((s) => s.chatFocusTrigger);

  const [chatOpen, setChatOpen] = useState(fullScreen);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isRunStreaming, setIsRunStreaming] = useState(false);
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
  useEffect(() => {
    if (!activeThreadId) return;
    try {
      localStorage.setItem(`dan_active_thread_${graphId ?? ""}`, activeThreadId);
    } catch {}
  }, [activeThreadId, graphId]);
  const [showThreadList, setShowThreadList] = useState(!fullScreen);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [confirmDeleteThreadId, setConfirmDeleteThreadId] = useState<string | null>(null);
  const [threadTitle, setThreadTitle] = useState("");
  const [sessionMarkers, setSessionMarkers] = useState<
    Record<string, { historyCursor: number }>
  >({});
  const [previewingMessage, setPreviewingMessage] =
    useState<ChatMessage | null>(null);
  const [isApplying, setIsApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [buildJustCompleted, setBuildJustCompleted] = useState(false);
  const [contextWindow, setContextWindow] = useState(0);
  const [staleRevision, setStaleRevision] = useState(false);
  const [activeChannelId, setActiveChannelId] = useState<string | null>(null);
  const [detectedMode, setDetectedMode] = useState<string | null>(null);
  const [escalation, setEscalation] = useState<{ targetMode: import("../store/useAppStore").AppMode; reason: string } | null>(null);
  const setMode = useAppStore((s) => s.setMode);
  const pendingChatMessage = useAppStore((s) => s.pendingChatMessage);
  const setPendingChatMessage = useAppStore((s) => s.setPendingChatMessage);
  const [switchPrefMenu, setSwitchPrefMenu] = useState(false);
  const [mutationConfirmMode, setMutationConfirmMode] = useState<boolean>(() =>
    readMutationConfirmPreference(),
  );
  const [pendingQueue, setPendingQueue] = useState<
    Array<{ id: string; content: string; timestamp: number }>
  >([]);
  const pendingQueueRef = useRef(pendingQueue);
  pendingQueueRef.current = pendingQueue;
  const sendMessageRef = useRef<((text: string) => void) | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<
    Array<{
      thread_id: string;
      thread_title: string;
      workflow_id: string;
      message_id: string;
      message_preview: string;
      timestamp: string;
    }>
  >([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showContextPanel, setShowContextPanel] = useState(false);
  const [threadContextMenu, setThreadContextMenu] = useState<{
    x: number;
    y: number;
    threadId: string;
    threadTitle: string;
    pinned: boolean;
  } | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [pasteHint, setPasteHint] = useState<{
    type: "url" | "code" | "image";
    value?: string;
    file?: File;
  } | null>(null);
  const [userAttachments, setUserAttachments] = useState<File[]>([]);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const applyingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const activeAssistantIdRef = useRef<string | null>(null);
  const runStreamHandoffRef = useRef(false);
  const dragging = useRef(false);
  const activeThreadIdRef = useRef<string | null>(null);
  const prevGraphIdRef = useRef<string | null>(null);
  const activeChannelIdRef = useRef<string | null>(null);
  const activeRunChannelIdRef = useRef<string | null>(null);
  const graphIdRef = useRef<string | null>(graphId);
  const persistenceCoordinatorRef = useRef<
    ReturnType<typeof createThreadPersistenceCoordinator<ChatMessage[]>> | null
  >(null);
  if (!persistenceCoordinatorRef.current) {
    persistenceCoordinatorRef.current =
      createThreadPersistenceCoordinator<ChatMessage[]>({
        persist: async (workflowId, threadId, nextMessages, options) => {
          try {
            await api.updateChatThread(workflowId, threadId, {
              messages: nextMessages.map(toBackendMessage),
            });
          } catch (err: unknown) {
            if (!options?.silent) {
              console.warn(
                options?.label ?? "Failed to save thread:",
                err,
              );
            }
          }
        },
      });
  }
  activeChannelIdRef.current = activeChannelId;
  graphIdRef.current = graphId;

  activeThreadIdRef.current = activeThreadId;

  const detachCurrentStream = useCallback(() => {
    const ws = wsRef.current;
    const threadId = activeThreadIdRef.current;
    const assistantId = activeAssistantIdRef.current;
    if (!ws || ws.readyState >= WebSocket.CLOSING || !threadId || !assistantId) return;
    const wfId = graphIdRef.current;
    if (!wfId) return;
    detachToBackground({
      ws,
      channelId: activeChannelIdRef.current ?? "",
      threadId,
      workflowId: wfId,
      assistantMessageId: assistantId,
      messages: messagesRef.current,
    });
    wsRef.current = null;
    activeAssistantIdRef.current = null;
  }, []);

  const [bgStreamIds, setBgStreamIds] = useState<Set<string>>(() => getBackgroundThreadIds());
  const bgStreamReloadRef = useRef<((wfId: string) => void) | null>(null);
  useEffect(() => {
    let prevIds = getBackgroundThreadIds();
    return subscribeBackgroundStreams(() => {
      const nextIds = getBackgroundThreadIds();
      setBgStreamIds(nextIds);
      const tid = activeThreadIdRef.current;
      const wfId = graphIdRef.current;
      if (tid && prevIds.has(tid) && !nextIds.has(tid) && wfId) {
        api.getChatThread(wfId, tid).then((data) => {
          const backendMsgs = (data.messages ?? []) as Record<string, unknown>[];
          setMessages(backendMsgs.map(fromBackendMessage));
          setIsStreaming(false);
          if (data.title) setThreadTitle(data.title as string);
        }).catch(() => {});
        bgStreamReloadRef.current?.(wfId);
      }
      prevIds = nextIds;
    });
  }, []);

  useEffect(() => {
    if (!isStreaming) {
      activeAssistantIdRef.current = null;
      const q = pendingQueueRef.current;
      if (q.length > 0) {
        const next = q[0];
        setPendingQueue((prev) => prev.slice(1));
        setTimeout(() => sendMessageRef.current?.(next.content), 100);
      }
    }
  }, [isStreaming]);

  // -------------------------------------------------------------------------
  // Thread persistence helpers
  // -------------------------------------------------------------------------

  const persistThreadMessages = useCallback(
    (
      workflowId: string | null,
      threadId: string | null | undefined,
      nextMessages: ChatMessage[],
      options?: { silent?: boolean; label?: string },
    ) => {
      if (!workflowId || !threadId) return;
      void persistenceCoordinatorRef.current?.persistNow(
        workflowId,
        threadId,
        nextMessages,
        options,
      );
    },
    [],
  );

  const scheduleThreadPersist = useCallback(
    (
      workflowId: string | null,
      threadId: string | null | undefined,
      nextMessages: ChatMessage[],
      delayMs = 5_000,
    ) => {
      if (!workflowId || !threadId) return;
      persistenceCoordinatorRef.current?.schedule(
        workflowId,
        threadId,
        nextMessages,
        delayMs,
        { silent: true },
      );
    },
    [],
  );

  const flushScheduledThreadPersist = useCallback(
    (
      fallbackWorkflowId?: string | null,
      fallbackThreadId?: string | null,
      fallbackMessages?: ChatMessage[],
    ) => {
      void persistenceCoordinatorRef.current?.flushPending(
        fallbackWorkflowId && fallbackThreadId && fallbackMessages
          ? {
              workflowId: fallbackWorkflowId,
              threadId: fallbackThreadId,
              value: fallbackMessages,
              options: { silent: true },
            }
          : undefined,
      );
    },
    [],
  );

  useEffect(() => {
    return () => {
      persistenceCoordinatorRef.current?.dispose();
    };
  }, []);

  const fetchThreads = useCallback(async (wfId: string) => {
    setLoadingThreads(true);
    try {
      const { threads: list } = await api.listChatThreads(wfId);
      setThreads(list);
      const active = list.find((t) => t.id === activeThreadIdRef.current);
      if (active?.title) {
        setThreadTitle(active.title);
      }
      return list;
    } catch (err) {
      console.warn("Failed to fetch threads:", err);
      return [];
    } finally {
      setLoadingThreads(false);
    }
  }, []);
  bgStreamReloadRef.current = fetchThreads;

  const refreshActiveThreadTitle = useCallback(
    async (wfId: string, threadId: string) => {
      try {
        const data = await api.getChatThread(wfId, threadId);
        const nextTitle = getDisplayThreadTitle(data.title as string, "");
        if (!nextTitle) return;
        if (activeThreadIdRef.current !== threadId) return;
        setThreadTitle(nextTitle);
        onThreadTitleUpdate?.(threadId, nextTitle);
        setThreads((prev) =>
          prev.map((thread) =>
            thread.id === threadId
              ? {
                  ...thread,
                  title: nextTitle,
                  updated_at:
                    typeof data.updated_at === "string"
                      ? data.updated_at
                      : thread.updated_at,
                }
              : thread,
          ),
        );
      } catch {
        // Best-effort title refresh.
      }
    },
    [onThreadTitleUpdate],
  );

  const loadThread = useCallback(
    async (wfId: string, threadId: string) => {
      if (isStreaming) detachCurrentStream();
      setIsStreaming(false);
      setPendingQueue([]);
      try {
        const data = await api.getChatThread(wfId, threadId);
        const backendMsgs = (data.messages ?? []) as Record<string, unknown>[];
        setMessages(backendMsgs.map(fromBackendMessage));
        setActiveThreadId(threadId);
        const title = (data.title as string) || "";
        setThreadTitle(title);
        setShowThreadList(false);
        setError(null);
        const storedMode = (data.mode as ChatMode) || "agent";
        useGraphStore.getState().setChatMode(storedMode);
        if (isStreamingInBackground(threadId)) {
          setIsStreaming(true);
        }
        onThreadOpen?.(threadId, title);
        onThreadTitleUpdate?.(threadId, title);
        requestAnimationFrame(() => textareaRef.current?.focus());
      } catch (err) {
        console.warn("Failed to load thread:", err);
      }
    },
    [isStreaming, detachCurrentStream, onThreadOpen, onThreadTitleUpdate],
  );

  const probeBackendAvailability = useCallback(async () => {
    try {
      await api.getServerHealth();
      return true;
    } catch {
      return false;
    }
  }, []);

  const restoreThreadSnapshotFromServer = useCallback(
    async (wfId: string | null, threadId: string | null) => {
      if (!wfId || !threadId) return false;
      try {
        const data = await api.getChatThread(wfId, threadId);
        const backendMsgs = (data.messages ?? []) as Record<string, unknown>[];
        if (activeThreadIdRef.current === threadId) {
          setMessages(backendMsgs.map(fromBackendMessage));
          setThreadTitle(getDisplayThreadTitle((data.title as string) || "", ""));
          const storedMode = (data.mode as ChatMode) || "agent";
          useGraphStore.getState().setChatMode(storedMode);
        }
        setThreads((prev) =>
          prev.map((thread) =>
            thread.id === threadId
              ? {
                  ...thread,
                  title:
                    typeof data.title === "string" && data.title.trim()
                      ? data.title
                      : thread.title,
                  updated_at:
                    typeof data.updated_at === "string"
                      ? data.updated_at
                      : thread.updated_at,
                }
              : thread,
          ),
        );
        return true;
      } catch {
        return false;
      }
    },
    [],
  );

  const classifyBackendDisconnectState = useCallback(
    async (closeCode: number, hadTransportError: boolean) => {
      if (closeCode === 4004) return "restarted" as const;
      const backendAvailable = await probeBackendAvailability();
      if (!backendAvailable) return "unavailable" as const;
      return hadTransportError ? ("restarted" as const) : ("unknown" as const);
    },
    [probeBackendAvailability],
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
        let targetId = sorted[0].id;
        const wsActiveThread = useWorkspaceStore.getState().getActiveWorkspace()?.activeThreadId;
        if (wsActiveThread && sorted.some((t) => t.id === wsActiveThread)) {
          targetId = wsActiveThread;
        } else {
          try {
            const saved = localStorage.getItem(`dan_active_thread_${graphId}`);
            if (saved && sorted.some((t) => t.id === saved)) targetId = saved;
          } catch {}
        }
        await loadThread(graphId, targetId);
      } else if (sorted.length === 0) {
        setShowThreadList(true);
        requestAnimationFrame(() => textareaRef.current?.focus());
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatOpen, graphId]);

  useEffect(() => {
    if (!showThreadList || !graphId) return;
    void fetchThreads(graphId);
  }, [showThreadList, graphId, fetchThreads]);

  // -------------------------------------------------------------------------
  // Keyboard shortcut: Cmd/Ctrl+Shift+M to cycle chat modes
  // -------------------------------------------------------------------------

  useEffect(() => {
    const CYCLE: ChatMode[] = ["agent", "ask", "plan", "debug"];
    const handler = (e: globalThis.KeyboardEvent) => {
      if (e.shiftKey && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "m") {
        const activeNode = document.activeElement;
        const isInputFocused =
          activeNode?.tagName === "INPUT" ||
          activeNode?.tagName === "TEXTAREA" ||
          (activeNode as HTMLElement)?.isContentEditable;
        if (isInputFocused) return;

        e.preventDefault();
        const current = useGraphStore.getState().chatMode;
        const idx = CYCLE.indexOf(current as ChatMode);
        const next = CYCLE[(idx + 1) % CYCLE.length];
        useGraphStore.getState().setChatMode(next);
        const tid = activeThreadIdRef.current;
        const gid = graphId;
        if (tid && gid) {
          api.updateChatThread(gid, tid, { mode: next }).catch(() => {});
        }
        useGraphStore.getState().addToast({
          type: "info",
          message: `Chat mode: ${MODE_CONFIG[next].label}`,
        });
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [graphId]);

  // -------------------------------------------------------------------------
  // Auto-scroll on new messages
  // -------------------------------------------------------------------------

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!error && !staleRevision) return;
    const timer = window.setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [error, staleRevision]);

  useEffect(() => {
    if (chatFocusTrigger > 0) {
      setChatOpen(true);
      setShowThreadList(false);
      const prefill = useGraphStore.getState().chatPrefill;
      if (!prefill) {
        setMessages([]);
        setActiveThreadId(null);
        setThreadTitle("");
      }
      setError(null);
      setBuildJustCompleted(false);
    }
  }, [chatFocusTrigger]);

  useEffect(() => {
    const prefill = useGraphStore.getState().chatPrefill;
    if (prefill) {
      setInputText(prefill);
      useGraphStore.setState({ chatPrefill: null });
      requestAnimationFrame(() => textareaRef.current?.focus());
    }
  }, [chatFocusTrigger]);

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
      type: string;
      id: string;
      name: string;
    }) => {
      const ta = textareaRef.current;
      if (!ta) return;
      const ref: MentionRef = {
        name: mention.name,
        type: mention.type as MentionRef["type"],
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

  // Clean up all WebSockets + background streams on unmount / window close
  useEffect(() => {
    const onBeforeUnload = () => {
      flushScheduledThreadPersist(
        graphIdRef.current,
        activeThreadIdRef.current,
        messagesRef.current,
      );
      wsRef.current?.close();
      shutdownAllBackgroundStreams();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      onBeforeUnload();
    };
  }, [flushScheduledThreadPersist]);

  // -------------------------------------------------------------------------
  // Mutation preference + run stream helpers
  // -------------------------------------------------------------------------

  const toggleMutationConfirmMode = useCallback(() => {
    setMutationConfirmMode((prev) => {
      const next = !prev;
      writeMutationConfirmPreference(next);
      useGraphStore.getState().addToast({
        type: "info",
        message: next
          ? "Review diffs before apply enabled"
          : "Auto-apply mutations enabled",
      });
      return next;
    });
  }, []);

  const attachRunStream = useCallback(
    (
      initialStreamChannelId: string,
      assistantId: string,
      capturedGraphId: string | null,
      initialRunRef?: { runId: string; scope: string; status: string } | null,
      options?: { reconnectCount?: number },
    ) => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const connectRunStream = (streamChannelId: string, reconnectCount = 0) => {
        activeRunChannelIdRef.current = streamChannelId;
        const runWs = new WebSocket(
          `${proto}//${location.host}/api/chat/${streamChannelId}/events`,
        );
        wsRef.current = runWs;
        setActiveChannelId(null); // run streams are observational, not stoppable via chat stop route
        setIsStreaming(false);
        setIsRunStreaming(true);
        let runWsClosedIntentionally = false;
        let runWsHadTransportError = false;
        let runWsHadTerminalEvent = false;

        runWs.onmessage = (ev) => {
          try {
            const parsed = JSON.parse(ev.data);
            if (parsed.type === "chat_run_event" && parsed.run_event) {
              const re = parsed.run_event as {
                event_type: string;
                node_id?: string | null;
                summary: string;
                detail?: Record<string, unknown>;
              };
              const isTerminalRunEvent =
                re.event_type === "run_completed" ||
                re.event_type === "run_failed" ||
                re.event_type === "run_cancelled";
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: m.content ? `${m.content}\n${re.summary}` : re.summary,
                        runEvents: [
                          ...(m.runEvents || []),
                          {
                            type: "run_event",
                            event_type: re.event_type,
                            node_id: re.node_id,
                            summary: re.summary,
                            detail: re.detail,
                          },
                        ],
                        runRef: m.runRef
                          ? {
                              ...m.runRef,
                              status:
                                re.event_type === "run_completed"
                                  ? "completed"
                                  : re.event_type === "run_failed"
                                    ? "failed"
                                    : re.event_type === "run_cancelled"
                                      ? "cancelled"
                                      : m.runRef.status,
                            }
                          : initialRunRef ?? m.runRef,
                      }
                    : m,
                );
                const tid = activeThreadIdRef.current;
                if (isTerminalRunEvent) {
                  persistThreadMessages(capturedGraphId, tid, updated);
                } else {
                  scheduleThreadPersist(capturedGraphId, tid, updated);
                }
                return updated;
              });
              if (isTerminalRunEvent) {
                runWsHadTerminalEvent = true;
                runWsClosedIntentionally = true;
                activeRunChannelIdRef.current = null;
                setIsRunStreaming(false);
                runWs.close();
              }
            }
          } catch {
            /* ignore parse errors */
          }
        };

        runWs.onerror = () => {
          runWsHadTransportError = true;
          flushScheduledThreadPersist(
            capturedGraphId,
            activeThreadIdRef.current,
            messagesRef.current,
          );
        };
        runWs.onclose = (event) => {
          flushScheduledThreadPersist(
            capturedGraphId,
            activeThreadIdRef.current,
            messagesRef.current,
          );
          if (
            shouldReconnectStream({
              closedIntentionally: runWsClosedIntentionally,
              activeChannelId: activeRunChannelIdRef.current,
              channelId: streamChannelId,
              reconnectCount,
              closeCode: event.code,
              hadTransportError: runWsHadTransportError,
              hadTerminalEvent: runWsHadTerminalEvent,
            })
          ) {
            window.setTimeout(() => {
              connectRunStream(streamChannelId, reconnectCount + 1);
            }, getStreamReconnectDelayMs(reconnectCount));
            return;
          }
          if (
            runWsClosedIntentionally ||
            ((event.code === 1000 || event.code === 1005) && runWsHadTerminalEvent)
          ) {
            if (activeRunChannelIdRef.current === streamChannelId) {
              activeRunChannelIdRef.current = null;
            }
            setIsRunStreaming(false);
            return;
          }
          if (activeRunChannelIdRef.current === streamChannelId) {
            activeRunChannelIdRef.current = null;
          }
          setIsRunStreaming(false);
          void (async () => {
            const backendState = await classifyBackendDisconnectState(
              event.code,
              runWsHadTransportError,
            );
            const disconnectError = getStreamDisconnectError({
              closedIntentionally: runWsClosedIntentionally,
              closeCode: event.code,
              hadTransportError: runWsHadTransportError,
              streamLabel: "Run stream connection",
              recoveryHint:
                backendState === "unavailable"
                  ? "Wait a few seconds for DAN to come back, then ask for status or rerun."
                  : "Ask for status or rerun if needed.",
              backendState,
            });
            if (disconnectError) {
              setError(disconnectError);
              useGraphStore.getState().addToast({
                type: "error",
                message:
                  backendState === "unavailable"
                    ? "Local DAN backend unavailable"
                    : "Run stream disconnected",
              });
            }
          })();
        };
      };

      connectRunStream(initialStreamChannelId, options?.reconnectCount ?? 0);
    },
    [
      classifyBackendDisconnectState,
      flushScheduledThreadPersist,
      persistThreadMessages,
      scheduleThreadPersist,
    ],
  );

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

  const lastPromptTokens = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const tu = messages[i].tokenUsage;
      if (messages[i].role === "assistant" && tu && typeof tu.prompt === "number") {
        return tu.prompt;
      }
    }
    return 0;
  })();

  const totalTokens = messages.reduce((sum, m) => {
    const tu = m.tokenUsage;
    if (!tu) return sum;
    const p = typeof tu.prompt === "number" ? tu.prompt : 0;
    const c = typeof tu.completion === "number" ? tu.completion : 0;
    return sum + p + c;
  }, 0);

  // -------------------------------------------------------------------------
  // Send message (with thread auto-creation & save-on-complete)
  // -------------------------------------------------------------------------

  const sendMessage = useCallback(
    async (text?: string, historyOverride?: ChatMessage[], modeOverride?: ChatMode) => {
      const content = (text ?? inputText).trim();
      if (!content) return;

      if (isStreaming && !historyOverride) {
        setPendingQueue((q) => [
          ...q,
          { id: crypto.randomUUID(), content, timestamp: Date.now() },
        ]);
        setInputText("");
        return;
      }

      let threadId = activeThreadIdRef.current;
      if (!threadId && graphId) {
        try {
          const draftTitle = deriveDraftThreadTitleFromMessage(content);
          const data = await api.createChatThread(graphId, draftTitle);
          threadId = (data as Record<string, unknown>).id as string;
          setActiveThreadId(threadId);
          activeThreadIdRef.current = threadId;
          setThreadTitle(draftTitle);
          setShowThreadList(false);
          onThreadOpen?.(threadId, draftTitle);
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
      activeAssistantIdRef.current = assistantId;
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setInputText("");
      setUserAttachments([]);
      if (content.startsWith("/")) {
        trackCommand(content.split(/\s/)[0]);
      }
      setIsStreaming(true);
      setError(null);

      if (fullScreen) {
        const esc = detectEscalation(content);
        if (esc) {
          const pref = getSwitchPreference(esc.targetMode);
          if (pref === "always") {
            setMode(esc.targetMode);
          } else if (pref === "ask") {
            setEscalation(esc);
          }
        }
      }
      setStaleRevision(false);
      setBuildJustCompleted(false);
      setDetectedMode(null);

      const capturedGraphId = graphId;
      const graphState = useGraphStore.getState();
      const currentDanGraph = isGraphDirty
        ? deriveGraphRevisionSource({
            baseGraph: graphState.danGraph,
            nodes: graphState.nodes,
            edges: graphState.edges,
            layerStack: graphState.layerStack,
            loopGroups: graphState.loopGroups,
          })
        : graphState.danGraph;
      const clientGraphRevision = currentDanGraph
        ? await getClientGraphRevision(currentDanGraph, graphRevision, {
            preferLocal: isGraphDirty,
          })
        : graphRevision ?? undefined;

      try {
        const res = await fetch("/api/chat/message", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workflow_id: graphId,
            message: content,
            history: (historyOverride ?? messagesRef.current).map((m) => ({
              role: m.role,
              content: m.content,
            })),
            thread_id: threadId,
            client_graph_revision: clientGraphRevision,
            mode: modeOverride || useGraphStore.getState().chatMode,
            mentions: parseMentions(content)
              .segments.filter((s) => s.type === "mention")
              .map((s) => ({
                type: (s as { type: "mention"; mention: MentionRef }).mention.type,
                identifier: (s as { type: "mention"; mention: MentionRef }).mention.id,
              })),
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

        if (resBody.type === "run_error") {
          const errMsg =
            (resBody.error as { message?: string } | undefined)?.message ??
            "Unknown error";
          setMessages((prev) => {
            const updated = prev.map((m) =>
              m.id === assistantId
                ? { ...m, content: `Run failed: ${errMsg}` }
                : m,
            );
            persistThreadMessages(
              capturedGraphId,
              threadId ?? activeThreadIdRef.current,
              updated,
            );
            return updated;
          });
          setIsStreaming(false);
          return;
        }

        if (resBody.type === "run_started") {
          const initialRunRef = {
            runId: resBody.run_id as string,
            scope: (resBody.scope as string) ?? "full",
            status: "running",
          };
          setMessages((prev) => {
            const updated = prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    content: `Started ${resBody.scope ?? "full"} run.`,
                    runRef: initialRunRef,
                  }
                : m,
            );
            persistThreadMessages(
              capturedGraphId,
              threadId ?? activeThreadIdRef.current,
              updated,
            );
            return updated;
          });

          if (resBody.stream_channel_id) {
            attachRunStream(
              resBody.stream_channel_id as string,
              assistantId,
              capturedGraphId,
              initialRunRef,
            );
          } else {
            setIsStreaming(false);
          }
          return;
        }

        const { stream_channel_id } = resBody as {
          message_id: string;
          stream_channel_id: string;
        };

        {
          const tid = threadId;
          if (tid && capturedGraphId) {
            const snapshot = [
              ...(historyOverride ?? messagesRef.current),
              userMsg,
              assistantMsg,
            ];
            persistThreadMessages(capturedGraphId, tid, snapshot, {
              label: "Failed to save initial messages:",
            });
            setTimeout(() => {
              void refreshActiveThreadTitle(capturedGraphId, tid);
            }, 1200);
            setTimeout(() => {
              void refreshActiveThreadTitle(capturedGraphId, tid);
            }, 3500);
          }
        }

        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const seenStreamChannels = new Set<string>();

        const connectToChatStream = (
          channelId: string,
          options?: { reconnectCount?: number },
        ) => {
          const reconnectCount = options?.reconnectCount ?? 0;
          const isReconnect = reconnectCount > 0;
          if (!isReconnect && seenStreamChannels.has(channelId)) {
            setError("Chat stream redirect loop detected");
            setIsStreaming(false);
            setActiveChannelId(null);
            return;
          }
          if (!isReconnect) seenStreamChannels.add(channelId);
          setActiveChannelId(channelId);

          const ws = new WebSocket(
            `${proto}//${location.host}/api/chat/${channelId}/events`,
          );
          wsRef.current = ws;
          let wsClosedIntentionally = false;
          let wsHadTransportError = false;
          let wsHadTerminalEvent = false;

          ws.onmessage = (e) => {
            try {
              const evt: ChatStreamEvent = JSON.parse(e.data);

              if (["node_started", "node_completed", "artifact_created"].includes(evt.type)) {
                window.dispatchEvent(new CustomEvent("dan:engine-event-raw", { detail: evt }));
              }

              if (evt.type === "chat_queued") {
                const nextChannel = (evt.stream_channel_id ?? "").trim();
                const queuePosition =
                  typeof evt.queue_position === "number" ? evt.queue_position : 0;

                setMessages((prev) => {
                  const updated = prev.map((m) =>
                    m.id === assistantId
                      ? {
                          ...m,
                          content:
                            queuePosition > 1
                              ? `Queued behind ${queuePosition} earlier messages...`
                              : "Queued behind an earlier message...",
                        }
                      : m,
                  );
                  scheduleThreadPersist(
                    capturedGraphId,
                    threadId ?? activeThreadIdRef.current,
                    updated,
                  );
                  return updated;
                });

                if (nextChannel && nextChannel !== channelId) {
                  runStreamHandoffRef.current = true;
                  wsClosedIntentionally = true;
                  ws.close();
                  connectToChatStream(nextChannel);
                }
                return;
              }
            if (evt.type === "chat_token") {
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content:
                          evt.accumulated ?? m.content + (evt.delta ?? ""),
                        progressStatus: undefined,
                      }
                    : m,
                );
                scheduleThreadPersist(
                  capturedGraphId,
                  threadId ?? activeThreadIdRef.current,
                  updated,
                );
                return updated;
              });
            } else if (evt.type === "chat_complete") {
              const isProgressAck = evt.detected_mode === "progress_ack";
              if (evt.context_window) setContextWindow(evt.context_window);
              if (evt.detected_mode && !isProgressAck) setDetectedMode(evt.detected_mode);
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? isProgressAck
                      ? {
                          ...m,
                          progressStatus: evt.content || m.progressStatus,
                          tokenUsage: safeTokenUsage(evt.token_usage) ?? m.tokenUsage ?? null,
                        }
                      : {
                          ...m,
                          content: (evt.content || m.content),
                          progressStatus: undefined,
                          tokenUsage: safeTokenUsage(evt.token_usage) ?? m.tokenUsage ?? null,
                          estimatedCost:
                            typeof evt.estimated_cost === "number"
                              ? evt.estimated_cost
                              : m.estimatedCost ?? null,
                        }
                    : m,
                );
                if (isProgressAck) {
                  scheduleThreadPersist(
                    capturedGraphId,
                    threadId ?? activeThreadIdRef.current,
                    updated,
                  );
                } else {
                  persistThreadMessages(
                    capturedGraphId,
                    threadId ?? activeThreadIdRef.current,
                    updated,
                  );
                }
                return updated;
              });
              if (isProgressAck) {
                // Progress/reassurance update — keep streaming
              } else {
                wsHadTerminalEvent = true;
                wsClosedIntentionally = true;
                if (evt.stream_channel_id) {
                  runStreamHandoffRef.current = true;
                  ws.close();
                  const runId = parseRunIdFromStreamChannel(evt.stream_channel_id);
                  attachRunStream(
                    evt.stream_channel_id,
                    assistantId,
                    capturedGraphId,
                    runId
                      ? { runId, scope: "full", status: "running" }
                      : null,
                  );
                } else {
                  ws.close();
                  setIsStreaming(false);
                  setActiveChannelId(null);
                  if (capturedGraphId) void fetchThreads(capturedGraphId);
                }
              }
            } else if (evt.type === "chat_mutation") {
              if (evt.context_window) setContextWindow(evt.context_window);
              if (evt.detected_mode) setDetectedMode(evt.detected_mode);
              let nextMutationMessage: ChatMessage | null = null;
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? (() => {
                        const built = buildAutoApplyPreviewMessage(
                          m,
                          evt.mutation_plan ?? null,
                          evt.dry_run_result ?? null,
                          evt.message_id ?? null,
                          evt.content ?? m.content,
                          safeTokenUsage(evt.token_usage) ?? m.tokenUsage ?? null,
                        );
                        nextMutationMessage = built;
                        return built;
                      })()
                    : m,
                );
                persistThreadMessages(
                  capturedGraphId,
                  threadId ?? activeThreadIdRef.current,
                  updated,
                );
                return updated;
              });
              setIsStreaming(false);
              setActiveChannelId(null);
              wsHadTerminalEvent = true;
              wsClosedIntentionally = true;
              ws.close();
              if (capturedGraphId) void fetchThreads(capturedGraphId);
              if (
                nextMutationMessage &&
                shouldAutoApplyMutation(
                  (nextMutationMessage as ChatMessage).dryRunResult ?? null,
                  mutationConfirmMode,
                )
              ) {
                const autoMessage = nextMutationMessage;
                setTimeout(() => {
                  void applyMutationForMessage(autoMessage, { auto: true });
                }, 0);
              }
            } else if (evt.type === "chat_interrupted") {
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: (evt.content || m.content) + "\n\n*[generation stopped]*",
                        tokenUsage: safeTokenUsage(evt.token_usage) ?? m.tokenUsage ?? null,
                      }
                    : m,
                );
                persistThreadMessages(
                  capturedGraphId,
                  threadId ?? activeThreadIdRef.current,
                  updated,
                );
                return updated;
              });
              setIsStreaming(false);
              setActiveChannelId(null);
              wsHadTerminalEvent = true;
              wsClosedIntentionally = true;
              ws.close();
              if (capturedGraphId) void fetchThreads(capturedGraphId);
            } else if (evt.type === "chat_tool_call_start") {
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? (() => {
                        const nextToolCalls = [
                          ...(m.toolCalls || []),
                          {
                            id: evt.tool_call_id!,
                            toolName: evt.tool_name!,
                            argsPreview: evt.args_preview ?? "",
                            status: "running" as const,
                          },
                        ];
                        const prog = describeLatestToolProgress(nextToolCalls);
                        return {
                          ...m,
                          progressStatus: prog?.text ?? m.progressStatus,
                          progressFilePath: prog?.filePath ?? m.progressFilePath,
                          toolCalls: nextToolCalls,
                        };
                      })()
                    : m,
                );
                scheduleThreadPersist(
                  capturedGraphId,
                  threadId ?? activeThreadIdRef.current,
                  updated,
                );
                return updated;
              });
            } else if (evt.type === "chat_tool_call_result") {
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? (() => {
                        const nextToolCalls = (m.toolCalls || []).map((tc: ToolCallInfo) =>
                          tc.id === evt.tool_call_id
                            ? {
                                ...tc,
                                status: (evt.status as "success" | "error") ?? "success",
                                outputPreview: evt.output_preview,
                                durationMs: evt.duration_ms,
                              }
                            : tc,
                        );
                        const prog = describeLatestToolProgress(nextToolCalls);
                        return {
                          ...m,
                          progressStatus: prog?.text ?? m.progressStatus,
                          progressFilePath: prog?.filePath ?? m.progressFilePath,
                          toolCalls: nextToolCalls,
                        };
                      })()
                    : m,
                );
                scheduleThreadPersist(
                  capturedGraphId,
                  threadId ?? activeThreadIdRef.current,
                  updated,
                );
                return updated;
              });
            } else if (evt.type === "chat_graph_created") {
              if (capturedGraphId) void loadGraph(capturedGraphId);
            } else if (evt.type === "chat_validation_result") {
              if (evt.success === false && evt.errors?.length) {
                const errSummary = (evt.errors as string[]).slice(0, 3).join("; ");
                useGraphStore
                  .getState()
                  .addToast({ type: "error", message: `Validation failed: ${errSummary}` });
              }
            } else if (evt.type === "chat_file_attachment") {
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        attachments: (m.attachments || []).some(
                          (attachment) => attachment.path === (evt.path ?? ""),
                        )
                          ? (m.attachments || [])
                          : [
                              ...(m.attachments || []),
                              {
                                path: evt.path ?? "",
                                filename: evt.filename ?? "File",
                                size: evt.size,
                              },
                            ],
                      }
                    : m,
                );
                scheduleThreadPersist(
                  capturedGraphId,
                  threadId ?? activeThreadIdRef.current,
                  updated,
                );
                return updated;
              });
            } else if (evt.type === "chat_injected_message") {
              const injectedUserMsg: ChatMessage = {
                id: evt.inject_id ?? crypto.randomUUID(),
                role: "user",
                content: evt.content ?? "",
                timestamp: Date.now(),
              };
              setMessages((prev) => {
                const aidx = prev.findIndex((m) => m.id === assistantId);
                if (aidx === -1) return [...prev, injectedUserMsg];
                const before = prev.slice(0, aidx);
                const after = prev.slice(aidx);
                return [...before, injectedUserMsg, ...after];
              });
            } else if (evt.type === "ping") {
              // WS keepalive — no-op
            } else if (evt.type === "chat_error") {
              if (evt.error?.includes("revision_mismatch")) {
                setStaleRevision(true);
              }
              flushScheduledThreadPersist(
                capturedGraphId,
                threadId ?? activeThreadIdRef.current,
                messagesRef.current,
              );
              setError(evt.error ?? "Unknown error");
              setIsStreaming(false);
              setActiveChannelId(null);
              wsHadTerminalEvent = true;
              wsClosedIntentionally = true;
              ws.close();
              if (capturedGraphId) void fetchThreads(capturedGraphId);
              }

              if (
                (evt.type === "chat_complete" || evt.type === "chat_mutation") &&
                evt.revision_mismatch
              ) {
                setStaleRevision(true);
              }
            } catch {
              /* ignore parse errors */
            }
          };

          ws.onerror = () => {
            wsHadTransportError = true;
            flushScheduledThreadPersist(
              capturedGraphId,
              threadId ?? activeThreadIdRef.current,
              messagesRef.current,
            );
          };

          ws.onclose = (event) => {
            flushScheduledThreadPersist(
              capturedGraphId,
              threadId ?? activeThreadIdRef.current,
              messagesRef.current,
            );
            if (runStreamHandoffRef.current) {
              runStreamHandoffRef.current = false;
              return;
            }
            if (
              shouldReconnectStream({
                closedIntentionally: wsClosedIntentionally,
                activeChannelId: activeChannelIdRef.current,
                channelId,
                reconnectCount,
                closeCode: event.code,
                hadTransportError: wsHadTransportError,
                hadTerminalEvent: wsHadTerminalEvent,
              })
            ) {
              window.setTimeout(() => {
                connectToChatStream(channelId, {
                  reconnectCount: reconnectCount + 1,
                });
              }, getStreamReconnectDelayMs(reconnectCount));
              return;
            }
            if (
              wsClosedIntentionally ||
              ((event.code === 1000 || event.code === 1005) && wsHadTerminalEvent)
            ) {
              setIsStreaming(false);
              setActiveChannelId(null);
              return;
            }
            setIsStreaming(false);
            setActiveChannelId(null);
            void (async () => {
              const activeThreadForRecovery =
                threadId ?? activeThreadIdRef.current ?? null;
              const backendState = await classifyBackendDisconnectState(
                event.code,
                wsHadTransportError,
              );
              const restoredSnapshot =
                backendState === "unavailable"
                  ? false
                  : await restoreThreadSnapshotFromServer(
                      capturedGraphId,
                      activeThreadForRecovery,
                    );
              const disconnectError = getStreamDisconnectError({
                closedIntentionally: wsClosedIntentionally,
                closeCode: event.code,
                hadTransportError: wsHadTransportError,
                backendState,
                restoredSnapshot,
                recoveryHint:
                  backendState === "unavailable"
                    ? "Wait a few seconds for DAN to come back, then click Retry."
                    : "Click Retry to continue from the latest saved thread state.",
              });
              if (disconnectError) {
                setError(disconnectError);
                useGraphStore.getState().addToast({
                  type: "error",
                  message:
                    backendState === "unavailable"
                      ? "Local DAN backend unavailable"
                      : backendState === "restarted"
                        ? restoredSnapshot
                          ? "Recovered latest saved chat snapshot"
                          : "Chat stream lost after backend restart"
                        : "Chat stream disconnected",
                });
              }
              if (capturedGraphId && backendState !== "unavailable") {
                void fetchThreads(capturedGraphId);
              }
            })();
          };
        };

        connectToChatStream(stream_channel_id);
      } catch (err) {
        const msg =
          err instanceof Error ? err.message : "Failed to send message";
        setError(msg);
        setIsStreaming(false);
        setMessages((prev) => {
          const updated = prev.filter((m) => m.id !== assistantId);
          persistThreadMessages(
            capturedGraphId,
            threadId ?? activeThreadIdRef.current,
            updated,
            { silent: true },
          );
          return updated;
        });
      }
    },
    [
      attachRunStream,
      deriveDraftThreadTitleFromMessage,
      flushScheduledThreadPersist,
      fullScreen,
      graphId,
      graphRevision,
      isGraphDirty,
      inputText,
      isStreaming,
      mutationConfirmMode,
      persistThreadMessages,
      refreshActiveThreadTitle,
      restoreThreadSnapshotFromServer,
      scheduleThreadPersist,
      classifyBackendDisconnectState,
      fetchThreads,
    ],
  );
  sendMessageRef.current = sendMessage;

  const retryLast = useCallback(() => {
    const msgs = messagesRef.current;
    const lastUser = [...msgs].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    const idx = msgs.lastIndexOf(lastUser);
    const trimmed = msgs.slice(0, idx);
    setMessages(trimmed);
    setError(null);
    sendMessage(lastUser.content, trimmed);
  }, [sendMessage]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (mentionQuery !== null) {
        if (["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(e.key))
          return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage(e.currentTarget.value);
      }
    },
    [sendMessage, mentionQuery],
  );

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
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
      return;
    }

    const items = Array.from(e.clipboardData.items);
    const imageItem = items.find((item) => item.type.startsWith("image/"));
    if (imageItem) {
      const file = imageItem.getAsFile();
      if (file) {
        setPasteHint({ type: "image", file });
      }
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes("Files")) {
      e.preventDefault();
      setIsDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setIsDragOver(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) {
      setUserAttachments((prev) => [...prev, ...files]);
    }
  }, []);

  const handleStop = useCallback(async () => {
    const chId = activeChannelIdRef.current;
    if (!chId) return;
    try {
      await api.stopChatStream(chId);
    } catch (err) {
      console.warn("Failed to stop stream:", err);
    }
  }, []);

  const handleExport = useCallback(
    async (format: "md" | "json" = "md") => {
      const tid = activeThreadIdRef.current;
      if (!graphId || !tid) return;
      try {
        const { content } = await api.exportChatThread(graphId, tid, format);
        const ext = format === "json" ? "json" : "md";
        const blob = new Blob([content], { type: "text/plain" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `chat-${tid}.${ext}`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (err) {
        console.warn("Export failed:", err);
      }
    },
    [graphId],
  );

  const handleCopyMessage = useCallback((msg: ChatMessage) => {
    const role = msg.role.charAt(0).toUpperCase() + msg.role.slice(1);
    const md = `### ${role}\n\n${msg.content}`;
    navigator.clipboard.writeText(md).catch(() => {});
    useGraphStore.getState().addToast({ type: "info", message: "Copied to clipboard" });
  }, []);

  const handleSearch = useCallback(
    async (query: string) => {
      setSearchQuery(query);
      if (!query.trim()) {
        setSearchResults([]);
        return;
      }
      setIsSearching(true);
      try {
        const { results } = await api.searchChatThreads(query, graphId || undefined);
        setSearchResults(results);
      } catch (err) {
        console.warn("Search failed:", err);
        setSearchResults([]);
      } finally {
        setIsSearching(false);
      }
    },
    [graphId],
  );

  const handleExportThread = useCallback(
    async (threadId: string, format: "md" | "json" = "md") => {
      if (!graphId) return;
      try {
        const { content } = await api.exportChatThread(graphId, threadId, format);
        const ext = format === "json" ? "json" : "md";
        const blob = new Blob([content], { type: "text/plain" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `chat-${threadId}.${ext}`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (err) {
        console.warn("Export failed:", err);
      }
    },
    [graphId],
  );

  const handlePinThread = useCallback(
    async (threadId: string, pinned: boolean) => {
      if (!graphId) return;
      try {
        await api.pinChatThread(graphId, threadId, pinned);
        await fetchThreads(graphId);
      } catch (err) {
        console.warn("Failed to pin thread:", err);
      }
    },
    [graphId, fetchThreads],
  );

  // -------------------------------------------------------------------------
  // Thread management handlers
  // -------------------------------------------------------------------------

  const handleNewChat = useCallback(() => {
    if (isStreaming) detachCurrentStream();
    setActiveThreadId(null);
    setMessages([]);
    setThreadTitle("");
    setShowThreadList(false);
    setError(null);
    setIsStreaming(false);
    setSessionMarkers({});
    setPendingQueue([]);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [isStreaming, detachCurrentStream]);

  useEffect(() => {
    if (!fullScreen) return;
    const onPersistentSend = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.message) {
        sendMessage(detail.message);
      }
    };
    window.addEventListener("persistent-chat:send", onPersistentSend);
    return () => window.removeEventListener("persistent-chat:send", onPersistentSend);
  }, [sendMessage]);

  useEffect(() => {
    if (!fullScreen) return;
    if (!pendingChatMessage) return;
    setPendingChatMessage(null);
    sendMessage(pendingChatMessage);
  }, [fullScreen, pendingChatMessage, sendMessage, setPendingChatMessage]);

  useEffect(() => {
    if (!fullScreen) return;
    const handler = (e: globalThis.KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      if (e.key.toLowerCase() === "n" && !e.shiftKey) {
        e.preventDefault();
        handleNewChat();
      } else if (e.key.toLowerCase() === "l" && e.shiftKey) {
        e.preventDefault();
        setShowThreadList((prev) => !prev);
      } else if (e.key.toLowerCase() === "i" && !e.shiftKey) {
        e.preventDefault();
        setShowContextPanel((prev) => !prev);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [fullScreen, handleNewChat]);

  // Sync active thread to AppStore for cross-mode access
  useEffect(() => {
    useAppStore.getState().setActiveChatThread(activeThreadId, graphId);
  }, [activeThreadId, graphId]);

  useEffect(() => {
    if (!fullScreen) return;
    const handleSelectThread = (e: Event) => {
      const threadId = (e as CustomEvent).detail?.threadId;
      if (threadId && graphId && threadId !== activeThreadIdRef.current) {
        loadThread(graphId, threadId);
      }
    };
    const handleNewThread = () => handleNewChat();
    window.addEventListener("workspace:selectThread", handleSelectThread);
    window.addEventListener("workspace:newThread", handleNewThread);
    return () => {
      window.removeEventListener("workspace:selectThread", handleSelectThread);
      window.removeEventListener("workspace:newThread", handleNewThread);
    };
  }, [fullScreen, graphId, loadThread, handleNewChat]);

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
    (threadId: string) => {
      setConfirmDeleteThreadId(threadId);
    },
    [],
  );

  const executeDeleteThread = useCallback(
    async (threadId: string) => {
      if (!graphId) return;
      let snapshot: Record<string, unknown> | null = null;
      try {
        snapshot = await api.getChatThread(graphId, threadId);
      } catch { /* proceed without undo capability */ }
      try {
        persistenceCoordinatorRef.current?.cancelForThread(graphId, threadId);
        await api.deleteChatThread(graphId, threadId);
        const wasActive = activeThreadId === threadId;
        if (wasActive) {
          setActiveThreadId(null);
          setMessages([]);
          setThreadTitle("");
        }
        const remaining = await fetchThreads(graphId);
        if (wasActive && remaining.length > 0) {
          await loadThread(graphId, remaining[0].id);
        } else if (wasActive) {
          requestAnimationFrame(() => textareaRef.current?.focus());
        }
        if (snapshot) {
          const title = (snapshot as { title?: string }).title ?? threadId.slice(0, 8);
          const snapshotTitle = (snapshot as { title?: string }).title;
          const snapshotMessages = (snapshot as { messages?: unknown[] }).messages;
          const snapshotMode = (snapshot as { mode?: string }).mode;
          useGraphStore.getState().addToast({
            type: "info",
            message: `Deleted "${title}"`,
            durationMs: 8000,
            action: {
              label: "Undo",
              onClick: async () => {
                try {
                  const restored = await api.createChatThread(graphId);
                  const restoredId = (restored as { id?: string }).id;
                  if (!restoredId) {
                    throw new Error("Missing restored thread id");
                  }
                  await api.updateChatThread(graphId, restoredId, {
                    title: snapshotTitle,
                    messages: snapshotMessages ?? [],
                    mode: snapshotMode,
                  });
                  await fetchThreads(graphId);
                  if (wasActive) {
                    await loadThread(graphId, restoredId);
                  }
                  useGraphStore.getState().addToast({ type: "success", message: `Restored "${title}"` });
                } catch {
                  useGraphStore.getState().addToast({ type: "error", message: "Failed to restore conversation" });
                }
              },
            },
          });
        }
      } catch (err) {
        console.warn("Failed to delete thread:", err);
      }
    },
    [graphId, activeThreadId, fetchThreads, loadThread],
  );

  const handleRenameThread = useCallback(
    async (threadId: string, newTitle: string) => {
      if (!graphId) return;
      const trimmed = normalizeThreadTitleInput(newTitle);
      if (!trimmed) return;
      try {
        await api.updateChatThread(graphId, threadId, {
          title: trimmed,
        });
        setThreads((prev) =>
          prev.map((thread) =>
            thread.id === threadId
              ? {
                  ...thread,
                  title: trimmed,
                  updated_at: new Date().toISOString(),
                }
              : thread,
          ),
        );
        if (activeThreadIdRef.current === threadId) {
          setThreadTitle(trimmed);
        }
      } catch (err) {
        console.warn("Failed to rename thread:", err);
      }
    },
    [graphId],
  );

  const handleTitleSave = useCallback(
    async (newTitle: string) => {
      setEditingTitle(false);
      const trimmed = normalizeThreadTitleInput(newTitle);
      if (!trimmed) return;
      if (trimmed === threadTitle) return;
      setThreadTitle(trimmed);
      if (!graphId || !activeThreadId) return;
      try {
        await api.updateChatThread(graphId, activeThreadId, {
          title: trimmed,
        });
        setThreads((prev) =>
          prev.map((thread) =>
            thread.id === activeThreadId
              ? {
                  ...thread,
                  title: trimmed,
                  updated_at: new Date().toISOString(),
                }
              : thread,
          ),
        );
      } catch (err) {
        console.warn("Failed to update title:", err);
      }
    },
    [graphId, activeThreadId, threadTitle],
  );

  const handlePreviewMutation = useCallback((message: ChatMessage) => {
    setPreviewingMessage(message);
    setApplyError(null);
  }, []);

  const applyMutationForMessage = useCallback(
    async (
      msg: ChatMessage,
      options?: { auto?: boolean },
    ) => {
      if (!graphId || !msg.mutationPlan || applyingRef.current) return;
      applyingRef.current = true;
      setIsApplying(true);
      setApplyError(null);
      const plan = msg.mutationPlan as Record<string, unknown>;
      const preApplyGraph =
        useGraphStore.getState().danGraph as Record<string, unknown> | null;
      try {
        const res: ApplyMutationResult = await api.applyMutation(graphId, plan);
        if (!res.success) {
          const errMsg = res.errors?.[0]?.message ?? "Apply failed";
          setApplyError(
            res.stale_plan ? `${errMsg} (graph changed — try again)` : errMsg,
          );
          if (options?.auto) {
            setPreviewingMessage(msg);
            useGraphStore.getState().addToast({
              type: "error",
              message: errMsg,
            });
          }
          return;
        }
        if (res.diagnostics?.length) {
          const addToast = useGraphStore.getState().addToast;
          const diagMsg =
            res.diagnostics.length === 1
              ? res.diagnostics[0]
              : `${res.diagnostics.length} warnings: ${res.diagnostics[0]}${res.diagnostics.length > 1 ? "…" : ""}`;
          addToast({ type: "warning", message: diagMsg });
        }
        pushSnapshot();
        await loadGraph(graphId);

        const currentGraph = useGraphStore.getState().danGraph;
        const tid = activeThreadIdRef.current;
        if (tid && graphId && currentGraph) {
          api
            .saveChatCheckpoint(
              graphId,
              tid,
              msg.id,
              currentGraph as unknown as Record<string, unknown>,
            )
            .catch((err: unknown) =>
              console.warn("Failed to save checkpoint:", err),
            );
        }

        const updated = messagesRef.current.map((m) =>
          m.id === msg.id ? { ...m, mutationStatus: "applied" as const } : m,
        );
        setMessages(updated);
        const pastLen = useGraphStore.getState()._history.past.length;
        setSessionMarkers((prev) => ({
          ...prev,
          [msg.id]: { historyCursor: pastLen > 0 ? pastLen - 1 : 0 },
        }));
        if (tid && graphId) {
          api
            .updateChatThread(graphId, tid, {
              messages: updated.map(toBackendMessage),
            })
            .catch((err: unknown) =>
              console.warn("Failed to save thread:", err),
            );
        }
        if (!options?.auto) {
          setPreviewingMessage(null);
        }
        setApplyError(null);
        // Detect build-from-intent completion: graph went from empty to non-empty
        const wasEmpty =
          ((preApplyGraph as { nodes?: unknown[] } | null)?.nodes?.length ?? 0) === 0;
        const isNowPopulated = (useGraphStore.getState().danGraph?.nodes?.length ?? 0) > 0;
        if (wasEmpty && isNowPopulated) {
          setBuildJustCompleted(true);
        }
        if (options?.auto) {
          useGraphStore.getState().addToast({
            type: "success",
            message: summarizeMutationPlan(plan),
          });
        }
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : "Apply failed";
        setApplyError(errorMessage);
        if (options?.auto) {
          setPreviewingMessage(msg);
          useGraphStore.getState().addToast({
            type: "error",
            message: errorMessage,
          });
        }
      } finally {
        applyingRef.current = false;
        setIsApplying(false);
      }
    },
    [graphId, pushSnapshot, loadGraph],
  );

  const handleApplyMutation = useCallback(async () => {
    const msg = previewingMessage;
    if (!msg) return;
    await applyMutationForMessage(msg);
  }, [previewingMessage, applyMutationForMessage]);

  const handleRejectMutation = useCallback(() => {
    const msg = previewingMessage;
    if (!msg) return;
    const updated = messages.map((m) =>
      m.id === msg.id ? { ...m, mutationStatus: "rejected" as const } : m,
    );
    setMessages(updated);
    const tid = activeThreadIdRef.current;
    if (tid && graphId) {
      api
        .updateChatThread(graphId, tid, {
          messages: updated.map(toBackendMessage),
        })
        .catch((err: unknown) => console.warn("Failed to save thread:", err));
    }
    setPreviewingMessage(null);
    setApplyError(null);
  }, [previewingMessage, graphId, messages]);

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

  // -------------------------------------------------------------------------
  // Shared conversation content (used in both fullScreen and sidebar layouts)
  // -------------------------------------------------------------------------
  const conversationHeader = fullScreen ? (
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-200 flex-shrink-0 bg-gray-50/30">
      <div className="flex items-center gap-2 min-w-0 flex-1">
        <button
          onClick={() => setShowThreadList(!showThreadList)}
          className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors ${
            showThreadList ? "bg-gray-200 text-gray-700" : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
          }`}
          title="Chat history (⌘⇧L)"
        >
          <History size={13} />
          <span>History</span>
        </button>
        <div className="w-px h-4 bg-gray-200" />
        {editingTitle ? (
          <input
            autoFocus
            defaultValue={threadTitle}
            onBlur={(e) => handleTitleSave(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleTitleSave((e.target as HTMLInputElement).value);
              if (e.key === "Escape") setEditingTitle(false);
            }}
            className="text-sm font-semibold text-gray-800 bg-gray-50 border border-gray-200 rounded px-1.5 py-0.5 outline-none focus:border-indigo-300 min-w-0 flex-1"
          />
        ) : (
          <div className="flex items-center gap-1 min-w-0 flex-1">
            <span
              onClick={() => activeThreadId && setEditingTitle(true)}
              className={`text-sm font-semibold text-gray-800 truncate transition-colors ${
                activeThreadId
                  ? "cursor-pointer hover:text-indigo-600"
                  : ""
              }`}
              title={activeThreadId ? "Rename chat title" : undefined}
            >
              {getDisplayThreadTitle(threadTitle, "New conversation")}
            </span>
            {activeThreadId && (
              <button
                onClick={() => setEditingTitle(true)}
                className="text-gray-300 hover:text-indigo-500 p-0.5 rounded transition-colors flex-shrink-0"
                title="Rename chat title"
              >
                <PencilLine size={12} />
              </button>
            )}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {(lastPromptTokens > 0 || totalTokens > 0) && (
          <span className="text-[10px] text-gray-400 tabular-nums" title={`${totalTokens.toLocaleString()} total tokens used`}>
            {contextWindow > 0 && lastPromptTokens > 0
              ? `~${formatTokenCount(lastPromptTokens)} / ${formatTokenCount(contextWindow)}`
              : `${totalTokens.toLocaleString()} tok`}
          </span>
        )}
        <button onClick={() => handleExport("md")} className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors" title="Export as Markdown">
          <Download size={13} />
        </button>
        <button
          onClick={() => setShowContextPanel((prev) => !prev)}
          className={`p-0.5 rounded transition-colors ${showContextPanel ? "text-indigo-500 bg-indigo-50" : "text-gray-400 hover:text-gray-600"}`}
          title="Context panel (⌘I)"
        >
          <Info size={13} />
        </button>
        <button
          onClick={handleNewChat}
          className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium text-gray-600 hover:bg-gray-100 hover:text-gray-800 transition-colors"
          title="New chat (⌘N)"
        >
          <Plus size={13} />
          <span>New</span>
        </button>
      </div>
    </div>
  ) : (
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-200 flex-shrink-0">
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
              if (e.key === "Enter") handleTitleSave((e.target as HTMLInputElement).value);
              if (e.key === "Escape") setEditingTitle(false);
            }}
            className="text-sm font-semibold text-gray-800 bg-gray-50 border border-gray-200 rounded px-1.5 py-0.5 outline-none focus:border-indigo-300 min-w-0 flex-1"
          />
        ) : (
          <div className="flex items-center gap-1 min-w-0 flex-1">
            <span
              onClick={() => activeThreadId && setEditingTitle(true)}
              className={`text-sm font-semibold text-gray-800 truncate transition-colors ${
                activeThreadId
                  ? "cursor-pointer hover:text-indigo-600"
                  : ""
              }`}
              title={activeThreadId ? "Rename chat title" : undefined}
            >
              {getDisplayThreadTitle(threadTitle, "Untitled chat")}
            </span>
            {activeThreadId && (
              <button
                onClick={() => setEditingTitle(true)}
                className="text-gray-300 hover:text-indigo-500 p-0.5 rounded transition-colors flex-shrink-0"
                title="Rename chat title"
              >
                <PencilLine size={12} />
              </button>
            )}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {(lastPromptTokens > 0 || totalTokens > 0) && (
          <span className="text-[10px] text-gray-400 tabular-nums" title={`${totalTokens.toLocaleString()} total tokens used`}>
            {contextWindow > 0 && lastPromptTokens > 0
              ? `~${formatTokenCount(lastPromptTokens)} / ${formatTokenCount(contextWindow)}`
              : `${totalTokens.toLocaleString()} tok`}
          </span>
        )}
        <button onClick={() => handleExport("md")} className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors" title="Export as Markdown">
          <Download size={13} />
        </button>
        <button onClick={() => setChatOpen(false)} className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors">
          <X size={14} />
        </button>
      </div>
    </div>
  );

  const modeSelector = (
    <div className={`flex items-center gap-1 px-3 py-1.5 border-b border-gray-100 bg-gray-50/50 flex-shrink-0 ${fullScreen ? "justify-center" : ""}`}>
      <div className={`flex items-center gap-1 ${fullScreen ? "max-w-3xl w-full" : ""}`}>
        {(["auto", "agent", "ask", "plan", "debug"] as const).map((m) => {
          const cfg = MODE_CONFIG[m];
          const Icon = cfg.icon;
          const active = chatMode === m;
          return (
            <button
              key={m}
              onClick={() => {
                useGraphStore.getState().setChatMode(m);
                if (m !== "auto") setDetectedMode(null);
                const tid = activeThreadIdRef.current;
                const gid = graphId;
                if (tid && gid) {
                  api.updateChatThread(gid, tid, { mode: m }).catch(() => {});
                }
              }}
              className={`flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded-md transition-colors ${
                active
                  ? "bg-white text-gray-800 shadow-sm border border-gray-200"
                  : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"
              }`}
            >
              <Icon size={11} />
              {cfg.label}
              {m === "auto" && active && detectedMode && (
                <span className="text-[9px] text-violet-600 font-normal">
                  → {detectedMode.charAt(0).toUpperCase() + detectedMode.slice(1)}
                </span>
              )}
            </button>
          );
        })}
        {!fullScreen && (
          <div className="ml-auto">
            <button
              onClick={toggleMutationConfirmMode}
              className={`flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md transition-colors ${
                mutationConfirmMode
                  ? "bg-amber-50 text-amber-700 border border-amber-200"
                  : "bg-emerald-50 text-emerald-700 border border-emerald-200"
              }`}
              title={mutationConfirmMode ? "Review diffs before applying" : "Auto-apply mutations"}
            >
              {mutationConfirmMode ? <PencilLine size={11} /> : <CheckCircle2 size={11} />}
              {mutationConfirmMode ? "Review diffs" : "Auto-apply"}
            </button>
          </div>
        )}
      </div>
    </div>
  );

  // Derive typing indicator phase from streaming state
  const typingPhase: "thinking" | "executing" | null = (() => {
    if (!isStreaming) return null;
    const lastMsg = messages.length > 0 ? messages[messages.length - 1] : null;
    if (!lastMsg || lastMsg.role !== "assistant") return null;
    if (lastMsg.toolCalls?.some((tc) => tc.status === "running")) return "executing";
    if (!lastMsg.content) return "thinking";
    return null;
  })();

  // Derive follow-up suggestions from last assistant message
  const followupSuggestions = useMemo(() => {
    if (isStreaming || messages.length === 0 || inputText.length > 0) return [];
    const lastMsg = messages[messages.length - 1];
    if (lastMsg.role !== "assistant" || !lastMsg.content) return [];
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    return generateFollowups(lastMsg.content, lastUser?.content ?? "");
  }, [isStreaming, messages, inputText]);

  const messagesArea = (
    <div className={`flex-1 overflow-y-auto py-4 min-h-0 ${fullScreen ? "px-4" : "px-3"}`}>
      <div className={fullScreen ? "max-w-3xl mx-auto" : ""}>
        {messages.length === 0 ? (
          <EmptyState
            onSelect={(t) => sendMessage(t)}
            mode={chatMode}
            isEmptyGraph={(danGraph?.nodes?.length ?? 0) === 0}
            fullScreen={fullScreen}
          />
        ) : (
          <>
            {messages.map((m, i) => (
              <div key={m.id}>
                <ChatMessageBubble
                  message={m}
                  sessionMarker={sessionMarkers[m.id]}
                  onRevert={() => handleRevert(m.id)}
                  onPreviewMutation={chatMode === "ask" ? undefined : handlePreviewMutation}
                  onCopyMarkdown={() => handleCopyMessage(m)}
                  isStreaming={isAssistantBubbleStreaming({
                    role: m.role,
                    isLastMessage: i === messages.length - 1,
                    isStreaming,
                    isRunStreaming,
                  })}
                />
                {chatMode === "plan" &&
                  m.role === "assistant" &&
                  !m.mutationPlan &&
                  m.content &&
                  i === messages.length - 1 &&
                  !isStreaming && (
                    <PlanApprovalButtons
                      onApprove={() =>
                        sendMessage("Approved. Please generate the mutation plan now.", undefined, "agent")
                      }
                      onRevise={() => textareaRef.current?.focus()}
                    />
                  )}
              </div>
            ))}

            {!isStreaming && followupSuggestions.length > 0 && (
              <SuggestedFollowups
                suggestions={followupSuggestions}
                onSelect={(text) => sendMessage(text)}
              />
            )}

            {typingPhase && <TypingIndicator phase={typingPhase} />}

            {fullScreen && escalation && (
              <div className="flex items-start gap-3 my-3">
                <div className="flex-1 min-w-0">
                  <EscalationBanner suggestion={escalation} onDismiss={() => setEscalation(null)} />
                  <div className="relative inline-block ml-4 mt-1">
                    <button
                      onClick={() => setSwitchPrefMenu((v) => !v)}
                      className="text-[10px] text-gray-400 hover:text-gray-600 transition-colors flex items-center gap-0.5"
                      title="Switch preference"
                    >
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                      Preference
                    </button>
                    {switchPrefMenu && (
                      <div className="absolute bottom-full mb-1 left-0 z-50 rounded border border-gray-200 bg-white shadow-lg py-1 min-w-[130px]">
                        {(["always", "ask", "never"] as SwitchAction[]).map((action) => {
                          const current = getSwitchPreference(escalation.targetMode);
                          return (
                            <button
                              key={action}
                              className={`w-full text-left px-3 py-1 text-[11px] hover:bg-gray-100 ${current === action ? "text-indigo-600 font-medium" : "text-gray-600"}`}
                              onClick={() => {
                                setSwitchPreference(escalation.targetMode, action);
                                setSwitchPrefMenu(false);
                                if (action === "never") setEscalation(null);
                              }}
                            >
                              {action === "always" ? "Always switch" : action === "ask" ? "Always ask" : "Never switch"}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
                <ModePreview mode={escalation.targetMode} onClick={() => setMode(escalation.targetMode)} />
              </div>
            )}

            {previewingMessage && !previewingMessage.dryRunResult?.new_graph && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
                <div className="bg-white rounded-xl shadow-2xl max-w-sm w-full p-6 text-center">
                  <p className="text-sm text-gray-500 mb-4">
                    Preview unavailable — the proposed changes may have been saved before this feature, or the graph has changed.
                  </p>
                  <button
                    onClick={() => { setPreviewingMessage(null); setApplyError(null); }}
                    className="px-4 py-2 text-xs font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
                  >
                    Close
                  </button>
                </div>
              </div>
            )}

            {previewingMessage && previewingMessage.dryRunResult?.new_graph && danGraph && (
              <GraphDiffPreview
                diff={computeGraphDiff(
                  danGraph as unknown as Record<string, unknown>,
                  previewingMessage.dryRunResult.new_graph as Record<string, unknown>,
                )}
                onApplyAll={handleApplyMutation}
                onApplySelected={() => {}}
                onReject={handleRejectMutation}
                onClose={() => { setPreviewingMessage(null); setApplyError(null); }}
                allowPartialApply={false}
                disabled={isApplying}
                mutationSource={(previewingMessage.mutationPlan as Record<string, unknown> | undefined)?.metadata
                  ? ((previewingMessage.mutationPlan as Record<string, unknown>).metadata as Record<string, unknown>)?.source as string | undefined
                  : undefined}
                applyError={applyError}
              />
            )}

            {staleRevision && (
              <div className="flex items-center gap-2 mb-3 px-2 py-2 bg-amber-50 border border-amber-200 rounded-xl text-sm text-amber-700">
                <span className="flex-1">Graph changed since your last message — context may be stale.</span>
                <button onClick={() => { setStaleRevision(false); retryLast(); }} className="flex items-center gap-1 text-amber-600 hover:text-amber-800 text-xs font-medium flex-shrink-0"><RotateCcw size={12} /> Retry with current</button>
                <button onClick={() => setStaleRevision(false)} className="text-amber-500 hover:text-amber-700 text-xs font-medium flex-shrink-0">Dismiss</button>
              </div>
            )}

            {error && (
              <div className="flex items-start gap-2 mb-3 px-2 py-2 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
                <span className="flex-1">{error}</span>
                <button onClick={retryLast} className="flex items-center gap-1 text-red-600 hover:text-red-800 font-medium text-xs flex-shrink-0">
                  <RotateCcw size={12} /> Retry
                </button>
              </div>
            )}
          </>
        )}
        {buildJustCompleted && graphId && (
          <div className="flex flex-col items-center gap-2 my-4 px-4 py-3 bg-green-50 rounded-xl border border-green-100">
            <span className="text-xs text-green-700 font-medium">Workflow built successfully</span>
            <button
              onClick={() => { setBuildJustCompleted(false); useGraphStore.getState().startRun(); }}
              className="flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg transition-colors shadow-sm"
            >
              <Play size={12} /> Run this workflow
            </button>
            <span className="text-[10px] text-gray-400">or continue chatting to refine</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
    </div>
  );

  const queueStrip = pendingQueue.length > 0 ? (
    <div className={`flex-shrink-0 border-t border-gray-100 bg-gray-50/50 max-h-36 overflow-y-auto ${fullScreen ? "px-4 py-2" : "px-3 py-2"}`}>
      <div className={`${fullScreen ? "max-w-3xl mx-auto" : ""} space-y-1`}>
        <div className="text-[10px] font-medium text-gray-500 px-1">
          Queued messages ({pendingQueue.length})
        </div>
        {pendingQueue.map((item, idx) => (
          <div
            key={item.id}
            className="flex items-center gap-1.5 bg-white rounded-lg px-2.5 py-1.5 group border border-gray-100"
          >
            <GripVertical size={12} className="text-gray-300 flex-shrink-0" />
            <span className="flex-1 text-xs text-gray-700 truncate min-w-0">
              {item.content}
            </span>
            <div className="flex items-center gap-0.5 flex-shrink-0">
              {idx === 0 && isStreaming && activeChannelId && (
                <button
                  onClick={() => {
                    const chId = activeChannelIdRef.current;
                    if (!chId) return;
                    api.injectChatMessage(chId, item.content, item.id).then(() => {
                      setPendingQueue((q) => q.filter((_, i) => i !== 0));
                    }).catch((err) => {
                      console.warn("Failed to inject message:", err);
                    });
                  }}
                  className="text-xs text-indigo-500 hover:text-indigo-700 px-1.5 py-0.5 rounded bg-indigo-50 hover:bg-indigo-100 transition-colors font-medium"
                  title="Inject into current session — DAN will see this in the next tool round"
                >
                  Push
                </button>
              )}
              <button
                onClick={() => {
                  setPendingQueue((q) => q.filter((_, i) => i !== idx));
                  setInputText(item.content);
                  requestAnimationFrame(() => textareaRef.current?.focus());
                }}
                className="text-gray-400 hover:text-indigo-500 p-0.5 rounded transition-colors"
                title="Edit this message"
              >
                <PencilLine size={11} />
              </button>
              {idx > 0 && (
                <button
                  onClick={() => {
                    setPendingQueue((q) => {
                      const next = [...q];
                      [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
                      return next;
                    });
                  }}
                  className="text-gray-400 hover:text-indigo-500 p-0.5 rounded transition-colors"
                  title="Move up in queue"
                >
                  <ArrowUp size={11} />
                </button>
              )}
              <button
                onClick={() => setPendingQueue((q) => q.filter((_, i) => i !== idx))}
                className="text-gray-400 hover:text-red-500 p-0.5 rounded transition-colors"
                title="Remove from queue"
              >
                <X size={11} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  ) : null;

  const inputArea = (
    <div className={`flex-shrink-0 border-t border-gray-200 ${fullScreen ? "px-4 py-4 bg-white" : "p-3"}`}>
      <div className={fullScreen ? "max-w-3xl mx-auto" : ""}>
        {fullScreen && !isStreaming && messages.length > 0 && (
          <RecentCommandsBar
            onSelect={(cmd) => {
              setInputText(cmd);
              requestAnimationFrame(() => textareaRef.current?.focus());
            }}
          />
        )}
        {userAttachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2 px-1">
            {userAttachments.map((f, i) => (
              <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] text-gray-600 bg-gray-100 border border-gray-200 rounded-full">
                <Upload size={10} className="text-gray-400" />
                {f.name.length > 20 ? f.name.slice(0, 18) + "…" : f.name}
                <button
                  onClick={() => setUserAttachments((prev) => prev.filter((_, j) => j !== i))}
                  className="text-gray-400 hover:text-red-500 transition-colors"
                >
                  <X size={10} />
                </button>
              </span>
            ))}
          </div>
        )}
        {inputText.startsWith("/") && inputText.length < 15 && (
          <SlashCommandPopup
            filter={inputText}
            onSelect={(cmd) => {
              setInputText(cmd + " ");
              requestAnimationFrame(() => textareaRef.current?.focus());
            }}
          />
        )}
        <div className={`flex items-end gap-2 border border-gray-200 rounded-xl px-3 py-2.5 focus-within:shadow-md focus-within:border-indigo-300 transition-all ${fullScreen ? "shadow-sm" : "focus-within:shadow-sm"}`}>
          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => { setInputText(e.target.value); setPasteHint(null); requestAnimationFrame(checkMention); }}
            onKeyDown={handleKeyDown}
            onKeyUp={checkMention}
            onClick={checkMention}
            onPaste={handlePaste}
            placeholder={
              isStreaming
                ? "Type to queue next message…"
                : fullScreen
                ? "Message DAN… (@ to mention, / for commands)"
                : chatMode === "ask" ? "Ask about your workflow…"
                : chatMode === "plan" ? "Describe what changes to plan…"
                : chatMode === "debug" ? "Describe the issue or ask to diagnose…"
                : chatMode === "auto" ? "Type anything — mode auto-detected… (@ to mention)"
                : "Ask about your workflow… (@ to mention)"
            }
            rows={1}
            className={`flex-1 resize-none text-gray-900 placeholder-gray-400 bg-transparent outline-none max-h-[160px] leading-snug ${fullScreen ? "text-[15px] min-h-[28px]" : "text-sm min-h-[24px]"}`}
          />
          {mentionQuery !== null && (
            <MentionAutocomplete
              query={mentionQuery}
              anchorRect={mentionAnchor}
              onSelect={handleMentionSelect}
              onDismiss={dismissMention}
            />
          )}
          {isStreaming || isRunStreaming ? (
            <div className="flex items-center gap-1 flex-shrink-0">
              {inputText.trim() && (
                <button
                  onClick={() => sendMessage()}
                  className="text-indigo-500 hover:text-indigo-700 transition-colors p-0.5"
                  title="Queue message"
                >
                  <ArrowUp size={fullScreen ? 18 : 16} />
                </button>
              )}
              {isStreaming && activeChannelId ? (
                <button onClick={handleStop} className="text-red-500 hover:text-red-700 transition-colors p-0.5" title="Stop generation">
                  <Square size={fullScreen ? 18 : 16} />
                </button>
              ) : (
                <span className="text-gray-300 p-0.5" title="Waiting for run updates">
                  <Loader2 size={fullScreen ? 18 : 16} className="animate-spin" />
                </span>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-1 flex-shrink-0">
              {fullScreen && (
                <VoiceInput
                  size={fullScreen ? 16 : 14}
                  onTranscript={(text) => setInputText((prev) => prev + text)}
                />
              )}
              <button
                onClick={() => sendMessage()}
                disabled={!inputText.trim()}
                className="text-indigo-500 hover:text-indigo-700 disabled:text-gray-300 transition-colors p-0.5"
              >
                <Send size={fullScreen ? 18 : 16} />
              </button>
            </div>
          )}
        </div>
        <div className="text-[10px] text-gray-400 mt-1.5 px-1">
          {isStreaming
            ? "Enter to queue · Shift+Enter for newline" + (fullScreen ? " · ⌘K command palette" : "")
            : "Enter to send · Shift+Enter for newline" + (fullScreen ? " · ⌘K command palette" : "")}
        </div>
        {pasteHint && (
          <SmartPasteHint
            hint={pasteHint}
            onAccept={() => {
              if (pasteHint.type === "url" && pasteHint.value) {
                setInputText((prev) =>
                  prev.replace(pasteHint.value!, `Fetch and summarize: ${pasteHint.value}`),
                );
              } else if (pasteHint.type === "code" && pasteHint.value) {
                setInputText((prev) =>
                  prev.replace(pasteHint.value!, "```\n" + pasteHint.value + "\n```"),
                );
              } else if (pasteHint.type === "image" && pasteHint.file) {
                setUserAttachments((prev) => [...prev, pasteHint.file!]);
              }
              setPasteHint(null);
            }}
            onDismiss={() => setPasteHint(null)}
          />
        )}
      </div>
    </div>
  );

  // -------------------------------------------------------------------------
  // Full-screen layout: thread sidebar + conversation side-by-side
  // -------------------------------------------------------------------------
  if (fullScreen) {
    return (
      <div
        className="absolute inset-0 flex bg-white overflow-hidden"
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {isDragOver && (
          <div className="absolute inset-0 z-50 flex items-center justify-center bg-indigo-500/10 border-2 border-dashed border-indigo-400/30 rounded-lg pointer-events-none">
            <div className="flex flex-col items-center gap-2 text-indigo-400">
              <Upload size={32} />
              <span className="text-sm font-medium">Drop files to attach</span>
            </div>
          </div>
        )}
        {showThreadList && (
          <div className="w-72 flex-shrink-0 border-r border-gray-200 flex flex-col">
            <ThreadListView
              threads={threads}
              loading={loadingThreads}
              onNewChat={handleNewChat}
              onSelectThread={(id) => { handleSelectThread(id); setShowThreadList(false); }}
              onDeleteThread={handleDeleteThread}
              onRenameThread={handleRenameThread}
              onPinThread={handlePinThread}
              onExportThread={handleExportThread}
              onClose={() => setShowThreadList(false)}
              searchQuery={searchQuery}
              searchResults={searchResults}
              isSearching={isSearching}
              onSearch={handleSearch}
              bgStreamIds={bgStreamIds}
              onContextMenu={setThreadContextMenu}
            />
          </div>
        )}
        <div className="flex flex-col flex-1 min-w-0 min-h-0 overflow-hidden">
          {conversationHeader}
          {modeSelector}
          {messagesArea}
          {queueStrip}
          {inputArea}
        </div>
        {showContextPanel && (
          <ContextPanel
            messages={messages}
            workspaceName={graphId ?? "Scratch"}
            onClose={() => setShowContextPanel(false)}
          />
        )}
        {threadContextMenu && (
          <ThreadContextMenu
            x={threadContextMenu.x}
            y={threadContextMenu.y}
            threadId={threadContextMenu.threadId}
            threadTitle={threadContextMenu.threadTitle}
            pinned={threadContextMenu.pinned}
            onPin={(id, pin) => { handlePinThread(id, pin); setThreadContextMenu(null); }}
            onRename={() => setThreadContextMenu(null)}
            onExport={(id, fmt) => { handleExportThread(id, fmt); setThreadContextMenu(null); }}
            onDelete={(id) => { handleDeleteThread(id); setThreadContextMenu(null); }}
            onClose={() => setThreadContextMenu(null)}
          />
        )}
        <ConfirmDialog
          open={confirmDeleteThreadId !== null}
          title="Delete conversation"
          message="Are you sure you want to delete this conversation?"
          confirmLabel="Delete"
          confirmVariant="danger"
          onConfirm={() => {
            if (confirmDeleteThreadId) executeDeleteThread(confirmDeleteThreadId);
            setConfirmDeleteThreadId(null);
          }}
          onCancel={() => setConfirmDeleteThreadId(null)}
        />
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Sidebar layout: toggle between thread list and conversation
  // -------------------------------------------------------------------------
  return (
    <div
      className="flex flex-shrink-0 h-full border-l border-gray-200 bg-white relative"
      style={{ width: panelWidth }}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {isDragOver && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-indigo-500/10 border-2 border-dashed border-indigo-400/30 rounded-lg pointer-events-none">
          <div className="flex flex-col items-center gap-2 text-indigo-400">
            <Upload size={24} />
            <span className="text-xs font-medium">Drop files to attach</span>
          </div>
        </div>
      )}
      <div
        onMouseDown={onResizeStart}
        className="w-1 cursor-col-resize hover:bg-indigo-200 active:bg-indigo-300 transition-colors flex-shrink-0"
      />
      <div className="flex flex-col flex-1 min-w-0 min-h-0">
        {showThreadList ? (
          <ThreadListView
            threads={threads}
            loading={loadingThreads}
            onNewChat={handleNewChat}
            onSelectThread={handleSelectThread}
            onDeleteThread={handleDeleteThread}
            onRenameThread={handleRenameThread}
            onPinThread={handlePinThread}
            onExportThread={handleExportThread}
            onClose={() => setChatOpen(false)}
            searchQuery={searchQuery}
            searchResults={searchResults}
            isSearching={isSearching}
            onSearch={handleSearch}
            bgStreamIds={bgStreamIds}
            onContextMenu={setThreadContextMenu}
          />
        ) : (
          <>
            {conversationHeader}
            {modeSelector}
            {messagesArea}
            {queueStrip}
            {inputArea}
          </>
        )}
      </div>
      {threadContextMenu && (
        <ThreadContextMenu
          x={threadContextMenu.x}
          y={threadContextMenu.y}
          threadId={threadContextMenu.threadId}
          threadTitle={threadContextMenu.threadTitle}
          pinned={threadContextMenu.pinned}
          onPin={(id, pin) => { handlePinThread(id, pin); setThreadContextMenu(null); }}
          onRename={() => setThreadContextMenu(null)}
          onExport={(id, fmt) => { handleExportThread(id, fmt); setThreadContextMenu(null); }}
          onDelete={(id) => { handleDeleteThread(id); setThreadContextMenu(null); }}
          onClose={() => setThreadContextMenu(null)}
        />
      )}
      <ConfirmDialog
        open={confirmDeleteThreadId !== null}
        title="Delete conversation"
        message="Are you sure you want to delete this conversation?"
        confirmLabel="Delete"
        confirmVariant="danger"
        onConfirm={() => {
          if (confirmDeleteThreadId) executeDeleteThread(confirmDeleteThreadId);
          setConfirmDeleteThreadId(null);
        }}
        onCancel={() => setConfirmDeleteThreadId(null)}
      />
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
  onRenameThread,
  onPinThread,
  onExportThread,
  onClose,
  searchQuery,
  searchResults,
  isSearching,
  onSearch,
  bgStreamIds,
  onContextMenu,
}: {
  threads: ChatThreadSummary[];
  loading: boolean;
  onNewChat: () => void;
  onSelectThread: (id: string) => void;
  onDeleteThread: (id: string) => void;
  onRenameThread: (id: string, title: string) => void;
  onPinThread: (id: string, pinned: boolean) => void;
  onExportThread: (id: string, format: "md" | "json") => void;
  onClose?: () => void;
  searchQuery: string;
  searchResults: Array<{
    thread_id: string;
    thread_title: string;
    workflow_id: string;
    message_id: string;
    message_preview: string;
    timestamp: string;
  }>;
  isSearching: boolean;
  onSearch: (query: string) => void;
  bgStreamIds?: Set<string>;
  onContextMenu?: (menu: { x: number; y: number; threadId: string; threadTitle: string; pinned: boolean }) => void;
}) {
  const pinnedThreads = threads.filter((t) => (t as ChatThreadSummary & { pinned?: boolean }).pinned);
  const unpinnedThreads = threads.filter((t) => !(t as ChatThreadSummary & { pinned?: boolean }).pinned);
  const sortedUnpinned = [...unpinnedThreads].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  );
  const sortedPinned = [...pinnedThreads].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  );

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
          {onClose && (
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Search bar */}
      <div className="px-3 py-2 border-b border-gray-100 flex-shrink-0">
        <div className="flex items-center gap-2 bg-gray-50 rounded-lg px-2.5 py-1.5">
          <Search size={12} className="text-gray-400 flex-shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search conversations…"
            className="text-xs text-gray-700 placeholder-gray-400 bg-transparent outline-none flex-1 min-w-0"
          />
          {isSearching && <Loader2 size={12} className="text-gray-300 animate-spin flex-shrink-0" />}
          {searchQuery && !isSearching && (
            <button
              onClick={() => onSearch("")}
              className="text-gray-400 hover:text-gray-600"
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Show search results if there's a query */}
        {searchQuery.trim() ? (
          searchResults.length === 0 && !isSearching ? (
            <div className="text-center py-8 text-xs text-gray-400">
              No results for "{searchQuery}"
            </div>
          ) : (
            <div className="py-1">
              {searchResults.map((r, i) => (
                <div
                  key={`${r.thread_id}-${r.message_id}-${i}`}
                  onClick={() => onSelectThread(r.thread_id)}
                  className="px-3 py-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
                >
                  <div className="text-xs font-medium text-gray-700 truncate">
                    {r.thread_title}
                  </div>
                  <div className="text-[10px] text-gray-400 mt-0.5 line-clamp-2">
                    {r.message_preview}
                  </div>
                </div>
              ))}
            </div>
          )
        ) : loading ? (
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
            {sortedPinned.length > 0 && (
              <>
                <div className="px-3 pt-2 pb-1">
                  <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-1">
                    <Pin size={9} className="text-indigo-400" />
                    Pinned
                  </span>
                </div>
                {sortedPinned.map((t) => (
                  <ThreadRow
                    key={t.id}
                    thread={t}
                    onSelect={() => onSelectThread(t.id)}
                    onDelete={() => onDeleteThread(t.id)}
                    onRename={(title) => onRenameThread(t.id, title)}
                    onPin={() => onPinThread(t.id, false)}
                    onExport={(fmt) => onExportThread(t.id, fmt)}
                    pinned
                    isStreamingInBg={bgStreamIds?.has(t.id) ?? false}
                    onContextMenu={onContextMenu}
                  />
                ))}
                {sortedUnpinned.length > 0 && (
                  <div className="mx-3 my-1 border-t border-gray-100" />
                )}
              </>
            )}
            {groupThreadsByDate(sortedUnpinned).map(({ label, threads: group }) => (
              <div key={label}>
                <div className="px-3 pt-2 pb-1">
                  <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
                    {label}
                  </span>
                </div>
                {group.map((t) => (
                  <ThreadRow
                    key={t.id}
                    thread={t}
                    onSelect={() => onSelectThread(t.id)}
                    onDelete={() => onDeleteThread(t.id)}
                    onRename={(title) => onRenameThread(t.id, title)}
                    onPin={() =>
                      onPinThread(
                        t.id,
                        !(t as ChatThreadSummary & { pinned?: boolean }).pinned,
                      )
                    }
                    onExport={(fmt) => onExportThread(t.id, fmt)}
                    pinned={false}
                    isStreamingInBg={bgStreamIds?.has(t.id) ?? false}
                    onContextMenu={onContextMenu}
                  />
                ))}
              </div>
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
  onRename,
  onPin,
  pinned,
  isStreamingInBg,
  onContextMenu,
}: {
  thread: ChatThreadSummary;
  onSelect: () => void;
  onDelete?: () => void;
  onRename: (title: string) => void;
  onPin: () => void;
  onExport?: (format: "md" | "json") => void;
  pinned: boolean;
  isStreamingInBg?: boolean;
  onContextMenu?: (menu: { x: number; y: number; threadId: string; threadTitle: string; pinned: boolean }) => void;
}) {
  const title = getDisplayThreadTitle(thread.title, "Untitled chat");
  const displayTitle = title.length > 40 ? title.slice(0, 40) + "…" : title;
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);

  useEffect(() => {
    setDraftTitle(title);
  }, [title]);

  const commitRename = useCallback(() => {
    const trimmed = normalizeThreadTitleInput(draftTitle);
    setEditing(false);
    setDraftTitle(title);
    if (!trimmed || trimmed === title) return;
    onRename(trimmed);
  }, [draftTitle, onRename, title]);

  const openContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onContextMenu?.({
      x: e.clientX,
      y: e.clientY,
      threadId: thread.id,
      threadTitle: title,
      pinned,
    });
  }, [onContextMenu, thread.id, title, pinned]);

  return (
    <div
      onClick={onSelect}
      onContextMenu={openContextMenu}
      className="group flex items-center gap-2 px-3 py-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
    >
      {pinned && <Pin size={10} className="text-indigo-400 flex-shrink-0" />}
      {isStreamingInBg && (
        <span className="flex-shrink-0 w-2 h-2 rounded-full bg-indigo-400 dan-bg-stream-pulse" title="Working in background" />
      )}
      <div className="flex-1 min-w-0">
        {editing ? (
          <input
            autoFocus
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onBlur={commitRename}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") commitRename();
              if (e.key === "Escape") {
                setEditing(false);
                setDraftTitle(title);
              }
            }}
            className="w-full text-sm text-gray-800 bg-white border border-gray-200 rounded px-1.5 py-0.5 outline-none focus:border-indigo-300"
          />
        ) : (
          <div className="text-sm text-gray-800 truncate">{displayTitle}</div>
        )}
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[10px] text-gray-400">
            {thread.message_count} msg
            {thread.message_count !== 1 ? "s" : ""}
          </span>
          <span className="text-[10px] text-gray-300">·</span>
          <span className="text-[10px] text-gray-400">
            {relativeTimeShort(thread.updated_at)}
          </span>
          {thread.mode && (
            <>
              <span className="text-[10px] text-gray-300">·</span>
              <span className="text-[10px] text-gray-400 capitalize">
                {thread.mode}
              </span>
            </>
          )}
        </div>
      </div>
      <div className="flex items-center gap-0.5">
        <button
          onClick={(e) => {
            e.stopPropagation();
            setEditing(true);
          }}
          className="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-indigo-500 p-0.5 rounded transition-all"
          title="Rename"
        >
          <PencilLine size={12} />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onPin();
          }}
          className={`${pinned ? "opacity-100 text-indigo-400" : "opacity-0 group-hover:opacity-100 text-gray-300"} hover:text-indigo-500 p-0.5 rounded transition-all`}
          title={pinned ? "Unpin" : "Pin"}
        >
          <Pin size={12} />
        </button>
        <button
          onClick={openContextMenu}
          className="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-gray-600 p-0.5 rounded transition-all"
          title="More options"
        >
          <MoreVertical size={12} />
        </button>
      </div>
    </div>
  );
}

function EmptyState({ onSelect, mode, isEmptyGraph, fullScreen }: { onSelect: (text: string) => void; mode: ChatMode; isEmptyGraph?: boolean; fullScreen?: boolean }) {
  const cfg = MODE_CONFIG[mode];
  const Icon = cfg.icon;

  const promptsMap: Record<ChatMode, string[]> = {
    agent: isEmptyGraph ? BUILD_PROMPTS : EXAMPLE_PROMPTS,
    ask: ASK_PROMPTS,
    plan: PLAN_PROMPTS,
    debug: DEBUG_PROMPTS,
    auto: isEmptyGraph ? BUILD_PROMPTS : EXAMPLE_PROMPTS,
  };

  if (fullScreen) {
    const prompts = promptsMap[mode];
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-4 pb-20">
        <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center mb-5 shadow-lg">
          <MessageSquare size={28} className="text-white" />
        </div>
        <h2 className="text-xl font-semibold text-gray-800 mb-2">What can I help you with?</h2>
        <p className="text-sm text-gray-400 mb-8 max-w-md">
          Ask me anything — research, analysis, coding, writing. I can build multi-step workflows, search the web, read files, and more.
        </p>
        <div className="grid grid-cols-2 gap-2 max-w-lg w-full">
          {prompts.slice(0, 4).map((prompt) => (
            <button
              key={prompt}
              onClick={() => onSelect(prompt)}
              className="text-left text-sm text-gray-600 bg-gray-50 hover:bg-indigo-50 hover:text-indigo-700 border border-gray-100 hover:border-indigo-200 rounded-xl px-4 py-3 transition-all"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>
    );
  }

  const titleMap: Record<ChatMode, string> = {
    agent: isEmptyGraph ? "Build a Workflow" : "Workflow Assistant",
    ask: "Ask About Your Workflow",
    plan: "Plan Changes",
    debug: "Debug Your Workflow",
    auto: "Auto Mode",
  };
  const descMap: Record<ChatMode, string> = {
    agent: isEmptyGraph
      ? "Describe what workflow you want to build and AI will create it for you."
      : "Ask questions about your graph or describe changes you'd like to make.",
    ask: "Ask questions about your workflow's topology, data flow, and node connections.",
    plan: "Describe a change and get a step-by-step plan before any modifications are made.",
    debug: "Diagnose run failures, identify root causes, and get fix suggestions.",
    auto: "The assistant will automatically detect the best mode for your message.",
  };

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-4">
      <div className="w-10 h-10 rounded-full bg-indigo-50 flex items-center justify-center mb-3">
        <Icon size={20} className="text-indigo-400" />
      </div>
      <h3 className="text-sm font-semibold text-gray-700 mb-1">{titleMap[mode]}</h3>
      <p className="text-xs text-gray-400 mb-4 max-w-[260px]">{descMap[mode]}</p>
      <div className="flex flex-col gap-1.5 w-full">
        {promptsMap[mode].map((prompt) => (
          <button
            key={prompt}
            onClick={() => onSelect(prompt)}
            className="text-left text-xs text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded-lg px-3 py-2 transition-colors"
          >
            &ldquo;{prompt}&rdquo;
          </button>
        ))}
      </div>
    </div>
  );
}

function PlanApprovalButtons({
  onApprove,
  onRevise,
}: {
  onApprove: () => void;
  onRevise: () => void;
}) {
  return (
    <div className="flex items-center gap-2 mb-3 ml-2">
      <button
        onClick={onApprove}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg transition-colors shadow-sm"
      >
        <CheckCircle2 size={12} />
        Approve Plan
      </button>
      <button
        onClick={onRevise}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
      >
        <PencilLine size={12} />
        Revise
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Context Panel (Cmd+I) — workspace info, memory, referenced files
// ---------------------------------------------------------------------------

function ContextPanel({
  messages,
  workspaceName,
  onClose,
}: {
  messages: ChatMessage[];
  workspaceName: string;
  onClose: () => void;
}) {
  const referencedFiles = useMemo(() => {
    const paths = new Set<string>();
    for (const msg of messages) {
      if (msg.mentions) {
        for (const m of msg.mentions) {
          if (m.type === "file" || m.type === "code") paths.add(m.name);
        }
      }
      if (msg.attachments) {
        for (const a of msg.attachments) paths.add(a.filename);
      }
      if (msg.toolCalls) {
        for (const tc of msg.toolCalls) {
          const fileMatch = tc.argsPreview?.match(/(?:path|file)['":\s]+([^\s'",}]+)/i);
          if (fileMatch) paths.add(fileMatch[1]);
        }
      }
    }
    return Array.from(paths).slice(0, 50);
  }, [messages]);

  const memoryItems = useMemo(() => {
    return messages
      .filter((m) => m.role === "assistant" && m.content.length > 0)
      .slice(-5)
      .map((m) => ({
        id: m.id,
        preview: m.content.slice(0, 120) + (m.content.length > 120 ? "…" : ""),
        timestamp: m.timestamp,
      }));
  }, [messages]);

  return (
    <div className="w-80 flex-shrink-0 border-l border-gray-200 bg-white flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 flex-shrink-0">
        <h3 className="text-sm font-semibold text-gray-800">Context</h3>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors"
          title="Close (⌘I)"
        >
          <X size={14} />
        </button>
      </div>

      <div className="px-4 py-3 border-b border-gray-100">
        <h4 className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2 flex items-center gap-1">
          <Folder size={10} />
          Workspace
        </h4>
        <p className="text-sm text-gray-700 font-medium">{workspaceName}</p>
      </div>

      <div className="px-4 py-3 border-b border-gray-100">
        <h4 className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2 flex items-center gap-1">
          <Clock size={10} />
          Recent Memory
        </h4>
        {memoryItems.length === 0 ? (
          <p className="text-xs text-gray-400 italic">
            Memory items from the active conversation will appear here
          </p>
        ) : (
          <div className="space-y-2">
            {memoryItems.map((item) => (
              <div key={item.id} className="text-xs text-gray-500 leading-relaxed">
                {item.preview}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="px-4 py-3 flex-1 overflow-y-auto">
        <h4 className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2 flex items-center gap-1">
          <FileText size={10} />
          Referenced Files
        </h4>
        {referencedFiles.length === 0 ? (
          <p className="text-xs text-gray-400 italic">
            Files mentioned in conversation will appear here
          </p>
        ) : (
          <div className="space-y-0.5">
            {referencedFiles.map((path) => (
              <p
                key={path}
                className="text-[11px] text-gray-600 truncate font-mono py-0.5 px-1.5 rounded hover:bg-gray-50 cursor-default"
                title={path}
              >
                {path}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Thread Context Menu (right-click / "..." button)
// ---------------------------------------------------------------------------

function ThreadContextMenu({
  x,
  y,
  threadId,
  threadTitle,
  pinned,
  onPin,
  onRename,
  onExport,
  onDelete,
  onClose,
}: {
  x: number;
  y: number;
  threadId: string;
  threadTitle: string;
  pinned: boolean;
  onPin: (id: string, pinned: boolean) => void;
  onRename: (id: string) => void;
  onExport: (id: string, format: "md" | "json") => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const escHandler = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("keydown", escHandler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("keydown", escHandler);
    };
  }, [onClose]);

  // Clamp to viewport
  const clampedY = Math.min(y, window.innerHeight - 250);
  const clampedX = Math.min(x, window.innerWidth - 200);

  const menuItem = "flex items-center gap-2 px-3 py-2 text-xs text-gray-700 hover:bg-gray-50 cursor-pointer transition-colors w-full text-left";

  return (
    <div
      ref={menuRef}
      className="fixed z-50 bg-white rounded-lg shadow-lg border border-gray-200 py-1 min-w-[180px]"
      style={{ top: clampedY, left: clampedX }}
    >
      <div className="px-3 py-1.5 text-[10px] text-gray-400 truncate max-w-[200px]">
        {threadTitle}
      </div>
      <div className="border-t border-gray-100 mb-1" />
      <button className={menuItem} onClick={() => onRename(threadId)}>
        <PencilLine size={12} className="text-gray-400" />
        Rename
      </button>
      <button className={menuItem} onClick={() => onPin(threadId, !pinned)}>
        <Pin size={12} className={pinned ? "text-indigo-400" : "text-gray-400"} />
        {pinned ? "Unpin" : "Pin to top"}
      </button>
      <div className="my-1 border-t border-gray-100" />
      <button className={menuItem} onClick={() => onExport(threadId, "md")}>
        <Download size={12} className="text-gray-400" />
        Export as Markdown
      </button>
      <button className={menuItem} onClick={() => onExport(threadId, "json")}>
        <Download size={12} className="text-gray-400" />
        Export as JSON
      </button>
      <div className="my-1 border-t border-gray-100" />
      <button
        className="flex items-center gap-2 px-3 py-2 text-xs text-red-600 hover:bg-red-50 cursor-pointer transition-colors w-full text-left"
        onClick={() => onDelete(threadId)}
      >
        <Trash2 size={12} />
        Delete conversation
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Date grouping helper
// ---------------------------------------------------------------------------

function groupThreadsByDate(threads: ChatThreadSummary[]): Array<{ label: string; threads: ChatThreadSummary[] }> {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86_400_000);
  const weekAgo = new Date(today.getTime() - 7 * 86_400_000);

  const groups: Record<string, ChatThreadSummary[]> = {};
  const order: string[] = [];

  for (const t of threads) {
    const d = new Date(t.updated_at);
    let label: string;
    if (d >= today) label = "Today";
    else if (d >= yesterday) label = "Yesterday";
    else if (d >= weekAgo) label = "This week";
    else label = d.toLocaleDateString("en-US", { month: "short", year: "numeric" });

    if (!groups[label]) {
      groups[label] = [];
      order.push(label);
    }
    groups[label].push(t);
  }

  return order.map((label) => ({ label, threads: groups[label] }));
}

