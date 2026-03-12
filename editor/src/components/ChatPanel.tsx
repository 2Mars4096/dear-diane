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
} from "lucide-react";
import { useGraphStore } from "../store/useGraphStore";
import type { ChatMessage, ChatStreamEvent, ToolCallInfo } from "../types/chat";
import type { ChatThreadSummary } from "../lib/api";
import * as api from "../lib/api";
import type { ApplyMutationResult } from "../lib/api";
import ChatMessageBubble from "./ChatMessage";
import EscalationBanner, { detectEscalation } from "./EscalationBanner";
import GraphDiffPreview from "./GraphDiffPreview";
import MentionAutocomplete from "./MentionAutocomplete";
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
  getDisplayThreadTitle,
  normalizeThreadTitleInput,
} from "../lib/chatThreadTitle";

function normalizeForCanonicalJson(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => normalizeForCanonicalJson(item));
  }
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const normalized: Record<string, unknown> = {};
    for (const key of Object.keys(obj).sort()) {
      const v = obj[key];
      if (v === undefined) continue;
      normalized[key] = normalizeForCanonicalJson(v);
    }
    return normalized;
  }
  return value;
}

async function computeClientGraphRevision(
  graph: unknown,
): Promise<string | undefined> {
  try {
    if (!globalThis.crypto?.subtle) return undefined;
    const canonical = JSON.stringify(normalizeForCanonicalJson(graph));
    const data = new TextEncoder().encode(canonical);
    const digest = await globalThis.crypto.subtle.digest("SHA-256", data);
    const hex = Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    return hex.slice(0, 16);
  } catch {
    return undefined;
  }
}

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

// ---------------------------------------------------------------------------
// Backend ↔ frontend message conversion
// ---------------------------------------------------------------------------

function safeTokenUsage(tu: unknown): { prompt: number; completion: number } | null {
  if (!tu || typeof tu !== "object") return null;
  const raw = tu as Record<string, unknown>;
  const p = typeof raw.prompt === "number" ? raw.prompt : 0;
  const c = typeof raw.completion === "number" ? raw.completion : 0;
  return p + c > 0 ? { prompt: p, completion: c } : null;
}

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
    dry_run_result: m.dryRunResult ?? null,
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
    tool_calls: m.toolCalls?.map((tc) => ({
      id: tc.id,
      tool_name: tc.toolName,
      args_preview: tc.argsPreview,
      status: tc.status,
      output_preview: tc.outputPreview ?? null,
      duration_ms: tc.durationMs ?? null,
    })) ?? [],
    run_events: m.runEvents ?? [],
  };
}

function fromBackendMessage(m: Record<string, unknown>): ChatMessage {
  const tu = m.token_usage as Record<string, number> | null;
  const rr = m.run_ref as Record<string, string> | null;
  const rawTc = m.tool_calls as Array<Record<string, unknown>> | undefined;
  return {
    id: m.id as string,
    role: m.role as ChatMessage["role"],
    content: m.content as string,
    timestamp: new Date(m.timestamp as string).getTime(),
    tokenUsage: tu ? { prompt: tu.prompt, completion: tu.completion } : null,
    mutationPlan: m.mutation_plan ?? null,
    dryRunResult: (m.dry_run_result as Record<string, unknown>) ?? null,
    mutationId: (m.mutation_id as string) ?? null,
    mutationStatus:
      (m.mutation_status as ChatMessage["mutationStatus"]) ?? null,
    runRef: rr
      ? { runId: rr.run_id, scope: rr.scope, status: rr.status }
      : null,
    mentions: (m.mentions as ChatMessage["mentions"]) ?? [],
    toolCalls: rawTc?.map((tc) => ({
      id: tc.id as string,
      toolName: (tc.tool_name as string) ?? "",
      argsPreview: (tc.args_preview as string) ?? "",
      status: (tc.status as ToolCallInfo["status"]) ?? "success",
      outputPreview: tc.output_preview as string | undefined,
      durationMs: tc.duration_ms as number | undefined,
    })),
    runEvents: (m.run_events as ChatMessage["runEvents"]) ?? undefined,
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

interface ChatPanelProps {
  fullScreen?: boolean;
}

export default function ChatPanel({ fullScreen = false }: ChatPanelProps) {
  const rawGraphId = useGraphStore((s) => s.graphId);
  const graphId = rawGraphId || (fullScreen ? "_scratch" : null);
  const danGraph = useGraphStore((s) => s.danGraph);
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
  const [showThreadList, setShowThreadList] = useState(!fullScreen);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
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
  const [mutationConfirmMode, setMutationConfirmMode] = useState<boolean>(() =>
    readMutationConfirmPreference(),
  );
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

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const applyingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const runStreamHandoffRef = useRef(false);
  const dragging = useRef(false);
  const activeThreadIdRef = useRef<string | null>(null);
  const prevGraphIdRef = useRef<string | null>(null);
  const activeChannelIdRef = useRef<string | null>(null);
  activeChannelIdRef.current = activeChannelId;

  activeThreadIdRef.current = activeThreadId;

  // -------------------------------------------------------------------------
  // Thread persistence helpers
  // -------------------------------------------------------------------------

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
        const storedMode = (data.mode as ChatMode) || "agent";
        useGraphStore.getState().setChatMode(storedMode);
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

  // Clean up WebSocket on unmount
  useEffect(() => {
    return () => wsRef.current?.close();
  }, []);

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
      streamChannelId: string,
      assistantId: string,
      capturedGraphId: string | null,
      initialRunRef?: { runId: string; scope: string; status: string } | null,
    ) => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const runWs = new WebSocket(
        `${proto}//${location.host}/api/chat/${streamChannelId}/events`,
      );
      wsRef.current = runWs;
      setActiveChannelId(null); // run streams are observational, not stoppable via chat stop route
      setIsStreaming(false);
      setIsRunStreaming(true);

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
            setMessages((prev) =>
              prev.map((m) =>
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
              ),
            );
            if (
              re.event_type === "run_completed" ||
              re.event_type === "run_failed" ||
              re.event_type === "run_cancelled"
            ) {
              setIsRunStreaming(false);
              setMessages((prev) => {
                const tid = activeThreadIdRef.current;
                if (tid && capturedGraphId) {
                  api
                    .updateChatThread(capturedGraphId, tid, {
                      messages: prev.map(toBackendMessage),
                    })
                    .catch((err: unknown) =>
                      console.warn("Failed to save thread:", err),
                    );
                }
                return prev;
              });
              runWs.close();
            }
          }
        } catch {
          /* ignore parse errors */
        }
      };

      runWs.onerror = () => setIsRunStreaming(false);
      runWs.onclose = () => setIsRunStreaming(false);
    },
    [],
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

      if (fullScreen) {
        const esc = detectEscalation(content);
        if (esc) setEscalation(esc);
      }
      setStaleRevision(false);
      setBuildJustCompleted(false);
      setDetectedMode(null);

      const capturedGraphId = graphId;
      const currentDanGraph = useGraphStore.getState().danGraph;
      const clientGraphRevision = currentDanGraph
        ? await computeClientGraphRevision(currentDanGraph)
        : undefined;

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
          return;
        }

        if (resBody.type === "run_started") {
          const initialRunRef = {
            runId: resBody.run_id as string,
            scope: (resBody.scope as string) ?? "full",
            status: "running",
          };
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    content: `Started ${resBody.scope ?? "full"} run.`,
                    runRef: initialRunRef,
                  }
                : m,
            ),
          );

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
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const seenStreamChannels = new Set<string>();

        const connectToChatStream = (channelId: string) => {
          if (seenStreamChannels.has(channelId)) {
            setError("Chat stream redirect loop detected");
            setIsStreaming(false);
            setActiveChannelId(null);
            return;
          }
          seenStreamChannels.add(channelId);
          setActiveChannelId(channelId);

          const ws = new WebSocket(
            `${proto}//${location.host}/api/chat/${channelId}/events`,
          );
          wsRef.current = ws;
          let wsClosedIntentionally = false;

          ws.onmessage = (e) => {
            try {
              const evt: ChatStreamEvent = JSON.parse(e.data);
              if (evt.type === "chat_queued") {
                const nextChannel = (evt.stream_channel_id ?? "").trim();
                const queuePosition =
                  typeof evt.queue_position === "number" ? evt.queue_position : 0;

                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId
                      ? {
                          ...m,
                          content:
                            queuePosition > 1
                              ? `Queued behind ${queuePosition} earlier messages...`
                              : "Queued behind an earlier message...",
                        }
                      : m,
                  ),
                );

                if (nextChannel && nextChannel !== channelId) {
                  runStreamHandoffRef.current = true;
                  wsClosedIntentionally = true;
                  ws.close();
                  connectToChatStream(nextChannel);
                }
                return;
              }
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
              const isProgressAck = evt.detected_mode === "progress_ack";
              if (evt.context_window) setContextWindow(evt.context_window);
              if (evt.detected_mode && !isProgressAck) setDetectedMode(evt.detected_mode);
              setMessages((prev) => {
                const updated = prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: evt.content ?? m.content,
                        tokenUsage: safeTokenUsage(evt.token_usage) ?? m.tokenUsage ?? null,
                      }
                    : m,
                );
                if (!isProgressAck) {
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
                }
                return updated;
              });
              if (isProgressAck) {
                // Progress/reassurance update — keep streaming
              } else {
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
              setActiveChannelId(null);
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
              setActiveChannelId(null);
              wsClosedIntentionally = true;
              ws.close();
              if (capturedGraphId) void fetchThreads(capturedGraphId);
            } else if (evt.type === "chat_tool_call_start") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        toolCalls: [
                          ...(m.toolCalls || []),
                          {
                            id: evt.tool_call_id!,
                            toolName: evt.tool_name!,
                            argsPreview: evt.args_preview ?? "",
                            status: "running" as const,
                          },
                        ],
                      }
                    : m,
                ),
              );
            } else if (evt.type === "chat_tool_call_result") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        toolCalls: (m.toolCalls || []).map((tc: ToolCallInfo) =>
                          tc.id === evt.tool_call_id
                            ? {
                                ...tc,
                                status: (evt.status as "success" | "error") ?? "success",
                                outputPreview: evt.output_preview,
                                durationMs: evt.duration_ms,
                              }
                            : tc,
                        ),
                      }
                    : m,
                ),
              );
            } else if (evt.type === "chat_error") {
              if (evt.error?.includes("revision_mismatch")) {
                setStaleRevision(true);
              }
              setError(evt.error ?? "Unknown error");
              setIsStreaming(false);
              setActiveChannelId(null);
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
            wsClosedIntentionally = true;
            setError("WebSocket connection failed");
            setIsStreaming(false);
            setActiveChannelId(null);
          };

          ws.onclose = (event) => {
            if (runStreamHandoffRef.current) {
              runStreamHandoffRef.current = false;
              return;
            }
            if (
              !wsClosedIntentionally &&
              event.code !== 1000 &&
              event.code !== 1005
            ) {
              setError(
                "Connection lost — your response may be incomplete. Click Retry to resend.",
              );
              useGraphStore
                .getState()
                .addToast({ type: "error", message: "Chat stream disconnected" });
            }
            setIsStreaming(false);
            setActiveChannelId(null);
          };
        };

        connectToChatStream(stream_channel_id);
      } catch (err) {
        const msg =
          err instanceof Error ? err.message : "Failed to send message";
        setError(msg);
        setIsStreaming(false);
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
      }
    },
    [inputText, isStreaming, graphId],
  );

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
    setActiveThreadId(null);
    setMessages([]);
    setThreadTitle("");
    setShowThreadList(false);
    setError(null);
    setSessionMarkers({});
  }, []);

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
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [fullScreen, handleNewChat]);

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
                  isStreaming={isStreaming && i === messages.length - 1 && m.role === "assistant"}
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

            {isStreaming && messages[messages.length - 1]?.content === "" && <StreamingDots />}

            {fullScreen && escalation && (
              <EscalationBanner suggestion={escalation} onDismiss={() => setEscalation(null)} />
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

  const inputArea = (
    <div className={`flex-shrink-0 border-t border-gray-200 ${fullScreen ? "px-4 py-4 bg-white" : "p-3"}`}>
      <div className={fullScreen ? "max-w-3xl mx-auto" : ""}>
        <div className={`flex items-end gap-2 border border-gray-200 rounded-xl px-3 py-2.5 focus-within:shadow-md focus-within:border-indigo-300 transition-all ${fullScreen ? "shadow-sm" : "focus-within:shadow-sm"}`}>
          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => { setInputText(e.target.value); requestAnimationFrame(checkMention); }}
            onKeyDown={handleKeyDown}
            onKeyUp={checkMention}
            onClick={checkMention}
            placeholder={
              fullScreen
                ? "Message DAN… (@ to mention, / for commands)"
                : chatMode === "ask" ? "Ask about your workflow…"
                : chatMode === "plan" ? "Describe what changes to plan…"
                : chatMode === "debug" ? "Describe the issue or ask to diagnose…"
                : chatMode === "auto" ? "Type anything — mode auto-detected… (@ to mention)"
                : "Ask about your workflow… (@ to mention)"
            }
            rows={1}
            disabled={isStreaming}
            className={`flex-1 resize-none text-gray-900 placeholder-gray-400 bg-transparent outline-none max-h-[160px] leading-snug disabled:opacity-50 ${fullScreen ? "text-[15px] min-h-[28px]" : "text-sm min-h-[24px]"}`}
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
            isStreaming && activeChannelId ? (
              <button onClick={handleStop} className="text-red-500 hover:text-red-700 transition-colors p-0.5 flex-shrink-0" title="Stop generation">
                <Square size={fullScreen ? 18 : 16} />
              </button>
            ) : (
              <span className="text-gray-300 p-0.5 flex-shrink-0" title="Waiting for run updates">
                <Loader2 size={fullScreen ? 18 : 16} className="animate-spin" />
              </span>
            )
          ) : (
            <button
              onClick={() => sendMessage()}
              disabled={!inputText.trim()}
              className="text-indigo-500 hover:text-indigo-700 disabled:text-gray-300 transition-colors p-0.5 flex-shrink-0"
            >
              <Send size={fullScreen ? 18 : 16} />
            </button>
          )}
        </div>
        <div className="text-[10px] text-gray-400 mt-1.5 px-1">
          Enter to send · Shift+Enter for newline{fullScreen ? " · Cmd+K command palette" : ""}
        </div>
      </div>
    </div>
  );

  // -------------------------------------------------------------------------
  // Full-screen layout: thread sidebar + conversation side-by-side
  // -------------------------------------------------------------------------
  if (fullScreen) {
    return (
      <div className="flex h-full w-full bg-white">
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
              onClose={() => setShowThreadList(false)}
              searchQuery={searchQuery}
              searchResults={searchResults}
              isSearching={isSearching}
              onSearch={handleSearch}
            />
          </div>
        )}
        <div className="flex flex-col flex-1 min-w-0">
          {conversationHeader}
          {modeSelector}
          {messagesArea}
          {inputArea}
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Sidebar layout: toggle between thread list and conversation
  // -------------------------------------------------------------------------
  return (
    <div className="flex flex-shrink-0 h-full border-l border-gray-200 bg-white" style={{ width: panelWidth }}>
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
            onRenameThread={handleRenameThread}
            onPinThread={handlePinThread}
            onClose={() => setChatOpen(false)}
            searchQuery={searchQuery}
            searchResults={searchResults}
            isSearching={isSearching}
            onSearch={handleSearch}
          />
        ) : (
          <>
            {conversationHeader}
            {modeSelector}
            {messagesArea}
            {inputArea}
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
  onRenameThread,
  onPinThread,
  onClose,
  searchQuery,
  searchResults,
  isSearching,
  onSearch,
}: {
  threads: ChatThreadSummary[];
  loading: boolean;
  onNewChat: () => void;
  onSelectThread: (id: string) => void;
  onDeleteThread: (id: string) => void;
  onRenameThread: (id: string, title: string) => void;
  onPinThread: (id: string, pinned: boolean) => void;
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
}) {
  const sortedThreads = [...threads].sort((a, b) => {
    const aPinned = (a as ChatThreadSummary & { pinned?: boolean }).pinned ? 1 : 0;
    const bPinned = (b as ChatThreadSummary & { pinned?: boolean }).pinned ? 1 : 0;
    if (aPinned !== bPinned) return bPinned - aPinned;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });

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
            {sortedThreads.map((t) => (
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
                pinned={
                  (t as ChatThreadSummary & { pinned?: boolean }).pinned ??
                  false
                }
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
  onRename,
  onPin,
  pinned,
}: {
  thread: ChatThreadSummary;
  onSelect: () => void;
  onDelete: () => void;
  onRename: (title: string) => void;
  onPin: () => void;
  pinned: boolean;
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

  return (
    <div
      onClick={onSelect}
      className="group flex items-center gap-2 px-3 py-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
    >
      {pinned && <Pin size={10} className="text-indigo-400 flex-shrink-0" />}
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

function StreamingDots() {
  return (
    <div className="flex justify-start mb-3">
      <div className="bg-gray-50 rounded-2xl rounded-bl-md px-3.5 py-3 shadow-xs">
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-bounce [animation-delay:0ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-bounce [animation-delay:150ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-bounce [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  );
}
