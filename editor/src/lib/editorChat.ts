import type { ChatStreamEvent, RunEventPayload } from "../types/chat";
import type { ChatMessageResponse } from "./api";
import { connectChatStream } from "./api";
import { getStreamReconnectDelayMs, shouldReconnectStream } from "./chatStreamLifecycle";
import { isElectron, nativeFs } from "./electronBridge";

export type EditorChatMode =
  | "ask"
  | "agent"
  | "plan"
  | "debug"
  | "auto"
  | "conversation";

export interface ComposerAttachmentDraft {
  id: string;
  kind: "file" | "figure";
  name: string;
  path?: string;
  size?: number;
  mimeType?: string;
  caption?: string;
  source?: string;
  dataUrl?: string;
  file?: File;
}

export interface ChatAttachmentRecord {
  filename: string;
  path?: string;
  size?: number;
  mimeType?: string;
  kind?: ComposerAttachmentDraft["kind"];
  caption?: string;
  source?: string;
}

function mimeTypeFromDataUrl(dataUrl?: string): string | undefined {
  if (typeof dataUrl !== "string" || !dataUrl.startsWith("data:")) return undefined;
  const match = /^data:([^;,]+)/.exec(dataUrl);
  return match?.[1] || undefined;
}

function defaultExtensionForMimeType(mimeType?: string): string {
  switch ((mimeType || "").toLowerCase()) {
    case "image/png":
      return ".png";
    case "image/jpeg":
      return ".jpg";
    case "image/webp":
      return ".webp";
    case "image/gif":
      return ".gif";
    case "image/svg+xml":
      return ".svg";
    case "application/pdf":
      return ".pdf";
    case "application/json":
      return ".json";
    case "text/plain":
      return ".txt";
    default:
      return "";
  }
}

export function resolveAttachmentName(name?: string, mimeType?: string): string {
  const trimmed = name?.trim();
  if (trimmed) return trimmed;

  const ext = defaultExtensionForMimeType(mimeType);
  if (mimeType?.startsWith("image/")) return `Pasted Image${ext}`;
  if (mimeType === "application/pdf") return `Pasted PDF${ext}`;
  return `Attachment${ext}`;
}

export interface StartEditorChatOptions {
  message: string;
  history?: Array<{ role: "user" | "assistant"; content: string }>;
  threadId?: string | null;
  mode?: EditorChatMode;
  scope?: string;
  surfaceContext?: Record<string, unknown>;
  attachments?: ComposerAttachmentDraft[];
  mentions?: Array<{ type: string; identifier: string }>;
  signal?: AbortSignal;
}

export interface StreamEditorChatHandlers {
  onQueued?: (position: number) => void;
  onProgress?: (content: string, delta: string) => void;
  onComplete?: (content: string, event: ChatStreamEvent | ChatMessageResponse) => void;
  onError?: (message: string) => void;
  onRunEvent?: (event: RunEventPayload) => void;
  onInjectedMessage?: (message: { id: string; content: string }) => void;
  onFileAttachment?: (attachment: { path: string; filename: string; size?: number }) => void;
  onToolCallStart?: (toolCall: {
    id: string;
    toolName: string;
    argsPreview: string;
  }) => void;
  onToolCallResult?: (toolCall: {
    id: string;
    toolName: string;
    argsPreview: string;
    status: string;
    outputPreview?: string;
    durationMs?: number;
  }) => void;
  onChannelChange?: (channelId: string | null) => void;
  onCloseWithoutTerminalEvent?: () => void;
}

export interface EditorChatHistoryEntry {
  role: "user" | "assistant";
  content: string;
}

export function sanitizeChatHistory(
  history: Array<EditorChatHistoryEntry>,
): EditorChatHistoryEntry[] {
  return history.flatMap((entry) => {
    const content = typeof entry.content === "string" ? entry.content : String(entry.content ?? "");
    return content.trim()
      ? [{ role: entry.role, content }]
      : [];
  });
}

const TEXT_EXTENSIONS = new Set([
  ".c",
  ".cc",
  ".cfg",
  ".cpp",
  ".css",
  ".csv",
  ".go",
  ".h",
  ".hpp",
  ".html",
  ".java",
  ".js",
  ".json",
  ".jsx",
  ".md",
  ".mjs",
  ".py",
  ".rb",
  ".rs",
  ".sh",
  ".sql",
  ".svg",
  ".toml",
  ".ts",
  ".tsx",
  ".txt",
  ".xml",
  ".yaml",
  ".yml",
]);

const MAX_ATTACHMENTS = 6;
const MAX_ATTACHMENT_CHARS = 12000;

function truncateText(value: string, maxChars = MAX_ATTACHMENT_CHARS): string {
  if (value.length <= maxChars) return value;
  return `${value.slice(0, maxChars)}\n\n...[truncated]`;
}

function fileExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

function isLikelyTextAttachment(name: string, mimeType?: string): boolean {
  if (mimeType?.startsWith("text/")) return true;
  if (mimeType === "application/json" || mimeType === "image/svg+xml") return true;
  return TEXT_EXTENSIONS.has(fileExtension(name));
}

export function fileToAttachmentDraft(file: File): ComposerAttachmentDraft {
  const maybePath = (file as File & { path?: string }).path;
  const resolvedName = resolveAttachmentName(file.name, file.type || undefined);
  return {
    id: crypto.randomUUID(),
    kind: "file",
    name: resolvedName,
    path: typeof maybePath === "string" && maybePath.trim() ? maybePath : undefined,
    size: file.size,
    mimeType: file.type || undefined,
    file,
  };
}

export function cloneAttachmentDraft(
  attachment: ComposerAttachmentDraft,
): ComposerAttachmentDraft {
  return { ...attachment };
}

export function composerDraftToChatAttachment(
  attachment: ComposerAttachmentDraft,
): ChatAttachmentRecord {
  return {
    filename: resolveAttachmentName(attachment.name, attachment.mimeType),
    path: attachment.path,
    size: attachment.size,
    mimeType: attachment.mimeType,
    kind: attachment.kind,
    caption: attachment.caption,
    source: attachment.source,
  };
}

export function chatAttachmentToComposerDraft(
  attachment: ChatAttachmentRecord,
): ComposerAttachmentDraft {
  return {
    id: crypto.randomUUID(),
    kind: attachment.kind ?? "file",
    name: resolveAttachmentName(attachment.filename, attachment.mimeType),
    path: attachment.path,
    size: attachment.size,
    mimeType: attachment.mimeType,
    caption: attachment.caption,
    source: attachment.source,
  };
}

export function figureToAttachmentDraft(detail: {
  title?: string;
  caption?: string;
  src?: string;
  source?: string;
  path?: string;
}): ComposerAttachmentDraft {
  return {
    id: crypto.randomUUID(),
    kind: "figure",
    name: detail.title?.trim() || "Figure",
    caption: detail.caption?.trim() || undefined,
    mimeType: mimeTypeFromDataUrl(detail.src),
    source: detail.source?.trim() || "research",
    path: detail.path?.trim() || undefined,
    dataUrl: detail.src,
  };
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read attachment"));
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
        return;
      }
      reject(new Error("Unexpected attachment read result"));
    };
    reader.readAsDataURL(file);
  });
}

async function persistAttachmentPath(
  attachment: ComposerAttachmentDraft,
): Promise<ComposerAttachmentDraft> {
  if (attachment.path || !isElectron()) return attachment;

  let dataUrl = attachment.dataUrl;
  if (!dataUrl && attachment.file) {
    try {
      dataUrl = await fileToDataUrl(attachment.file);
    } catch (error) {
      console.warn("Failed to read attachment for temp persistence:", error);
      return attachment;
    }
  }

  if (!dataUrl) return attachment;

  try {
    const persistedPath = await nativeFs.writeTempAttachment({
      name: attachment.name,
      mimeType: attachment.mimeType ?? mimeTypeFromDataUrl(dataUrl),
      dataUrl,
    });
    if (!persistedPath) return attachment;
    return {
      ...attachment,
      path: persistedPath,
      dataUrl,
      mimeType: attachment.mimeType ?? mimeTypeFromDataUrl(dataUrl),
    };
  } catch (error) {
    console.warn("Failed to persist attachment to local cache:", error);
    return attachment;
  }
}

export async function normalizeAttachmentDrafts(
  attachments: ComposerAttachmentDraft[],
): Promise<ComposerAttachmentDraft[]> {
  return Promise.all(attachments.map((attachment) => persistAttachmentPath(attachment)));
}

function recommendedAttachmentTool(
  attachment: ComposerAttachmentDraft,
): string | null {
  const target = (attachment.path || attachment.name).toLowerCase();
  const ext = fileExtension(target);
  if (
    attachment.kind === "figure" ||
    [".png", ".jpg", ".jpeg", ".webp", ".gif"].includes(ext)
  ) {
    return "image_describe";
  }
  if (ext === ".pdf") return "pdf_read";
  if (ext === ".csv") return "csv_read";
  if (isLikelyTextAttachment(attachment.name, attachment.mimeType)) return "file_read";
  return "file_read";
}

async function readTextAttachmentPreview(
  attachment: ComposerAttachmentDraft,
): Promise<string> {
  if (attachment.kind === "figure") {
    if (
      typeof attachment.dataUrl === "string" &&
      attachment.dataUrl.startsWith("data:image/svg+xml,")
    ) {
      try {
        return truncateText(
          decodeURIComponent(attachment.dataUrl.slice("data:image/svg+xml,".length)),
        );
      } catch {
        return "";
      }
    }
    return "";
  }

  if (!isLikelyTextAttachment(attachment.name, attachment.mimeType)) return "";

  if (attachment.path) {
    const content = await nativeFs.readFile(attachment.path);
    if (typeof content === "string" && content.trim()) {
      return truncateText(content);
    }
  }

  if (attachment.file) {
    try {
      const text = await attachment.file.text();
      if (text.trim()) return truncateText(text);
    } catch {
      return "";
    }
  }

  return "";
}

export async function buildAttachmentContext(
  attachments: ComposerAttachmentDraft[],
): Promise<string> {
  if (attachments.length === 0) return "";

  const deduped = attachments.filter(
    (attachment, index, all) =>
      all.findIndex((candidate) => {
        const leftKey = `${attachment.kind}:${attachment.path ?? attachment.name}:${attachment.caption ?? ""}`;
        const rightKey = `${candidate.kind}:${candidate.path ?? candidate.name}:${candidate.caption ?? ""}`;
        return leftKey === rightKey;
      }) === index,
  );

  const sections = await Promise.all(
    deduped.slice(0, MAX_ATTACHMENTS).map(async (attachment) => {
      if (attachment.kind === "figure") {
        const lines = [
          `[Appended Figure] ${attachment.name}`,
          attachment.path ? `Path: ${attachment.path}` : null,
          attachment.caption ? `Caption: ${attachment.caption}` : null,
          attachment.source ? `Source: ${attachment.source}` : null,
          attachment.path
            ? "If the user asks what this figure shows, inspect it instead of claiming no image was attached."
            : "This figure was appended without a filesystem path; use the metadata/caption below if no image tool path is available.",
        ].filter(Boolean);
        const tool = recommendedAttachmentTool(attachment);
        if (tool && attachment.path) {
          lines.push(`Suggested tool: ${tool}`);
        }
        const preview = await readTextAttachmentPreview(attachment);
        if (preview) {
          lines.push("", "[Figure Source Preview]", preview);
        }
        return lines.join("\n");
      }

      const lines = [
        `[Appended File] ${attachment.name}`,
        attachment.path ? `Path: ${attachment.path}` : null,
        typeof attachment.size === "number" ? `Size: ${attachment.size} bytes` : null,
      ].filter(Boolean);
      const tool = recommendedAttachmentTool(attachment);
      if (tool && attachment.path) {
        lines.push(`Suggested tool: ${tool}`);
      }
      const preview = await readTextAttachmentPreview(attachment);
      if (preview) {
        lines.push("", "[File Content Preview]", preview);
      } else {
        lines.push(
          "",
          attachment.path
            ? "[Binary or non-text attachment: inspect via the path/tool above rather than saying no file was attached]"
            : "[Binary or non-text attachment: content preview unavailable and no filesystem path was exposed]",
        );
      }
      return lines.join("\n");
    }),
  );

  if (sections.length === 0) return "";
  return ["[Appended Attachments]", ...sections].join("\n\n");
}

async function postChatMessage(
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<ChatMessageResponse> {
  const response = await fetch("/api/chat/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    throw new Error(await response.text().catch(() => `Request failed: ${response.status}`));
  }
  return response.json() as Promise<ChatMessageResponse>;
}

export async function startEditorChat(
  options: StartEditorChatOptions,
): Promise<{ threadId: string; response: ChatMessageResponse }> {
  const threadId = options.threadId?.trim() || crypto.randomUUID();
  const message = options.message.trim();
  const normalizedAttachments = await normalizeAttachmentDrafts(options.attachments ?? []);
  const attachmentContext = await buildAttachmentContext(normalizedAttachments);
  const fullMessage = attachmentContext
    ? message
      ? `${message}\n\n${attachmentContext}`
      : `Please use the appended attachments as context.\n\n${attachmentContext}`
    : message;

  const scope = (options.scope ?? "editor").trim() || "editor";
  const surfaceId = `${scope}:${threadId}`;
  const firstAttachmentPath =
    normalizedAttachments.find((attachment) => typeof attachment.path === "string" && attachment.path)?.path ??
    null;
  const response = await postChatMessage({
    workflow_id: "_scratch",
    message: fullMessage,
    history: sanitizeChatHistory(options.history ?? []),
    thread_id: threadId,
    mode: options.mode ?? "ask",
    attachment_path: firstAttachmentPath,
    mentions: options.mentions ?? [],
    surface: `editor:${surfaceId}`,
    surface_type: "editor",
    surface_id: surfaceId,
    session_id: threadId,
    surface_context: {
      ...(options.surfaceContext ?? {}),
      appended_attachments: normalizedAttachments.map((attachment) => ({
        kind: attachment.kind,
        name: attachment.name,
        path: attachment.path ?? null,
        caption: attachment.caption ?? null,
        source: attachment.source ?? null,
      })),
    },
  }, options.signal);
  return { threadId, response };
}

export function streamEditorChatResponse(
  response: ChatMessageResponse,
  handlers: StreamEditorChatHandlers,
): WebSocket | null {
  if (response.type === "run_error") {
    const errorMessage =
      (response.error as { message?: string } | undefined)?.message ??
      "Editor chat request failed";
    handlers.onError?.(errorMessage);
    return null;
  }

  if (!response.stream_channel_id) {
    const runMessage =
      response.type === "run_started"
        ? `Started ${response.scope ?? "full"} run.`
        : "";
    handlers.onComplete?.(runMessage, response);
    return null;
  }

  let accumulated = "";
  let currentWs: WebSocket | null = null;

  type StreamConnectionState = {
    channelId: string;
    suppressClose: boolean;
    pendingClose: boolean;
    terminalEventSeen: boolean;
    hadTransportError: boolean;
    reconnectCount: number;
  };

  let currentState: StreamConnectionState | null = null;

  const closeConnection = (state: StreamConnectionState) => {
    if (currentState === state && currentWs) {
      currentWs.close();
      return;
    }
    state.pendingClose = true;
  };

  const connectToChannel = (channelId: string, reconnectCount = 0) => {
    const state: StreamConnectionState = {
      channelId,
      suppressClose: false,
      pendingClose: false,
      terminalEventSeen: false,
      hadTransportError: false,
      reconnectCount,
    };
    currentState = state;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    const clearIdleTimer = () => {
      if (idleTimer !== null) {
        clearTimeout(idleTimer);
        idleTimer = null;
      }
    };
    const scheduleIdleReconnect = () => {
      clearIdleTimer();
      if (state.terminalEventSeen || state.suppressClose) return;
      idleTimer = setTimeout(() => {
        if (currentState !== state || state.terminalEventSeen || state.suppressClose) return;
        state.hadTransportError = true;
        closeConnection(state);
      }, 12_000);
    };
    scheduleIdleReconnect();

    const ws = connectChatStream(
      channelId,
      (rawEvent) => {
        if (currentState !== state && !state.pendingClose) return;
        scheduleIdleReconnect();

        const event = rawEvent as unknown as ChatStreamEvent;

        if (event.type === "chat_queued") {
          handlers.onQueued?.(
            typeof event.queue_position === "number" ? event.queue_position : 0,
          );
          const nextChannel = (event.stream_channel_id ?? "").trim();
          if (nextChannel && nextChannel !== state.channelId) {
            state.suppressClose = true;
            handlers.onChannelChange?.(
              nextChannel.startsWith("chat-") ? nextChannel : null,
            );
            closeConnection(state);
            connectToChannel(nextChannel);
          }
          return;
        }

        if (event.type === "chat_token") {
          const nextAccumulated =
            typeof event.accumulated === "string"
              ? event.accumulated
              : accumulated + (event.delta ?? "");
          const delta =
            typeof event.delta === "string"
              ? event.delta
              : nextAccumulated.slice(accumulated.length);
          accumulated = nextAccumulated;
          handlers.onProgress?.(accumulated, delta);
          return;
        }

        if (event.type === "chat_tool_call_start") {
          handlers.onToolCallStart?.({
            id: event.tool_call_id ?? crypto.randomUUID(),
            toolName: event.tool_name ?? "",
            argsPreview: event.args_preview ?? "",
          });
          return;
        }

        if (event.type === "chat_tool_call_result") {
          handlers.onToolCallResult?.({
            id: event.tool_call_id ?? crypto.randomUUID(),
            toolName: event.tool_name ?? "",
            argsPreview: event.args_preview ?? "",
            status: event.status ?? "success",
            outputPreview: event.output_preview,
            durationMs: event.duration_ms,
          });
          return;
        }

        if (event.type === "chat_file_attachment") {
          handlers.onFileAttachment?.({
            path: event.path ?? "",
            filename: event.filename ?? "File",
            size: event.size,
          });
          return;
        }

        if (event.type === "chat_injected_message") {
          handlers.onInjectedMessage?.({
            id: event.inject_id ?? crypto.randomUUID(),
            content: event.content ?? "",
          });
          return;
        }

        if (event.type === "chat_run_event" && event.run_event) {
          handlers.onRunEvent?.(event.run_event);
          if (
            event.run_event.event_type === "run_completed" ||
            event.run_event.event_type === "run_failed" ||
            event.run_event.event_type === "run_cancelled"
          ) {
            state.terminalEventSeen = true;
          }
          return;
        }

        if (event.type === "chat_error") {
          state.terminalEventSeen = true;
          handlers.onError?.(event.error ?? "Editor chat stream failed");
          closeConnection(state);
          return;
        }

        if (event.type === "chat_interrupted") {
          state.terminalEventSeen = true;
          if (typeof event.content === "string") {
            accumulated = event.content;
          }
          handlers.onComplete?.(accumulated, event);
          closeConnection(state);
          return;
        }

        if (event.type === "chat_mutation") {
          state.terminalEventSeen = true;
          if (typeof event.content === "string" && event.content) {
            accumulated = event.content;
          }
          handlers.onComplete?.(accumulated, event);
          closeConnection(state);
          return;
        }

        if (event.type === "chat_complete") {
          if (event.detected_mode === "progress_ack") return;
          state.terminalEventSeen = true;
          if (typeof event.content === "string" && event.content) {
            accumulated = event.content;
          }
          handlers.onComplete?.(accumulated, event);
          const nextChannel = (event.stream_channel_id ?? "").trim();
          if (nextChannel && nextChannel !== state.channelId) {
            state.suppressClose = true;
            handlers.onChannelChange?.(
              nextChannel.startsWith("chat-") ? nextChannel : null,
            );
            closeConnection(state);
            connectToChannel(nextChannel);
            return;
          }
          closeConnection(state);
        }
      },
      (closeEvent) => {
        clearIdleTimer();
        const closeCode = closeEvent?.code ?? 1005;
        if (state.suppressClose) return;
        if (
          shouldReconnectStream({
            closedIntentionally: false,
            activeChannelId:
              currentState === state
                ? state.channelId
                : currentState?.channelId ?? null,
            channelId: state.channelId,
            reconnectCount: state.reconnectCount,
            closeCode,
            hadTransportError: state.hadTransportError,
            hadTerminalEvent: state.terminalEventSeen,
          })
        ) {
          setTimeout(() => {
            if (currentState === state) {
              connectToChannel(state.channelId, state.reconnectCount + 1);
            }
          }, getStreamReconnectDelayMs(state.reconnectCount));
          return;
        }
        if (!state.terminalEventSeen) {
          handlers.onCloseWithoutTerminalEvent?.();
        }
      },
      () => {
        clearIdleTimer();
        state.hadTransportError = true;
        closeConnection(state);
      },
    );

    if (currentState === state) {
      currentWs = ws;
    }
    if (state.pendingClose) {
      ws.close();
    }
  };

  connectToChannel(response.stream_channel_id);

  return {
    close: () => {
      if (currentState) {
        currentState.suppressClose = true;
      }
      currentWs?.close();
    },
  } as unknown as WebSocket;
}

export async function requestEditorChatText(
  options: StartEditorChatOptions,
): Promise<string> {
  const { response } = await startEditorChat(options);

  if (response.type === "run_error") {
    throw new Error(
      (response.error as { message?: string } | undefined)?.message ??
        "Editor chat request failed",
    );
  }

  if (!response.stream_channel_id) {
    return response.type === "run_started"
      ? `Started ${response.scope ?? "full"} run.`
      : "";
  }

  return new Promise<string>((resolve, reject) => {
    let settled = false;
    let accumulated = "";
    const ws = streamEditorChatResponse(response, {
      onProgress: (content) => {
        accumulated = content;
      },
      onComplete: (content) => {
        if (settled) return;
        settled = true;
        resolve(content || accumulated);
      },
      onError: (message) => {
        if (settled) return;
        settled = true;
        reject(new Error(message));
      },
      onRunEvent: (event) => {
        if (
          settled ||
          (event.event_type !== "run_completed" &&
            event.event_type !== "run_failed" &&
            event.event_type !== "run_cancelled")
        ) {
          return;
        }
        settled = true;
        resolve(accumulated || event.summary || "");
      },
      onCloseWithoutTerminalEvent: () => {
        if (settled) return;
        settled = true;
        resolve(accumulated);
      },
    });

    if (!ws && !settled) {
      settled = true;
      resolve("");
    }
  });
}
