import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import {
  ArrowUp,
  Bot,
  CheckCircle2,
  ChevronLeft,
  History,
  Menu,
  MessageSquarePlus,
  PanelLeftClose,
  Search,
  Square,
  Trash2,
  User,
  Wrench,
  X,
} from "lucide-react";
import {
  connectChatV2Stream,
  createChatV2Thread,
  deleteChatV2Thread,
  getChatV2Thread,
  listChatV2Threads,
  normalizeChatV2History,
  postChatV2Message,
  saveChatV2Thread,
  stopChatV2Stream,
  type ChatV2Mode,
  type ChatV2ThreadSummary,
} from "../../lib/chatV2Api";
import { upsertToolCallResult, upsertToolCallStart } from "../../lib/toolCallState";
import type { ChatMessage, ChatStreamEvent, RunEventPayload } from "../../types/chat";

const DEFAULT_WORKFLOW_ID = "_scratch";

const MODE_OPTIONS: Array<{ id: ChatV2Mode; label: string }> = [
  { id: "auto", label: "Auto" },
  { id: "agent", label: "Agent" },
  { id: "ask", label: "Ask" },
  { id: "plan", label: "Plan" },
  { id: "debug", label: "Debug" },
];

type ActiveThread = {
  id: string;
  workflowId: string;
  title?: string;
};

type StreamState = "idle" | "starting" | "queued" | "streaming" | "error";
type ServerState = "checking" | "online" | "offline";

function makeMessage(role: ChatMessage["role"], content: string): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    timestamp: Date.now(),
  };
}

function titleFromPrompt(prompt: string): string {
  const compact = prompt.replace(/\s+/g, " ").trim();
  if (!compact) return "New chat";
  return compact.length > 54 ? `${compact.slice(0, 51)}...` : compact;
}

function formatTime(value: string): string {
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(time);
}

function coerceMode(value: unknown): ChatV2Mode {
  if (
    value === "auto" ||
    value === "ask" ||
    value === "agent" ||
    value === "plan" ||
    value === "debug" ||
    value === "conversation"
  ) {
    return value;
  }
  return "auto";
}

function eventSummary(event: RunEventPayload): string {
  return event.summary || event.event_type || event.type || "Run event";
}

function StatusDot({ state }: { state: ServerState }) {
  const color =
    state === "online"
      ? "bg-emerald-600"
      : state === "offline"
        ? "bg-red-500"
        : "bg-amber-500";
  return <span className={`h-2 w-2 rounded-full ${color}`} />;
}

function EmptyState() {
  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col items-center justify-center px-6 text-center">
      <div className="mb-6 grid h-12 w-12 place-items-center rounded-2xl bg-emerald-700 text-white shadow-sm">
        <Bot size={24} />
      </div>
      <h1 className="text-3xl font-semibold tracking-normal text-[#151713] dark:text-[#f6f7f4]">
        What should DAN handle?
      </h1>
      <div className="mt-8 grid w-full max-w-2xl gap-2 sm:grid-cols-2">
        {[
          "Audit the latest run failure",
          "Plan a workflow change",
          "Summarize this workspace",
          "Debug a stuck chat stream",
        ].map((prompt) => (
          <button
            key={prompt}
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent("dan-v2:seed", { detail: prompt }))}
            className="rounded-lg border border-[#d8ded6] bg-white px-4 py-3 text-left text-sm text-[#30362f] shadow-sm transition hover:border-emerald-600 hover:bg-[#f8fbf8] dark:border-[#2c332d] dark:bg-[#181d19] dark:text-[#e7ece5] dark:hover:border-emerald-400"
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

function ToolStrip({ message }: { message: ChatMessage }) {
  const toolCalls = message.toolCalls ?? [];
  const runEvents = message.runEvents ?? [];
  if (toolCalls.length === 0 && runEvents.length === 0) return null;

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {toolCalls.slice(-4).map((tool) => (
        <span
          key={tool.id}
          className="inline-flex min-h-7 items-center gap-1.5 rounded-full border border-[#d8ded6] bg-white px-2.5 text-[11px] font-medium text-[#4a5148] dark:border-[#30382f] dark:bg-[#151a16] dark:text-[#cfd7cd]"
          title={tool.outputPreview || tool.argsPreview}
        >
          <Wrench size={12} />
          <span className="max-w-36 truncate">{tool.toolName || "tool"}</span>
          {tool.status === "running" ? (
            <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
          ) : tool.status === "error" ? (
            <X size={12} className="text-red-500" />
          ) : (
            <CheckCircle2 size={12} className="text-emerald-600" />
          )}
        </span>
      ))}
      {runEvents.slice(-3).map((event, index) => (
        <span
          key={`${event.event_type}-${index}`}
          className="inline-flex min-h-7 items-center rounded-full border border-[#d8ded6] bg-white px-2.5 text-[11px] font-medium text-[#4a5148] dark:border-[#30382f] dark:bg-[#151a16] dark:text-[#cfd7cd]"
          title={eventSummary(event)}
        >
          <span className="max-w-52 truncate">{eventSummary(event)}</span>
        </span>
      ))}
    </div>
  );
}

function MessageRow({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const Icon = isUser ? User : Bot;

  return (
    <article className={`flex gap-4 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#151713] text-white dark:bg-[#f2f5ef] dark:text-[#111411]">
          <Icon size={16} />
        </div>
      )}
      <div
        className={`max-w-[min(760px,calc(100vw-96px))] rounded-2xl px-4 py-3 text-[15px] leading-7 shadow-sm ${
          isUser
            ? "bg-emerald-700 text-white"
            : "border border-[#dfe5dd] bg-white text-[#151713] dark:border-[#293129] dark:bg-[#121713] dark:text-[#f0f4ed]"
        }`}
      >
        <div className="whitespace-pre-wrap break-words">
          {message.content || (!isUser ? "Working..." : "")}
        </div>
        {!isUser && <ToolStrip message={message} />}
      </div>
      {isUser && (
        <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-emerald-700 text-white">
          <Icon size={16} />
        </div>
      )}
    </article>
  );
}

export default function ChatV2App() {
  const [threads, setThreads] = useState<ChatV2ThreadSummary[]>([]);
  const [activeThread, setActiveThread] = useState<ActiveThread | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ChatV2Mode>("auto");
  const [threadFilter, setThreadFilter] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [streamState, setStreamState] = useState<StreamState>("idle");
  const [serverState, setServerState] = useState<ServerState>("checking");
  const [statusText, setStatusText] = useState("Ready");
  const [streamChannelId, setStreamChannelId] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const messagesRef = useRef<ChatMessage[]>([]);
  const modeRef = useRef<ChatV2Mode>("auto");
  const activeStreamRef = useRef<{
    channelId: string;
    thread: ActiveThread;
    assistantId: string;
    responseMessageId?: string;
  } | null>(null);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  const refreshThreads = useCallback(async () => {
    setLoadingThreads(true);
    try {
      setThreads(await listChatV2Threads());
    } catch {
      setThreads([]);
    } finally {
      setLoadingThreads(false);
    }
  }, []);

  useEffect(() => {
    void refreshThreads();
  }, [refreshThreads]);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const response = await fetch("/api/health", {
          signal: AbortSignal.timeout(3000),
        });
        if (!cancelled) setServerState(response.ok ? "online" : "offline");
      } catch {
        if (!cancelled) setServerState("offline");
      }
    };
    void poll();
    const timer = setInterval(poll, 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const onSeed = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail;
      if (typeof detail === "string") {
        setInput(detail);
      }
    };
    window.addEventListener("dan-v2:seed", onSeed);
    return () => window.removeEventListener("dan-v2:seed", onSeed);
  }, []);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, []);

  const visibleThreads = useMemo(() => {
    const query = threadFilter.trim().toLowerCase();
    if (!query) return threads;
    return threads.filter((thread) => {
      return `${thread.title} ${thread.workflow_id}`.toLowerCase().includes(query);
    });
  }, [threadFilter, threads]);

  const applyMessageUpdate = useCallback(
    (updater: (previous: ChatMessage[]) => ChatMessage[]) => {
      let nextMessages: ChatMessage[] = [];
      setMessages((previous) => {
        nextMessages = updater(previous);
        messagesRef.current = nextMessages;
        return nextMessages;
      });
      return nextMessages;
    },
    [],
  );

  const persistMessages = useCallback(
    async (thread: ActiveThread, nextMessages: ChatMessage[]) => {
      try {
        await saveChatV2Thread(thread.workflowId, thread.id, {
          messages: nextMessages,
          mode: modeRef.current,
        });
        await refreshThreads();
      } catch {
        setStatusText("Thread save failed");
      }
    },
    [refreshThreads],
  );

  const startNewChat = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    activeStreamRef.current = null;
    setActiveThread(null);
    setMessages([]);
    setInput("");
    setStreamState("idle");
    setStreamChannelId(null);
    setStatusText("Ready");
  }, []);

  const openThread = useCallback(
    async (summary: ChatV2ThreadSummary) => {
      if (streamState === "streaming" || streamState === "queued" || streamState === "starting") {
        return;
      }
      setStatusText("Loading thread");
      try {
        const thread = await getChatV2Thread(summary.workflow_id, summary.id);
        setActiveThread({
          id: thread.id,
          workflowId: thread.workflow_id,
          title: thread.title,
        });
        setMessages(thread.messages);
        setMode(coerceMode(thread.mode ?? summary.mode));
        setStatusText("Ready");
      } catch {
        setStatusText("Thread load failed");
      }
    },
    [streamState],
  );

  const removeThread = useCallback(
    async (thread: ChatV2ThreadSummary) => {
      if (!window.confirm(`Delete "${thread.title || "Untitled"}"?`)) return;
      try {
        await deleteChatV2Thread(thread.workflow_id, thread.id);
        if (activeThread?.id === thread.id) {
          startNewChat();
        }
        await refreshThreads();
      } catch {
        setStatusText("Delete failed");
      }
    },
    [activeThread?.id, refreshThreads, startNewChat],
  );

  const ensureThread = useCallback(
    async (prompt: string): Promise<ActiveThread> => {
      if (activeThread) return activeThread;
      const created = await createChatV2Thread(DEFAULT_WORKFLOW_ID, {
        title: titleFromPrompt(prompt),
        mode,
      });
      const nextThread = {
        id: created.id,
        workflowId: created.workflow_id || DEFAULT_WORKFLOW_ID,
        title: created.title,
      };
      setActiveThread(nextThread);
      await refreshThreads();
      return nextThread;
    },
    [activeThread, mode, refreshThreads],
  );

  const finishStream = useCallback(
    (thread: ActiveThread, assistantId: string, patch: Partial<ChatMessage>) => {
      const nextMessages = applyMessageUpdate((previous) =>
        previous.map((message) =>
          message.id === assistantId ? { ...message, ...patch } : message,
        ),
      );
      setStreamState("idle");
      setStreamChannelId(null);
      activeStreamRef.current = null;
      wsRef.current?.close();
      wsRef.current = null;
      void persistMessages(thread, nextMessages);
      setStatusText("Ready");
    },
    [applyMessageUpdate, persistMessages],
  );

  const connectToStream = useCallback(
    (channelId: string, thread: ActiveThread, assistantId: string, responseMessageId?: string) => {
      setStreamChannelId(channelId);
      activeStreamRef.current = { channelId, thread, assistantId, responseMessageId };
      setStreamState("streaming");
      setStatusText("Receiving");

      const handleEvent = (event: ChatStreamEvent) => {
        if (event.type === "ping") return;

        if (event.type === "chat_queued") {
          const nextChannel = (event.stream_channel_id || "").trim();
          setStreamState("queued");
          setStatusText(
            typeof event.queue_position === "number"
              ? `Queued ${event.queue_position}`
              : "Queued",
          );
          if (nextChannel && nextChannel !== channelId) {
            const previous = wsRef.current;
            wsRef.current = null;
            previous?.close();
            connectToStream(nextChannel, thread, assistantId, responseMessageId);
          }
          return;
        }

        if (event.type === "chat_token") {
          applyMessageUpdate((previous) =>
            previous.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content:
                      typeof event.accumulated === "string"
                        ? event.accumulated
                        : `${message.content}${event.delta ?? ""}`,
                  }
                : message,
            ),
          );
          return;
        }

        if (
          event.type === "chat_complete" &&
          event.detected_mode === "progress_ack"
        ) {
          setStatusText(event.phase_label || event.content || "Working");
          return;
        }

        if (event.type === "chat_notice") {
          setStatusText(event.content || "Notice");
          return;
        }

        if (event.type === "chat_tool_call_start") {
          applyMessageUpdate((previous) =>
            previous.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    toolCalls: upsertToolCallStart(message.toolCalls, {
                      id: event.tool_call_id || crypto.randomUUID(),
                      toolName: event.tool_name || "",
                      argsPreview: event.args_preview || "",
                    }),
                  }
                : message,
            ),
          );
          setStatusText(event.tool_name ? `Using ${event.tool_name}` : "Using tool");
          return;
        }

        if (event.type === "chat_tool_call_result") {
          applyMessageUpdate((previous) =>
            previous.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    toolCalls: upsertToolCallResult(message.toolCalls, {
                      id: event.tool_call_id || crypto.randomUUID(),
                      toolName: event.tool_name || "",
                      argsPreview: event.args_preview,
                      status: event.status,
                      outputPreview: event.output_preview,
                      durationMs: event.duration_ms,
                    }),
                  }
                : message,
            ),
          );
          return;
        }

        if (event.type === "chat_run_event" && event.run_event) {
          applyMessageUpdate((previous) =>
            previous.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    runEvents: [...(message.runEvents ?? []), event.run_event!],
                  }
                : message,
            ),
          );
          setStatusText(eventSummary(event.run_event));
          return;
        }

        if (event.type === "chat_file_attachment") {
          applyMessageUpdate((previous) =>
            previous.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    attachments: [
                      ...(message.attachments ?? []),
                      {
                        path: event.path,
                        filename: event.filename || event.path || "Attachment",
                        size: event.size,
                      },
                    ],
                  }
                : message,
            ),
          );
          return;
        }

        if (event.type === "chat_complete") {
          finishStream(thread, assistantId, {
            content: event.content ?? messagesRef.current.find((m) => m.id === assistantId)?.content ?? "",
            tokenUsage: event.token_usage
              ? {
                  prompt: event.token_usage.prompt ?? 0,
                  completion: event.token_usage.completion ?? 0,
                }
              : null,
            estimatedCost: event.estimated_cost ?? null,
          });
          return;
        }

        if (event.type === "chat_mutation") {
          finishStream(thread, assistantId, {
            content: event.content || "Mutation proposed.",
            mutationPlan: event.mutation_plan ?? null,
            dryRunResult: event.dry_run_result ?? null,
            mutationStatus: event.applied ? "applied" : "proposed",
          });
          return;
        }

        if (event.type === "chat_interrupted") {
          finishStream(thread, assistantId, {
            content: event.content || messagesRef.current.find((m) => m.id === assistantId)?.content || "Stopped.",
          });
          return;
        }

        if (event.type === "chat_error") {
          setStreamState("error");
          finishStream(thread, assistantId, {
            content: event.error || "Request failed.",
          });
        }
      };

      const ws = connectChatV2Stream(
        channelId,
        handleEvent,
        () => {
          if (wsRef.current !== ws) return;
          wsRef.current = null;
          const current = activeStreamRef.current;
          if (current?.assistantId === assistantId) {
            const assistant = messagesRef.current.find((m) => m.id === assistantId);
            if (!assistant?.content && streamState !== "idle") {
              finishStream(thread, assistantId, { content: "Stream closed." });
            }
          }
        },
        () => {
          if (wsRef.current === ws) {
            setStatusText("Stream error");
          }
        },
      );
      wsRef.current = ws;
    },
    [applyMessageUpdate, finishStream, streamState],
  );

  const sendMessage = useCallback(
    async (event?: FormEvent) => {
      event?.preventDefault();
      const prompt = input.trim();
      if (!prompt) return;
      if (streamState === "starting" || streamState === "streaming" || streamState === "queued") {
        return;
      }

      setInput("");
      setStreamState("starting");
      setStatusText("Starting");

      try {
        const thread = await ensureThread(prompt);
        const userMessage = makeMessage("user", prompt);
        const assistantMessage = makeMessage("assistant", "");
        const previousMessages = messagesRef.current;
        const nextMessages = [...previousMessages, userMessage, assistantMessage];
        setMessages(nextMessages);
        messagesRef.current = nextMessages;
        void persistMessages(thread, nextMessages);

        const response = await postChatV2Message({
          workflow_id: thread.workflowId,
          message: prompt,
          history: normalizeChatV2History(previousMessages),
          thread_id: thread.id,
          session_id: thread.id,
          mode,
          surface: `editor:v2:${thread.id}`,
          surface_type: "editor",
          surface_id: `v2:${thread.id}`,
          surface_context: {
            ui_version: "dan-chat-v2",
            endpoint_contract: "chat_stream_v1",
            control_surface: "lightweight_frontend",
          },
        });

        if (response.type === "run_error") {
          const error = response.error as { message?: string } | undefined;
          finishStream(thread, assistantMessage.id, {
            content: error?.message || "Run failed.",
          });
          return;
        }

        if (!response.stream_channel_id) {
          finishStream(thread, assistantMessage.id, {
            content:
              response.type === "run_started"
                ? `Started ${response.scope || "full"} run.`
                : "No stream returned.",
          });
          return;
        }

        connectToStream(
          response.stream_channel_id,
          thread,
          assistantMessage.id,
          response.message_id,
        );
      } catch (error) {
        setStreamState("error");
        setStatusText(error instanceof Error ? error.message : "Request failed");
      }
    },
    [connectToStream, ensureThread, finishStream, input, mode, persistMessages, streamState],
  );

  const stopStream = useCallback(async () => {
    const stream = activeStreamRef.current;
    if (!stream) return;
    setStatusText("Stopping");
    try {
      await stopChatV2Stream(stream.channelId, stream.responseMessageId);
    } catch {
      // Closing the local socket still releases the V2 UI even if the backend
      // already dropped the channel.
    }
    const nextMessages = applyMessageUpdate((previous) =>
      previous.map((message) =>
        message.id === stream.assistantId
          ? {
              ...message,
              content: message.content || "Stopped.",
            }
          : message,
      ),
    );
    wsRef.current?.close();
    wsRef.current = null;
    activeStreamRef.current = null;
    setStreamState("idle");
    setStreamChannelId(null);
    setStatusText("Ready");
    void persistMessages(stream.thread, nextMessages);
  }, [applyMessageUpdate, persistMessages]);

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    void sendMessage();
  };

  const busy =
    streamState === "starting" ||
    streamState === "streaming" ||
    streamState === "queued";

  return (
    <div className="h-screen w-screen overflow-hidden bg-[#f7faf6] text-[#151713] dark:bg-[#0f1310] dark:text-[#f2f5ef]">
      <div className="flex h-full">
        <aside
          className={`z-30 flex h-full w-[286px] shrink-0 flex-col border-r border-[#d8ded6] bg-[#eef4ec] transition-transform dark:border-[#263028] dark:bg-[#111712] ${
            sidebarOpen ? "translate-x-0" : "-translate-x-full"
          } fixed inset-y-0 left-0 md:static md:translate-x-0`}
        >
          <div className="flex h-14 items-center gap-2 px-3">
            <button
              type="button"
              onClick={() => {
                window.location.hash = "chat";
              }}
              className="grid h-8 w-8 place-items-center rounded-lg text-[#5f6b5d] transition hover:bg-white hover:text-[#151713] dark:text-[#b8c4b6] dark:hover:bg-[#1d251f] dark:hover:text-white"
              title="Classic DAN"
            >
              <ChevronLeft size={17} />
            </button>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">DAN V2</div>
              <div className="truncate text-[11px] text-[#697565] dark:text-[#a4afa1]">
                {activeThread?.title || "Light chat"}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setSidebarOpen(false)}
              className="grid h-8 w-8 place-items-center rounded-lg text-[#5f6b5d] transition hover:bg-white hover:text-[#151713] dark:text-[#b8c4b6] dark:hover:bg-[#1d251f] dark:hover:text-white md:hidden"
              title="Close sidebar"
            >
              <PanelLeftClose size={17} />
            </button>
          </div>

          <div className="px-3 pb-3">
            <button
              type="button"
              onClick={startNewChat}
              className="flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-[#151713] px-3 text-sm font-medium text-white transition hover:bg-[#263128] dark:bg-[#f0f4ed] dark:text-[#101410] dark:hover:bg-white"
            >
              <MessageSquarePlus size={16} />
              New chat
            </button>
          </div>

          <div className="px-3 pb-2">
            <label className="flex h-9 items-center gap-2 rounded-lg border border-[#d8ded6] bg-white px-3 text-[#6c7669] dark:border-[#293229] dark:bg-[#171e18] dark:text-[#a7b2a3]">
              <Search size={14} />
              <input
                value={threadFilter}
                onChange={(event) => setThreadFilter(event.target.value)}
                className="min-w-0 flex-1 bg-transparent text-sm text-[#151713] outline-none placeholder:text-[#8c9689] dark:text-[#f2f5ef]"
                placeholder="Search chats"
              />
            </label>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            <div className="mb-2 flex items-center gap-2 px-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#798375] dark:text-[#9da89a]">
              <History size={13} />
              Chats
            </div>
            {loadingThreads && (
              <div className="px-2 py-2 text-xs text-[#687464] dark:text-[#a8b4a5]">
                Loading
              </div>
            )}
            {visibleThreads.map((thread) => {
              const active = activeThread?.id === thread.id;
              return (
                <div
                  key={`${thread.workflow_id}:${thread.id}`}
                  className={`group mb-1 flex w-full items-start gap-2 rounded-lg transition ${
                    active
                      ? "bg-white shadow-sm dark:bg-[#1b231d]"
                      : "hover:bg-white/70 dark:hover:bg-[#182019]"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => void openThread(thread)}
                    className="flex min-w-0 flex-1 items-start gap-2 px-2 py-2 text-left"
                  >
                    <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-emerald-600" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {thread.title || "Untitled"}
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-[#6f796b] dark:text-[#a5afa1]">
                        {thread.workflow_id} · {formatTime(thread.updated_at)}
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      void removeThread(thread);
                    }}
                    className="mr-1 mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-md text-[#849080] opacity-0 transition hover:bg-red-50 hover:text-red-600 group-hover:opacity-100 dark:hover:bg-red-500/10"
                    title="Delete chat"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              );
            })}
          </div>

          <div className="border-t border-[#d8ded6] p-3 dark:border-[#263028]">
            <div className="flex items-center justify-between rounded-lg bg-white px-3 py-2 text-xs text-[#566153] dark:bg-[#171e18] dark:text-[#c3cec0]">
              <span className="inline-flex items-center gap-2">
                <StatusDot state={serverState} />
                Server
              </span>
              <span className="capitalize">{serverState}</span>
            </div>
          </div>
        </aside>

        {sidebarOpen && (
          <button
            type="button"
            className="fixed inset-0 z-20 bg-black/20 md:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close sidebar overlay"
          />
        )}

        <main className="flex min-w-0 flex-1 flex-col md:ml-0">
          <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[#dfe5dd] bg-[#f7faf6]/92 px-3 backdrop-blur dark:border-[#263028] dark:bg-[#0f1310]/92">
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              className="grid h-9 w-9 place-items-center rounded-lg text-[#52604f] transition hover:bg-white dark:text-[#c4d0c1] dark:hover:bg-[#1b231d] md:hidden"
              title="Open sidebar"
            >
              <Menu size={18} />
            </button>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">
                {activeThread?.title || "New chat"}
              </div>
              <div className="truncate text-xs text-[#6c7868] dark:text-[#a4b0a0]">
                {statusText}
                {streamChannelId ? ` · ${streamChannelId}` : ""}
              </div>
            </div>
            <div className="hidden items-center gap-1 rounded-lg border border-[#d8ded6] bg-white p-1 dark:border-[#293229] dark:bg-[#151b16] sm:flex">
              {MODE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setMode(option.id)}
                  className={`h-7 rounded-md px-2.5 text-xs font-medium transition ${
                    mode === option.id
                      ? "bg-[#151713] text-white dark:bg-[#eef4ec] dark:text-[#101410]"
                      : "text-[#5f6b5c] hover:bg-[#f0f4ef] dark:text-[#aab5a7] dark:hover:bg-[#202820]"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </header>

          <section className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
            {messages.length === 0 ? (
              <EmptyState />
            ) : (
              <div className="mx-auto flex max-w-4xl flex-col gap-5">
                {messages.map((message) => (
                  <MessageRow key={message.id} message={message} />
                ))}
              </div>
            )}
          </section>

          <footer className="shrink-0 border-t border-[#dfe5dd] bg-[#f7faf6] px-3 py-3 dark:border-[#263028] dark:bg-[#0f1310]">
            <form
              onSubmit={(event) => void sendMessage(event)}
              className="mx-auto flex max-w-4xl items-end gap-2 rounded-2xl border border-[#cfd8cc] bg-white p-2 shadow-sm dark:border-[#293229] dark:bg-[#151b16]"
            >
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                rows={1}
                className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-3 py-2.5 text-[15px] leading-6 text-[#151713] outline-none placeholder:text-[#7e897a] dark:text-[#f2f5ef] dark:placeholder:text-[#8d9989]"
                placeholder="Message DAN"
                disabled={busy}
              />
              {busy ? (
                <button
                  type="button"
                  onClick={() => void stopStream()}
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-[#151713] text-white transition hover:bg-[#30372f] dark:bg-[#eef4ec] dark:text-[#101410] dark:hover:bg-white"
                  title="Stop"
                >
                  <Square size={16} fill="currentColor" />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-emerald-700 text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-[#bac5b7] dark:disabled:bg-[#384237]"
                  title="Send"
                >
                  <ArrowUp size={18} />
                </button>
              )}
            </form>
          </footer>
        </main>
      </div>
    </div>
  );
}
