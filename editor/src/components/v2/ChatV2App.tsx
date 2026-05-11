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
  Activity,
  ArrowUp,
  Bot,
  CheckCircle2,
  ChevronLeft,
  Clock3,
  ExternalLink,
  FileText,
  GitBranch,
  GitFork,
  History,
  Image as ImageIcon,
  ListChecks,
  Menu,
  MessageSquarePlus,
  PanelLeftClose,
  Paperclip,
  RotateCcw,
  Search,
  Square,
  Trash2,
  User,
  Wrench,
  X,
} from "lucide-react";
import {
  connectChatV2AgentRunEvents,
  connectChatV2Stream,
  createChatV2AgentRun,
  createChatV2Thread,
  deleteChatV2Thread,
  executeChatV2AgentRun,
  getChatV2Thread,
  listChatV2ThreadTasks,
  listChatV2Threads,
  normalizeChatV2History,
  postChatV2Message,
  postChatV2AgentRunCommand,
  saveChatV2Thread,
  stopChatV2Stream,
  type ChatV2AgentRunEvent,
  type ChatV2Mode,
  type ChatV2ProductMode,
  type ChatV2TaskSnapshot,
  type ChatV2ThreadSummary,
} from "../../lib/chatV2Api";
import {
  buildAttachmentContext,
  composerDraftToChatAttachment,
  normalizeAttachmentDrafts,
  resolveAttachmentName,
  type ComposerAttachmentDraft,
} from "../../lib/editorChat";
import { isElectron, nativeDialog, nativeShell } from "../../lib/electronBridge";
import { upsertToolCallResult, upsertToolCallStart } from "../../lib/toolCallState";
import type {
  ChatAttachment,
  ChatMessage,
  ChatStreamEvent,
  RunEventPayload,
} from "../../types/chat";

const DEFAULT_WORKFLOW_ID = "_scratch";
const LAST_SESSION_STORAGE_KEY = "dan.chatV2.lastSession.v1";
const PROFILE_STORAGE_KEY = "dan.chatV2.agentProfile.v1";

const MODE_OPTIONS: Array<{ id: ChatV2ProductMode; label: string }> = [
  { id: "chat", label: "Chat" },
  { id: "agent", label: "Agent" },
];

type AgentProfileId = "fast" | "balanced" | "deep" | "max";

const AGENT_PROFILES: Array<{
  id: AgentProfileId;
  label: string;
  summary: string;
  policy: Record<string, unknown>;
}> = [
  {
    id: "fast",
    label: "Fast",
    summary: "short bounded run",
    policy: { depth: "fast", budget: "low", validation: "light" },
  },
  {
    id: "balanced",
    label: "Balanced",
    summary: "default agent pass",
    policy: { depth: "balanced", budget: "standard", validation: "standard" },
  },
  {
    id: "deep",
    label: "Deep",
    summary: "more search and validation",
    policy: { depth: "deep", budget: "high", validation: "strong" },
  },
  {
    id: "max",
    label: "Max",
    summary: "widest practical pass",
    policy: { depth: "max", budget: "max", validation: "strongest" },
  },
];

type ActiveThread = {
  id: string;
  workflowId: string;
  title?: string;
};

type StreamState = "idle" | "starting" | "queued" | "streaming" | "error";
type ServerState = "checking" | "online" | "offline";
type SendIntent = "normal" | "append" | "continue_after_current";

const TERMINAL_TASK_STATUSES = new Set(["completed", "failed", "blocked", "stopped"]);

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

function modeToRequestMode(mode: ChatV2ProductMode): ChatV2Mode {
  return mode === "agent" ? "agent" : "conversation";
}

function coerceMode(value: unknown): ChatV2ProductMode {
  return value === "agent" ? "agent" : "chat";
}

function coerceAgentProfile(value: unknown): AgentProfileId {
  return AGENT_PROFILES.some((profile) => profile.id === value)
    ? (value as AgentProfileId)
    : "balanced";
}

function activeProfile(profileId: AgentProfileId) {
  return AGENT_PROFILES.find((profile) => profile.id === profileId) ?? AGENT_PROFILES[1];
}

function fileNameFromPath(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function mimeTypeFromName(name: string): string | undefined {
  const lower = name.toLowerCase();
  if (lower.endsWith(".pdf")) return "application/pdf";
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".gif")) return "image/gif";
  if (lower.endsWith(".json")) return "application/json";
  if (lower.endsWith(".csv")) return "text/csv";
  if (lower.endsWith(".md") || lower.endsWith(".txt")) return "text/plain";
  return undefined;
}

function attachmentDraftFromPath(path: string): ComposerAttachmentDraft {
  const name = fileNameFromPath(path);
  const mimeType = mimeTypeFromName(name);
  return {
    id: crypto.randomUUID(),
    kind: mimeType?.startsWith("image/") ? "figure" : "file",
    name,
    path,
    mimeType,
    source: "editor",
  };
}

function attachmentDraftFromFile(file: File): ComposerAttachmentDraft {
  return {
    id: crypto.randomUUID(),
    kind: file.type.startsWith("image/") ? "figure" : "file",
    name: resolveAttachmentName(file.name, file.type),
    size: file.size,
    mimeType: file.type || mimeTypeFromName(file.name),
    source: "browser",
    file,
  };
}

function compactId(value?: string | null): string {
  if (!value) return "";
  return value.length > 16 ? `${value.slice(0, 6)}...${value.slice(-6)}` : value;
}

function artifactLabel(ref: Record<string, unknown>): string {
  const raw =
    ref.path ??
    ref.file_path ??
    ref.name ??
    ref.title ??
    ref.id ??
    ref.url ??
    "artifact";
  return fileNameFromPath(String(raw));
}

function artifactPath(ref: Record<string, unknown>): string {
  const raw = ref.path ?? ref.file_path ?? ref.url ?? "";
  return typeof raw === "string" ? raw : "";
}

function artifactsFromMessage(message: ChatMessage): Array<Record<string, unknown>> {
  const artifacts: Array<Record<string, unknown>> = [];
  for (const event of message.runEvents ?? []) {
    const refs = event.detail?.artifact_refs;
    if (Array.isArray(refs)) {
      for (const ref of refs) {
        if (ref && typeof ref === "object") artifacts.push(ref as Record<string, unknown>);
      }
    }
  }
  return artifacts.slice(-4);
}

function surfaceLabel(task: ChatV2TaskSnapshot): string {
  const lastTurn = task.metadata?.last_surface_turn;
  if (lastTurn && typeof lastTurn === "object") {
    const raw = lastTurn as Record<string, unknown>;
    const surfaceType = typeof raw.surface_type === "string" ? raw.surface_type : "";
    const surfaceId = typeof raw.surface_id === "string" ? raw.surface_id : "";
    if (surfaceType || surfaceId) return [surfaceType, surfaceId].filter(Boolean).join(" / ");
  }
  return "";
}

function findAssistantMessageForTask(
  messages: ChatMessage[],
  runId?: string | null,
  taskId?: string | null,
): string | undefined {
  const match = [...messages].reverse().find((message) => {
    if (message.role !== "assistant") return false;
    return (
      (runId && message.taskRunRef?.runId === runId) ||
      (taskId && message.taskRunRef?.taskId === taskId)
    );
  });
  return match?.id;
}

function lastSessionFromStorage():
  | { workflowId: string; threadId: string; mode?: ChatV2ProductMode }
  | null {
  try {
    const raw = window.localStorage.getItem(LAST_SESSION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const workflowId = typeof parsed.workflowId === "string" ? parsed.workflowId : "";
    const threadId = typeof parsed.threadId === "string" ? parsed.threadId : "";
    if (!workflowId || !threadId) return null;
    return { workflowId, threadId, mode: coerceMode(parsed.mode) };
  } catch {
    return null;
  }
}

function eventSummary(event: RunEventPayload): string {
  return event.summary || event.event_type || event.type || "Run event";
}

function agentEventSummary(event: ChatV2AgentRunEvent): string {
  return event.summary || event.source_event_type || event.type || "Agent event";
}

function agentEventToRunEvent(event: ChatV2AgentRunEvent): RunEventPayload {
  return {
    type: event.type,
    event_type: event.source_event_type || event.type,
    summary: agentEventSummary(event),
    detail: {
      run_id: event.run_id ?? "",
      task_id: event.task_id ?? "",
      payload: event.payload ?? {},
      artifact_refs: event.artifact_refs ?? [],
    },
  };
}

function isTaskTerminal(task?: ChatV2TaskSnapshot | null): boolean {
  return Boolean(task && TERMINAL_TASK_STATUSES.has(task.status));
}

function taskRunRefFromTask(task?: ChatV2TaskSnapshot | null): ChatMessage["taskRunRef"] {
  if (!task) return null;
  const runId =
    typeof task.metadata?.active_run_id === "string"
      ? task.metadata.active_run_id
      : null;
  return {
    taskId: task.task_id,
    runId,
    status: task.status,
    workspaceRoot:
      typeof task.metadata?.workspace_root === "string"
        ? task.metadata.workspace_root
        : "",
    workspaceId:
      typeof task.metadata?.workspace_id === "string"
        ? task.metadata.workspace_id
        : "",
  };
}

function taskRunRefFromResponse(
  value: unknown,
): ChatMessage["taskRunRef"] {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  return {
    taskId: typeof raw.task_id === "string" ? raw.task_id : null,
    runId: typeof raw.run_id === "string" ? raw.run_id : null,
    status: typeof raw.status === "string" ? raw.status : "",
    workspaceRoot:
      typeof raw.workspace_root === "string" ? raw.workspace_root : "",
    workspaceId: typeof raw.workspace_id === "string" ? raw.workspace_id : "",
  };
}

function laneLabel(lane: string): string {
  return lane === "append" ? "checkpoint append" : "run after current";
}

function branchLabel(thread: ChatV2ThreadSummary): string {
  if (!thread.parent_thread_id) return "";
  if (thread.branch_type === "edit") return "edit branch";
  if (thread.branch_type === "regenerate") return "regen branch";
  return "explore branch";
}

function branchDepth(
  thread: ChatV2ThreadSummary,
  byId: Record<string, ChatV2ThreadSummary>,
): number {
  let depth = 0;
  let parentId = thread.parent_thread_id ?? "";
  const seen = new Set<string>([thread.id]);
  while (parentId && byId[parentId] && !seen.has(parentId)) {
    depth += 1;
    seen.add(parentId);
    parentId = byId[parentId].parent_thread_id ?? "";
  }
  return Math.min(depth, 3);
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

function AttachmentChips({
  attachments,
  onRemove,
}: {
  attachments: ChatAttachment[] | ComposerAttachmentDraft[];
  onRemove?: (idOrIndex: string | number) => void;
}) {
  if (!attachments.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {attachments.map((attachment, index) => {
        const key = "id" in attachment && attachment.id ? attachment.id : index;
        const name =
          "name" in attachment
            ? attachment.name
            : attachment.filename || attachment.path || "Attachment";
        const path = attachment.path || "";
        const isImage =
          attachment.kind === "figure" ||
          (attachment.mimeType ?? "").startsWith("image/");
        const Icon = isImage ? ImageIcon : FileText;
        return (
          <span
            key={key}
            className="inline-flex min-h-7 max-w-full items-center gap-1.5 rounded-full border border-[#d8ded6] bg-white px-2.5 text-[11px] font-medium text-[#4a5148] dark:border-[#30382f] dark:bg-[#151a16] dark:text-[#cfd7cd]"
            title={path || name}
          >
            <Icon size={12} />
            <span className="max-w-44 truncate">{name}</span>
            {onRemove && (
              <button
                type="button"
                onClick={() => onRemove(typeof key === "string" ? key : index)}
                className="grid h-4 w-4 place-items-center rounded-full text-[#7a8477] hover:bg-[#eef4ec] hover:text-red-600 dark:hover:bg-[#242c24]"
                title="Remove attachment"
              >
                <X size={10} />
              </button>
            )}
          </span>
        );
      })}
    </div>
  );
}

function ArtifactChips({
  artifacts,
  onOpen,
}: {
  artifacts: Array<Record<string, unknown>>;
  onOpen?: (path: string) => void;
}) {
  if (!artifacts.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {artifacts.slice(0, 5).map((artifact, index) => {
        const path = artifactPath(artifact);
        return (
          <button
            key={`${artifactLabel(artifact)}-${index}`}
            type="button"
            onClick={() => path && onOpen?.(path)}
            disabled={!path}
            className="inline-flex min-h-7 max-w-full items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 text-[11px] font-medium text-emerald-900 transition hover:border-emerald-400 disabled:cursor-default disabled:hover:border-emerald-200 dark:border-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200"
            title={path || artifactLabel(artifact)}
          >
            <FileText size={12} />
            <span className="max-w-44 truncate">{artifactLabel(artifact)}</span>
            {path && <ExternalLink size={11} />}
          </button>
        );
      })}
    </div>
  );
}

function MessageRefStrip({
  message,
  onOpenPath,
}: {
  message: ChatMessage;
  onOpenPath: (path: string) => void;
}) {
  const artifacts = artifactsFromMessage(message);
  if (!message.taskRunRef && artifacts.length === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-1.5">
      {message.taskRunRef && (
        <div className="flex flex-wrap gap-2 text-[11px]">
          {message.taskRunRef.taskId && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[#d8ded6] bg-[#f8fbf8] px-2.5 py-1 text-[#52604f] dark:border-[#30382f] dark:bg-[#101511] dark:text-[#c2cec0]">
              <ListChecks size={12} />
              task {compactId(message.taskRunRef.taskId)}
            </span>
          )}
          {message.taskRunRef.runId && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[#d8ded6] bg-[#f8fbf8] px-2.5 py-1 text-[#52604f] dark:border-[#30382f] dark:bg-[#101511] dark:text-[#c2cec0]">
              <Activity size={12} />
              run {compactId(message.taskRunRef.runId)}
            </span>
          )}
        </div>
      )}
      <ArtifactChips artifacts={artifacts} onOpen={onOpenPath} />
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

function MessageRow({
  message,
  onBranch,
  onOpenPath,
  branchDisabled,
}: {
  message: ChatMessage;
  onBranch: (messageId: string) => void;
  onOpenPath: (path: string) => void;
  branchDisabled: boolean;
}) {
  const isUser = message.role === "user";
  const Icon = isUser ? User : Bot;

  return (
    <article className={`group flex gap-4 ${isUser ? "justify-end" : "justify-start"}`}>
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
        {message.attachments && message.attachments.length > 0 && (
          <AttachmentChips attachments={message.attachments} />
        )}
        <MessageRefStrip message={message} onOpenPath={onOpenPath} />
        {!isUser && <ToolStrip message={message} />}
        <div className="mt-2 flex justify-end">
          <button
            type="button"
            onClick={() => onBranch(message.id)}
            disabled={branchDisabled}
            className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] font-medium text-[#6d776a] opacity-0 transition hover:bg-[#eef4ec] hover:text-[#263128] disabled:cursor-not-allowed disabled:opacity-0 group-hover:opacity-100 dark:text-[#a7b2a3] dark:hover:bg-[#222a22] dark:hover:text-white"
            title={
              branchDisabled
                ? "Finish the active Agent run before branching"
                : "Branch from this message"
            }
          >
            <GitFork size={11} />
            Branch
          </button>
        </div>
      </div>
      {isUser && (
        <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-emerald-700 text-white">
          <Icon size={16} />
        </div>
      )}
    </article>
  );
}

function AgentRunPanel({
  task,
  tasks,
  events,
  onStop,
  onRetry,
  onOpenPath,
}: {
  task: ChatV2TaskSnapshot | null;
  tasks: ChatV2TaskSnapshot[];
  events: ChatV2AgentRunEvent[];
  onStop: () => void;
  onRetry: (task: ChatV2TaskSnapshot) => void;
  onOpenPath: (path: string) => void;
}) {
  if (!task && tasks.length === 0) return null;
  const primary = task ?? tasks[0];
  if (!primary) return null;
  const queueItems = primary.metadata?.queue_items ?? [];
  const activeRunId =
    typeof primary.metadata?.active_run_id === "string"
      ? primary.metadata.active_run_id
      : "";
  const terminal = isTaskTerminal(primary);
  const latestArtifacts = primary.latest_artifact_refs ?? [];
  const traceRefs = primary.trace_refs ?? [];
  const origin = surfaceLabel(primary);
  const tokenUsage = primary.metadata?.token_usage;
  const totalTokens =
    tokenUsage && typeof tokenUsage === "object"
      ? (tokenUsage as Record<string, unknown>).total_tokens
      : undefined;

  return (
    <div className="border-b border-[#dfe5dd] bg-[#eef4ec]/85 px-3 py-3 dark:border-[#263028] dark:bg-[#111712]/85">
      <div className="mx-auto flex max-w-4xl flex-col gap-3 rounded-xl border border-[#d1dccd] bg-white px-3 py-3 text-sm shadow-sm dark:border-[#293229] dark:bg-[#151b16]">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-[#151713] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-white dark:bg-[#eef4ec] dark:text-[#101410]">
                <ListChecks size={12} />
                Agent
              </span>
              <span className="rounded-full border border-[#d8ded6] px-2 py-0.5 text-xs capitalize text-[#52604f] dark:border-[#30382f] dark:text-[#bfcbc0]">
                {primary.status}
              </span>
              {primary.phase && (
                <span className="rounded-full border border-[#d8ded6] px-2 py-0.5 text-xs text-[#52604f] dark:border-[#30382f] dark:text-[#bfcbc0]">
                  {primary.phase}
                </span>
              )}
              {origin && (
                <span className="rounded-full border border-[#d8ded6] px-2 py-0.5 text-xs text-[#52604f] dark:border-[#30382f] dark:text-[#bfcbc0]">
                  {origin}
                </span>
              )}
            </div>
            <div className="mt-2 max-w-2xl truncate text-[#263128] dark:text-[#e7ece5]">
              {primary.latest_progress || "Agent run accepted."}
            </div>
            <div className="mt-1 truncate text-xs text-[#6f796b] dark:text-[#a5afa1]">
              {primary.task_id}
              {activeRunId ? ` · ${activeRunId}` : ""}
              {typeof totalTokens === "number" ? ` · ${totalTokens} tokens` : ""}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {!terminal && activeRunId && (
              <button
                type="button"
                onClick={onStop}
                className="inline-flex h-9 items-center gap-2 rounded-lg border border-[#d8ded6] px-3 text-xs font-medium text-[#30362f] transition hover:border-red-300 hover:bg-red-50 hover:text-red-700 dark:border-[#30382f] dark:text-[#dce4da] dark:hover:border-red-700 dark:hover:bg-red-500/10 dark:hover:text-red-300"
                title="Record stop command"
              >
                <Square size={13} fill="currentColor" />
                Stop
              </button>
            )}
            {terminal && activeRunId && (
              <button
                type="button"
                onClick={() => onRetry(primary)}
                className="inline-flex h-9 items-center gap-2 rounded-lg border border-[#d8ded6] px-3 text-xs font-medium text-[#30362f] transition hover:border-emerald-500 hover:bg-emerald-50 dark:border-[#30382f] dark:text-[#dce4da] dark:hover:bg-emerald-500/10"
                title="Retry this Agent run"
              >
                <RotateCcw size={13} />
                Retry
              </button>
            )}
            {traceRefs[0] && (
              <button
                type="button"
                onClick={() => onOpenPath(traceRefs[0])}
                className="inline-flex h-9 items-center gap-2 rounded-lg border border-[#d8ded6] px-3 text-xs font-medium text-[#30362f] transition hover:border-emerald-500 hover:bg-emerald-50 dark:border-[#30382f] dark:text-[#dce4da] dark:hover:bg-emerald-500/10"
                title={traceRefs[0]}
              >
                <ExternalLink size={13} />
                Open log
              </button>
            )}
          </div>
        </div>

        {(latestArtifacts.length > 0 || traceRefs.length > 0) && (
          <div className="rounded-lg border border-[#dfe5dd] bg-[#f8fbf8] px-3 py-2 dark:border-[#293229] dark:bg-[#101511]">
            <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#61705e] dark:text-[#aab6a7]">
              <span>Outputs</span>
              {traceRefs.length > 0 && <span>{traceRefs.length} trace refs</span>}
            </div>
            <ArtifactChips artifacts={latestArtifacts} onOpen={onOpenPath} />
            {traceRefs.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {traceRefs.slice(0, 3).map((trace) => (
                  <button
                    key={trace}
                    type="button"
                    onClick={() => onOpenPath(trace)}
                    className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-[#d8ded6] bg-white px-2.5 py-1 text-[11px] text-[#4a5148] transition hover:border-emerald-400 dark:border-[#30382f] dark:bg-[#111712] dark:text-[#cfd7cd]"
                    title={trace}
                  >
                    <FileText size={12} />
                    <span className="max-w-52 truncate">{fileNameFromPath(trace)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {tasks.length > 1 && (
          <div className="grid gap-2 sm:grid-cols-2">
            {tasks.slice(0, 4).map((item) => (
              <div
                key={item.task_id}
                className={`rounded-lg border px-3 py-2 ${
                  item.task_id === primary.task_id
                    ? "border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-500/10"
                    : "border-[#dfe5dd] bg-[#f8fbf8] dark:border-[#293229] dark:bg-[#101511]"
                }`}
              >
                <div className="flex items-center justify-between gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#61705e] dark:text-[#aab6a7]">
                  <span className="truncate">{compactId(item.task_id)}</span>
                  <span className="capitalize">{item.status}</span>
                </div>
                <div className="mt-1 line-clamp-2 text-xs text-[#30362f] dark:text-[#dce4da]">
                  {item.latest_progress || item.phase || "Agent task"}
                </div>
              </div>
            ))}
          </div>
        )}

        {queueItems.length > 0 && (
          <div className="grid gap-2 sm:grid-cols-2">
            {queueItems.slice(0, 4).map((item) => (
              <div
                key={item.id}
                className="rounded-lg border border-[#dfe5dd] bg-[#f8fbf8] px-3 py-2 dark:border-[#293229] dark:bg-[#101511]"
              >
                <div className="flex items-center justify-between gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#61705e] dark:text-[#aab6a7]">
                  <span>{laneLabel(item.lane)}</span>
                  <span>#{item.position}</span>
                </div>
                <div className="mt-1 line-clamp-2 text-xs text-[#30362f] dark:text-[#dce4da]">
                  {item.text || "Queued follow-up"}
                </div>
              </div>
            ))}
          </div>
        )}

        {events.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {events.slice(-5).map((event, index) => (
              <span
                key={`${event.type}-${event.source_event_id ?? index}`}
                className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-[#d8ded6] bg-white px-2.5 py-1 text-[11px] text-[#4a5148] dark:border-[#30382f] dark:bg-[#111712] dark:text-[#cfd7cd]"
                title={agentEventSummary(event)}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-600" />
                <span className="truncate">{agentEventSummary(event)}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ChatV2App() {
  const [threads, setThreads] = useState<ChatV2ThreadSummary[]>([]);
  const [activeThread, setActiveThread] = useState<ActiveThread | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ChatV2ProductMode>("chat");
  const [agentProfile, setAgentProfile] = useState<AgentProfileId>(() => {
    try {
      return coerceAgentProfile(window.localStorage.getItem(PROFILE_STORAGE_KEY));
    } catch {
      return "balanced";
    }
  });
  const [attachments, setAttachments] = useState<ComposerAttachmentDraft[]>([]);
  const [threadFilter, setThreadFilter] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [streamState, setStreamState] = useState<StreamState>("idle");
  const [serverState, setServerState] = useState<ServerState>("checking");
  const [statusText, setStatusText] = useState("Ready");
  const [streamChannelId, setStreamChannelId] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const [threadTasks, setThreadTasks] = useState<ChatV2TaskSnapshot[]>([]);
  const [threadTaskMap, setThreadTaskMap] = useState<Record<string, ChatV2TaskSnapshot[]>>({});
  const [agentEvents, setAgentEvents] = useState<ChatV2AgentRunEvent[]>([]);
  const [activeAgentRunId, setActiveAgentRunId] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const agentWsRef = useRef<WebSocket | null>(null);
  const messagesRef = useRef<ChatMessage[]>([]);
  const modeRef = useRef<ChatV2ProductMode>("chat");
  const agentProfileRef = useRef<AgentProfileId>("balanced");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const restoredSessionRef = useRef(false);
  const activeStreamRef = useRef<{
    channelId: string;
    thread: ActiveThread;
    assistantId: string;
    responseMessageId?: string;
  } | null>(null);
  const activeAgentRef = useRef<{
    runId: string;
    taskId: string;
    thread: ActiveThread;
    assistantId?: string;
  } | null>(null);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  useEffect(() => {
    agentProfileRef.current = agentProfile;
    try {
      window.localStorage.setItem(PROFILE_STORAGE_KEY, agentProfile);
    } catch {
      // Ignore storage failures in private windows.
    }
  }, [agentProfile]);

  useEffect(() => {
    if (!activeThread) return;
    try {
      window.localStorage.setItem(
        LAST_SESSION_STORAGE_KEY,
        JSON.stringify({
          workflowId: activeThread.workflowId,
          threadId: activeThread.id,
          mode,
          activeAgentRunId,
        }),
      );
    } catch {
      // Session restore is best-effort only.
    }
  }, [activeAgentRunId, activeThread, mode]);

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
      agentWsRef.current?.close();
      agentWsRef.current = null;
    };
  }, []);

  const visibleThreads = useMemo(() => {
    const query = threadFilter.trim().toLowerCase();
    if (!query) return threads;
    return threads.filter((thread) => {
      return `${thread.title} ${thread.workflow_id}`.toLowerCase().includes(query);
    });
  }, [threadFilter, threads]);

  const threadById = useMemo(
    () => Object.fromEntries(threads.map((thread) => [thread.id, thread])),
    [threads],
  );

  useEffect(() => {
    let cancelled = false;
    const visible = threads.slice(0, 24);
    if (visible.length === 0) {
      setThreadTaskMap({});
      return;
    }
    void Promise.all(
      visible.map(async (thread) => {
        const tasks = await listChatV2ThreadTasks(thread.id).catch(() => []);
        return [thread.id, tasks] as const;
      }),
    ).then((entries) => {
      if (cancelled) return;
      setThreadTaskMap(Object.fromEntries(entries));
    });
    return () => {
      cancelled = true;
    };
  }, [threads]);

  const activeTask = useMemo(() => {
    if (activeAgentRunId) {
      const byRun = threadTasks.find(
        (task) => task.metadata?.active_run_id === activeAgentRunId,
      );
      if (byRun) return byRun;
    }
    const active = threadTasks.find((task) => !isTaskTerminal(task));
    return active ?? threadTasks[0] ?? null;
  }, [activeAgentRunId, threadTasks]);

  const activeAgentRunning = Boolean(
    activeTask &&
      !isTaskTerminal(activeTask) &&
      typeof activeTask.metadata?.active_run_id === "string" &&
      activeTask.metadata.active_run_id,
  );

  const refreshThreadTasks = useCallback(
    async (thread: ActiveThread | null = activeThread) => {
      if (!thread) {
        setThreadTasks([]);
        return [];
      }
      try {
        const tasks = await listChatV2ThreadTasks(thread.id);
        setThreadTasks(tasks);
        setThreadTaskMap((previous) => ({ ...previous, [thread.id]: tasks }));
        const active = tasks.find((task) => !isTaskTerminal(task));
        const runId = active?.metadata?.active_run_id;
        if (typeof runId === "string" && runId) {
          setActiveAgentRunId((current) => current ?? runId);
        } else {
          setActiveAgentRunId(null);
        }
        return tasks;
      } catch {
        return [];
      }
    },
    [activeThread],
  );

  useEffect(() => {
    void refreshThreadTasks(activeThread);
  }, [activeThread, refreshThreadTasks]);

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

  const upsertTaskSnapshot = useCallback((task?: ChatV2TaskSnapshot | null) => {
    if (!task) return;
    setThreadTasks((previous) => {
      const index = previous.findIndex((item) => item.task_id === task.task_id);
      if (index < 0) return [task, ...previous];
      return previous.map((item) => (item.task_id === task.task_id ? task : item));
    });
    const runId = task.metadata?.active_run_id;
    if (typeof runId === "string" && runId && !isTaskTerminal(task)) {
      setActiveAgentRunId(runId);
    }
  }, []);

  const persistMessages = useCallback(
    async (thread: ActiveThread, nextMessages: ChatMessage[]) => {
      try {
        await saveChatV2Thread(thread.workflowId, thread.id, {
          messages: nextMessages,
          mode: modeToRequestMode(modeRef.current),
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
    agentWsRef.current?.close();
    agentWsRef.current = null;
    activeStreamRef.current = null;
    activeAgentRef.current = null;
    setActiveThread(null);
    setMessages([]);
    setInput("");
    setAttachments([]);
    setStreamState("idle");
    setStreamChannelId(null);
    setThreadTasks([]);
    setAgentEvents([]);
    setActiveAgentRunId(null);
    setStatusText("Ready");
  }, []);

  const openThread = useCallback(
    async (summary: ChatV2ThreadSummary) => {
      if (
        streamState === "streaming" ||
        streamState === "queued" ||
        streamState === "starting" ||
        activeAgentRunning
      ) {
        return;
      }
      setStatusText("Loading thread");
      try {
        const thread = await getChatV2Thread(summary.workflow_id, summary.id);
        const nextThread = {
          id: thread.id,
          workflowId: thread.workflow_id,
          title: thread.title,
        };
        agentWsRef.current?.close();
        agentWsRef.current = null;
        activeAgentRef.current = null;
        setActiveAgentRunId(null);
        setAgentEvents([]);
        setActiveThread(nextThread);
        setMessages(thread.messages);
        setMode(coerceMode(thread.mode ?? summary.mode));
        await refreshThreadTasks(nextThread);
        setStatusText("Ready");
      } catch {
        setStatusText("Thread load failed");
      }
    },
    [activeAgentRunning, refreshThreadTasks, streamState],
  );

  useEffect(() => {
    if (restoredSessionRef.current || activeThread || loadingThreads || threads.length === 0) {
      return;
    }
    restoredSessionRef.current = true;
    const saved = lastSessionFromStorage();
    const target = saved
      ? threads.find(
          (thread) =>
            thread.id === saved.threadId && thread.workflow_id === saved.workflowId,
        )
      : null;
    if (target) {
      if (saved?.mode) setMode(saved.mode);
      void openThread(target);
    }
  }, [activeThread, loadingThreads, openThread, threads]);

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

  const openLocalPath = useCallback(async (path: string) => {
    if (!path) return;
    const opened = await nativeShell.openPath(path);
    if (opened) {
      setStatusText("Opened");
      return;
    }
    try {
      await navigator.clipboard.writeText(path);
      setStatusText("Path copied");
    } catch {
      setStatusText("Could not open path");
    }
  }, []);

  const addAttachmentsFromDialog = useCallback(async () => {
    if (!isElectron()) {
      fileInputRef.current?.click();
      return;
    }
    const paths = await nativeDialog.openFile({ multiple: true });
    if (!paths?.length) return;
    setAttachments((previous) => [
      ...previous,
      ...paths.map(attachmentDraftFromPath),
    ]);
  }, []);

  const addAttachmentsFromInput = useCallback(
    (files: FileList | null) => {
      if (!files?.length) return;
      setAttachments((previous) => [
        ...previous,
        ...Array.from(files).map(attachmentDraftFromFile),
      ]);
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
    [],
  );

  const removeAttachment = useCallback((idOrIndex: string | number) => {
    setAttachments((previous) =>
      previous.filter((attachment, index) =>
        typeof idOrIndex === "string" ? attachment.id !== idOrIndex : index !== idOrIndex,
      ),
    );
  }, []);

  const branchFromMessage = useCallback(
    async (messageId: string) => {
      if (!activeThread || activeAgentRunning) return;
      const sourceMessages = messagesRef.current;
      const index = sourceMessages.findIndex((message) => message.id === messageId);
      if (index < 0) return;
      setStatusText("Creating branch");
      try {
        const branchMessages = sourceMessages.slice(0, index + 1);
        const sourceMessage = sourceMessages[index];
        const titleSeed = sourceMessage.content || activeThread.title || "Branch";
        const created = await createChatV2Thread(activeThread.workflowId, {
          title: `${titleFromPrompt(titleSeed)} branch`,
          mode: modeToRequestMode(modeRef.current),
          parent_thread_id: activeThread.id,
          branch_point_message_id: messageId,
          branch_type: "explore",
        });
        const nextThread = {
          id: created.id,
          workflowId: created.workflow_id || activeThread.workflowId,
          title: created.title,
        };
        await saveChatV2Thread(nextThread.workflowId, nextThread.id, {
          messages: branchMessages,
          mode: modeToRequestMode(modeRef.current),
        });
        agentWsRef.current?.close();
        agentWsRef.current = null;
        activeAgentRef.current = null;
        setActiveAgentRunId(null);
        setAgentEvents([]);
        setThreadTasks([]);
        setActiveThread(nextThread);
        setMessages(branchMessages);
        messagesRef.current = branchMessages;
        await refreshThreads();
        setStatusText("Branch ready");
      } catch (error) {
        setStatusText(error instanceof Error ? error.message : "Branch failed");
      }
    },
    [activeAgentRunning, activeThread, refreshThreads],
  );

  const ensureThread = useCallback(
    async (prompt: string): Promise<ActiveThread> => {
      if (activeThread) return activeThread;
      const created = await createChatV2Thread(DEFAULT_WORKFLOW_ID, {
        title: titleFromPrompt(prompt),
        mode: modeToRequestMode(mode),
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

  const connectToAgentRun = useCallback(
    (
      runId: string,
      taskId: string,
      thread: ActiveThread,
      assistantId?: string,
    ) => {
      agentWsRef.current?.close();
      setActiveAgentRunId(runId);
      setAgentEvents([]);
      activeAgentRef.current = { runId, taskId, thread, assistantId };
      setStatusText("Agent running");

      const handleEvent = (event: ChatV2AgentRunEvent) => {
        if (event.type === "error") {
          setStatusText(String(event.summary || "Agent stream error"));
          return;
        }

        setAgentEvents((previous) => [...previous, event]);
        setStatusText(agentEventSummary(event));
        void refreshThreadTasks(thread);

        if (assistantId) {
          applyMessageUpdate((previous) =>
            previous.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content:
                      TERMINAL_TASK_STATUSES.has(event.type)
                        ? agentEventSummary(event)
                        : message.content || agentEventSummary(event),
                    runEvents: [
                      ...(message.runEvents ?? []),
                      agentEventToRunEvent(event),
                    ],
                    taskRunRef: {
                      taskId: event.task_id ?? taskId,
                      runId: event.run_id ?? runId,
                      status: event.type,
                      workspaceRoot: message.taskRunRef?.workspaceRoot ?? "",
                      workspaceId: message.taskRunRef?.workspaceId ?? "",
                    },
                  }
                : message,
            ),
          );
        }

        if (TERMINAL_TASK_STATUSES.has(event.type)) {
          agentWsRef.current?.close();
          agentWsRef.current = null;
          activeAgentRef.current = null;
          setActiveAgentRunId(null);
          setStatusText("Ready");
          if (assistantId) {
            const nextMessages = messagesRef.current;
            void persistMessages(thread, nextMessages);
          }
        }
      };

      const ws = connectChatV2AgentRunEvents(
        runId,
        handleEvent,
        () => {
          if (agentWsRef.current === ws) {
            agentWsRef.current = null;
          }
        },
        () => {
          if (agentWsRef.current === ws) {
            setStatusText("Agent stream error");
          }
        },
      );
      agentWsRef.current = ws;
    },
    [applyMessageUpdate, persistMessages, refreshThreadTasks],
  );

  useEffect(() => {
    if (!activeThread || !activeTask || !activeAgentRunning) return;
    const runId =
      typeof activeTask.metadata?.active_run_id === "string"
        ? activeTask.metadata.active_run_id
        : "";
    if (!runId) return;
    if (activeAgentRef.current?.runId === runId || agentWsRef.current) return;
    const assistantId = findAssistantMessageForTask(
      messagesRef.current,
      runId,
      activeTask.task_id,
    );
    connectToAgentRun(runId, activeTask.task_id, activeThread, assistantId);
    setStatusText("Reconnected Agent run");
  }, [activeAgentRunning, activeTask, activeThread, connectToAgentRun]);

  const sendMessage = useCallback(
    async (event?: FormEvent, intent: SendIntent = "normal") => {
      event?.preventDefault();
      const prompt = input.trim();
      const sourceAttachments = attachments;
      if (!prompt && sourceAttachments.length === 0) return;
      const queueIntent = intent === "append" || intent === "continue_after_current";
      if (
        (streamState === "starting" ||
          streamState === "streaming" ||
          streamState === "queued") &&
        !queueIntent
      ) {
        return;
      }
      if (queueIntent && (!activeAgentRunning || !activeTask)) {
        return;
      }

      setInput("");
      setAttachments([]);
      setStatusText(queueIntent ? "Queueing" : "Starting");

      try {
        const normalizedAttachments = await normalizeAttachmentDrafts(sourceAttachments);
        const attachmentContext = await buildAttachmentContext(normalizedAttachments);
        const requestPrompt = attachmentContext
          ? prompt
            ? `${prompt}\n\n${attachmentContext}`
            : `Please use the appended attachments as context.\n\n${attachmentContext}`
          : prompt;
        const displayPrompt =
          prompt ||
          (normalizedAttachments.length === 1
            ? `Attached ${normalizedAttachments[0].name}`
            : `Attached ${normalizedAttachments.length} items`);
        const persistedAttachments = normalizedAttachments.map(
          composerDraftToChatAttachment,
        );
        const firstAttachmentPath =
          normalizedAttachments.find(
            (attachment) =>
              typeof attachment.path === "string" && attachment.path.trim(),
          )?.path ?? null;
        const attachmentSurfaceContext = {
          attachment_count: normalizedAttachments.length,
          appended_attachments: normalizedAttachments.map((attachment) => ({
            kind: attachment.kind,
            name: attachment.name,
            path: attachment.path ?? null,
            caption: attachment.caption ?? null,
            source: attachment.source ?? null,
          })),
        };
        const thread = await ensureThread(displayPrompt);
        const userMessage = {
          ...makeMessage("user", displayPrompt),
          attachments:
            persistedAttachments.length > 0 ? persistedAttachments : undefined,
        };
        const assistantMessage = makeMessage("assistant", "");
        const previousMessages = messagesRef.current;
        const nextMessages = [...previousMessages, userMessage, assistantMessage];
        setMessages(nextMessages);
        messagesRef.current = nextMessages;
        void persistMessages(thread, nextMessages);

        if (queueIntent && activeTask) {
          const response = await createChatV2AgentRun({
            workflow_id: thread.workflowId,
            message: requestPrompt,
            history: normalizeChatV2History(previousMessages),
            thread_id: thread.id,
            session_id: thread.id,
            mode: "conversation",
            attachment_path: firstAttachmentPath,
            surface: `editor:v2:${thread.id}`,
            surface_type: "editor",
            surface_id: `v2:${thread.id}`,
            surface_context: {
              ui_version: "dan-chat-v2",
              endpoint_contract: "agent_run_v1",
              control_surface: "lightweight_frontend",
              queue_action: intent,
              task_id: activeTask.task_id,
              active_run_id: activeTask.metadata?.active_run_id ?? null,
              ...attachmentSurfaceContext,
            },
          });
          upsertTaskSnapshot(response.task ?? null);
          if (response.event) {
            setAgentEvents((previous) => [...previous, response.event!]);
          }
          const ack =
            response.event?.summary ||
            (intent === "append"
              ? "Queued for checkpoint append."
              : "Queued to run after the current Agent run.");
          const queuedMessages = applyMessageUpdate((previous) =>
            previous.map((message) =>
              message.id === assistantMessage.id
                ? {
                    ...message,
                    content: ack,
                    runEvents: response.event
                      ? [agentEventToRunEvent(response.event)]
                      : message.runEvents,
                    taskRunRef:
                      taskRunRefFromResponse(response.task_run_ref) ??
                      taskRunRefFromTask(response.task ?? activeTask),
                  }
                : message,
            ),
          );
          void persistMessages(thread, queuedMessages);
          setStatusText(ack);
          return;
        }

        if (mode === "agent") {
          setStreamState("starting");
          const response = await createChatV2AgentRun({
            workflow_id: thread.workflowId,
            message: requestPrompt,
            history: normalizeChatV2History(previousMessages),
            thread_id: thread.id,
            session_id: thread.id,
            mode: "agent",
            attachment_path: firstAttachmentPath,
            surface: `editor:v2:${thread.id}`,
            surface_type: "editor",
            surface_id: `v2:${thread.id}`,
            surface_context: {
              ui_version: "dan-chat-v2",
              endpoint_contract: "agent_run_v1",
              control_surface: "lightweight_frontend",
              agent_profile: agentProfileRef.current,
              ...attachmentSurfaceContext,
            },
          });
          upsertTaskSnapshot(response.task ?? null);
          if (response.event) {
            setAgentEvents([response.event]);
          }
          const runId =
            response.v2_control_plane?.run_id ||
            response.task_run_ref?.run_id ||
            null;
          const taskId =
            response.v2_control_plane?.task_id ||
            response.task_run_ref?.task_id ||
            response.task?.task_id ||
            "";
          if (!runId || !taskId) {
            const failedMessages = applyMessageUpdate((previous) =>
              previous.map((message) =>
                message.id === assistantMessage.id
                  ? { ...message, content: "No Agent run was returned." }
                  : message,
              ),
            );
            setStreamState("idle");
            void persistMessages(thread, failedMessages);
            return;
          }
          const acceptedMessages = applyMessageUpdate((previous) =>
            previous.map((message) =>
              message.id === assistantMessage.id
                ? {
                    ...message,
                    content: response.event?.summary || "Agent run accepted.",
                    runEvents: response.event
                      ? [agentEventToRunEvent(response.event)]
                      : message.runEvents,
                    taskRunRef:
                      taskRunRefFromResponse(response.task_run_ref) ??
                      taskRunRefFromTask(response.task),
                  }
                : message,
            ),
          );
          void persistMessages(thread, acceptedMessages);
          connectToAgentRun(runId, taskId, thread, assistantMessage.id);
          setStreamState("idle");
          const profile = activeProfile(agentProfileRef.current);
          await executeChatV2AgentRun(runId, {
            background: true,
            profile_policy: {
              id: profile.id,
              label: profile.label,
              ...profile.policy,
            },
            metadata: {
              requested_from: "editor_v2",
              agent_profile: profile.id,
            },
          });
          return;
        }

        setStreamState("starting");
        const response = await postChatV2Message({
          workflow_id: thread.workflowId,
          message: requestPrompt,
          history: normalizeChatV2History(previousMessages),
          thread_id: thread.id,
          session_id: thread.id,
          mode: modeToRequestMode(mode),
          attachment_path: firstAttachmentPath,
          surface: `editor:v2:${thread.id}`,
          surface_type: "editor",
          surface_id: `v2:${thread.id}`,
          surface_context: {
            ui_version: "dan-chat-v2",
            endpoint_contract: "chat_stream_v1",
            control_surface: "lightweight_frontend",
            ...attachmentSurfaceContext,
          },
        });
        const taskRunRef =
          taskRunRefFromResponse(response.task_run_ref) ??
          taskRunRefFromResponse(response.v2_control_plane?.task_run_ref);

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
            taskRunRef,
          });
          return;
        }

        if (taskRunRef) {
          applyMessageUpdate((previous) =>
            previous.map((message) =>
              message.id === assistantMessage.id
                ? { ...message, taskRunRef }
                : message,
            ),
          );
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
    [
      activeAgentRunning,
      activeTask,
      applyMessageUpdate,
      connectToAgentRun,
      connectToStream,
      ensureThread,
      finishStream,
      input,
      attachments,
      mode,
      persistMessages,
      streamState,
      upsertTaskSnapshot,
    ],
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

  const stopAgentRun = useCallback(async () => {
    const active = activeAgentRef.current;
    if (!active) return;
    setStatusText("Stopping Agent");
    try {
      const response = await postChatV2AgentRunCommand(active.runId, {
        command: "stop",
        task_id: active.taskId,
      });
      upsertTaskSnapshot(response.task ?? null);
      setAgentEvents((previous) => [...previous, response.event]);
    } catch {
      setStatusText("Agent stop failed");
    }
  }, [upsertTaskSnapshot]);

  const retryAgentRun = useCallback(
    async (task: ChatV2TaskSnapshot) => {
      const runId =
        typeof task.metadata?.active_run_id === "string"
          ? task.metadata.active_run_id
          : "";
      if (!runId || !activeThread) return;
      setStatusText("Retrying Agent");
      try {
        const commandResponse = await postChatV2AgentRunCommand(runId, {
          command: "retry",
          task_id: task.task_id,
        });
        upsertTaskSnapshot(commandResponse.task ?? null);
        setAgentEvents((previous) => [...previous, commandResponse.event]);
        const assistantId = findAssistantMessageForTask(
          messagesRef.current,
          runId,
          task.task_id,
        );
        connectToAgentRun(runId, task.task_id, activeThread, assistantId);
        const profile = activeProfile(agentProfileRef.current);
        const executeResponse = await executeChatV2AgentRun(runId, {
          background: true,
          profile_policy: {
            id: profile.id,
            label: profile.label,
            ...profile.policy,
          },
          metadata: {
            requested_from: "editor_v2_retry",
            agent_profile: profile.id,
          },
        });
        upsertTaskSnapshot(executeResponse.task ?? null);
      } catch (error) {
        setStatusText(error instanceof Error ? error.message : "Retry failed");
      }
    },
    [activeThread, connectToAgentRun, upsertTaskSnapshot],
  );

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    if (activeAgentRunning) {
      void sendMessage(
        undefined,
        event.metaKey || event.ctrlKey ? "continue_after_current" : "append",
      );
      return;
    }
    void sendMessage();
  };

  const busy =
    streamState === "starting" ||
    streamState === "streaming" ||
    streamState === "queued";
  const backendUnavailable = serverState === "offline";
  const composerDisabled = backendUnavailable || (busy && !activeAgentRunning);
  const activeProfileSummary = activeProfile(agentProfile);

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
              const branch = branchLabel(thread);
              const depth = branchDepth(thread, threadById);
              const parentThread = thread.parent_thread_id
                ? threadById[thread.parent_thread_id]
                : null;
              const summaryTasks = threadTaskMap[thread.id] ?? [];
              const runningTask = summaryTasks.find((item) => !isTaskTerminal(item));
              const latestTask = runningTask ?? summaryTasks[0] ?? null;
              const latestArtifact = latestTask?.latest_artifact_refs?.[0];
              return (
                <div
                  key={`${thread.workflow_id}:${thread.id}`}
                  className={`group mb-1 flex w-full items-start gap-2 rounded-lg border transition ${
                    active
                      ? branch
                        ? "border-amber-300 bg-white shadow-sm dark:border-amber-700 dark:bg-[#1b231d]"
                        : "border-transparent bg-white shadow-sm dark:bg-[#1b231d]"
                      : branch
                        ? "border-amber-200/70 hover:bg-white/70 dark:border-amber-800/50 dark:hover:bg-[#182019]"
                        : "border-transparent hover:bg-white/70 dark:hover:bg-[#182019]"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => void openThread(thread)}
                    className="flex min-w-0 flex-1 items-start gap-2 px-2 py-2 text-left"
                    style={{ paddingLeft: `${8 + depth * 14}px` }}
                  >
                    <span
                      className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                        branch ? "bg-amber-600" : "bg-emerald-600"
                      }`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex min-w-0 items-center gap-1.5">
                        {branch && <GitBranch size={12} className="shrink-0 text-amber-600" />}
                        <span className="block truncate text-sm font-medium">
                          {thread.title || "Untitled"}
                        </span>
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-[#6f796b] dark:text-[#a5afa1]">
                        {thread.workflow_id} · {formatTime(thread.updated_at)}
                      </span>
                      {branch && (
                        <span className="mt-1 block truncate text-[10px] font-medium uppercase tracking-[0.08em] text-amber-700 dark:text-amber-300">
                          {branch}
                          {parentThread
                            ? ` of ${parentThread.title || "parent"}`
                            : thread.parent_thread_id
                              ? ` of ${compactId(thread.parent_thread_id)}`
                              : ""}
                          {thread.branch_point_message_id
                            ? ` · ${compactId(thread.branch_point_message_id)}`
                            : ""}
                        </span>
                      )}
                      {latestTask && (
                        <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[10px] text-[#61705e] dark:text-[#aab6a7]">
                          <ListChecks size={11} className="shrink-0" />
                          <span className="truncate">
                            {latestTask.status}
                            {latestArtifact ? ` · ${artifactLabel(latestArtifact)}` : ""}
                          </span>
                        </span>
                      )}
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
            {mode === "agent" && (
              <div className="hidden items-center gap-1 rounded-lg border border-[#d8ded6] bg-white p-1 dark:border-[#293229] dark:bg-[#151b16] lg:flex">
                {AGENT_PROFILES.map((profile) => (
                  <button
                    key={profile.id}
                    type="button"
                    onClick={() => setAgentProfile(profile.id)}
                    className={`h-7 rounded-md px-2 text-xs font-medium transition ${
                      agentProfile === profile.id
                        ? "bg-emerald-700 text-white"
                        : "text-[#5f6b5c] hover:bg-[#f0f4ef] dark:text-[#aab5a7] dark:hover:bg-[#202820]"
                    }`}
                    title={profile.summary}
                  >
                    {profile.label}
                  </button>
                ))}
              </div>
            )}
          </header>

          {backendUnavailable && (
            <div className="border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">
              Backend is offline. Start `dan-serve` before sending Chat or Agent turns.
            </div>
          )}

          <AgentRunPanel
            task={activeTask}
            tasks={threadTasks}
            events={agentEvents}
            onStop={stopAgentRun}
            onRetry={retryAgentRun}
            onOpenPath={openLocalPath}
          />

          <section className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
            {messages.length === 0 ? (
              <EmptyState />
            ) : (
              <div className="mx-auto flex max-w-4xl flex-col gap-5">
                {messages.map((message) => (
                  <MessageRow
                    key={message.id}
                    message={message}
                    onBranch={branchFromMessage}
                    onOpenPath={openLocalPath}
                    branchDisabled={activeAgentRunning}
                  />
                ))}
              </div>
            )}
          </section>

          <footer className="shrink-0 border-t border-[#dfe5dd] bg-[#f7faf6] px-3 py-3 dark:border-[#263028] dark:bg-[#0f1310]">
            {attachments.length > 0 && (
              <div className="mx-auto max-w-4xl pb-2">
                <AttachmentChips
                  attachments={attachments}
                  onRemove={removeAttachment}
                />
              </div>
            )}
            <form
              onSubmit={(event) => void sendMessage(event)}
              className="mx-auto flex max-w-4xl items-end gap-2 rounded-2xl border border-[#cfd8cc] bg-white p-2 shadow-sm dark:border-[#293229] dark:bg-[#151b16]"
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => addAttachmentsFromInput(event.target.files)}
              />
              <button
                type="button"
                onClick={() => void addAttachmentsFromDialog()}
                disabled={composerDisabled}
                className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-[#d8ded6] text-[#52604f] transition hover:border-emerald-500 hover:bg-[#f0f7ef] disabled:cursor-not-allowed disabled:text-[#9aa594] dark:border-[#30382f] dark:text-[#c4d0c1] dark:hover:border-emerald-400 dark:hover:bg-[#1d251f]"
                title="Attach files"
              >
                <Paperclip size={17} />
              </button>
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                rows={1}
                className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-3 py-2.5 text-[15px] leading-6 text-[#151713] outline-none placeholder:text-[#7e897a] dark:text-[#f2f5ef] dark:placeholder:text-[#8d9989]"
                placeholder={
                  activeAgentRunning
                    ? "Enter appends to this Agent run; Cmd/Ctrl+Enter runs after"
                    : "Message DAN"
                }
                disabled={composerDisabled}
              />
              {activeAgentRunning ? (
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => void sendMessage(undefined, "append")}
                    disabled={!input.trim() && attachments.length === 0}
                    className="grid h-11 w-11 place-items-center rounded-xl bg-emerald-700 text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-[#bac5b7] dark:disabled:bg-[#384237]"
                    title="Append to current run at next checkpoint (Enter)"
                  >
                    <ArrowUp size={18} />
                  </button>
                  <button
                    type="button"
                    onClick={() => void sendMessage(undefined, "continue_after_current")}
                    disabled={!input.trim() && attachments.length === 0}
                    className="grid h-11 w-11 place-items-center rounded-xl border border-[#cfd8cc] text-[#30362f] transition hover:border-emerald-600 hover:bg-[#f0f7ef] disabled:cursor-not-allowed disabled:text-[#9aa594] dark:border-[#30382f] dark:text-[#dce4da] dark:hover:border-emerald-400 dark:hover:bg-[#1d251f] dark:disabled:text-[#566052]"
                    title="Run after current Agent run completes (Cmd/Ctrl+Enter)"
                  >
                    <Clock3 size={18} />
                  </button>
                </div>
              ) : busy ? (
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
                  disabled={!input.trim() && attachments.length === 0}
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-emerald-700 text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-[#bac5b7] dark:disabled:bg-[#384237]"
                  title="Send"
                >
                  <ArrowUp size={18} />
                </button>
              )}
            </form>
            {mode === "agent" && (
              <div className="mx-auto mt-2 max-w-4xl text-[11px] text-[#6c7868] dark:text-[#a4b0a0]">
                Agent profile: {activeProfileSummary.label} - {activeProfileSummary.summary}
              </div>
            )}
          </footer>
        </main>
      </div>
    </div>
  );
}
