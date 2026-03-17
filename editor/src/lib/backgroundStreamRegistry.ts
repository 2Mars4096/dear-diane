import type { ChatMessage, ChatStreamEvent, ToolCallInfo } from "../types/chat";
import { safeTokenUsage, toBackendMessage } from "./chatMessagePersistence";
import * as api from "./api";

interface BackgroundStream {
  ws: WebSocket;
  channelId: string;
  threadId: string;
  workflowId: string;
  assistantMessageId: string;
  messages: ChatMessage[];
  onComplete?: () => void;
  hadTerminalEvent?: boolean;
  closedIntentionally?: boolean;
}

const _streams = new Map<string, BackgroundStream>();
const _listeners = new Set<() => void>();

function notify() {
  _listeners.forEach((fn) => fn());
}

function saveMessages(stream: BackgroundStream) {
  api
    .updateChatThread(stream.workflowId, stream.threadId, {
      messages: stream.messages.map(toBackendMessage),
    })
    .catch(() => {});
}

function markInterrupted(stream: BackgroundStream) {
  const note = "Final assistant text was not captured because the live stream disconnected before completion. Review the tool output below or retry.";
  const tail = "*[stream disconnected before final response]*";
  stream.messages = stream.messages.map((message) =>
    message.id === stream.assistantMessageId
      ? {
          ...message,
          content: message.content.trim()
            ? `${message.content}\n\n${tail}`
            : note,
          progressStatus: undefined,
        }
      : message,
  );
}

function finalizeDetachedStream(stream: BackgroundStream) {
  if (!stream.closedIntentionally && !stream.hadTerminalEvent) {
    markInterrupted(stream);
  }
  stream.closedIntentionally = true;
  saveMessages(stream);
  cleanup(stream.threadId);
}

function handleEvent(stream: BackgroundStream, evt: ChatStreamEvent) {
  const aid = stream.assistantMessageId;

  if (evt.type === "chat_token") {
    stream.messages = stream.messages.map((m) =>
      m.id === aid
        ? { ...m, content: evt.accumulated ?? m.content + (evt.delta ?? "") }
        : m,
    );
  } else if (evt.type === "chat_complete") {
    const isProgressAck = evt.detected_mode === "progress_ack";
    stream.messages = stream.messages.map((m) =>
      m.id === aid
        ? isProgressAck
          ? { ...m, tokenUsage: safeTokenUsage(evt.token_usage) ?? m.tokenUsage ?? null }
          : {
              ...m,
              content: evt.content || m.content,
              progressStatus: undefined,
              tokenUsage: safeTokenUsage(evt.token_usage) ?? m.tokenUsage ?? null,
            }
        : m,
    );
    if (!isProgressAck) {
      stream.hadTerminalEvent = true;
      saveMessages(stream);
      cleanup(stream.threadId);
      return;
    }
  } else if (evt.type === "chat_mutation") {
    stream.messages = stream.messages.map((m) =>
      m.id === aid
        ? {
            ...m,
            content: evt.content || m.content,
            mutationPlan: evt.mutation_plan ?? null,
            dryRunResult: evt.dry_run_result ?? null,
            mutationId: evt.message_id ?? m.mutationId ?? null,
            mutationStatus: "proposed",
            progressStatus: undefined,
            tokenUsage: safeTokenUsage(evt.token_usage) ?? m.tokenUsage ?? null,
          }
        : m,
    );
    stream.hadTerminalEvent = true;
    saveMessages(stream);
    cleanup(stream.threadId);
    return;
  } else if (evt.type === "chat_tool_call_start") {
    stream.messages = stream.messages.map((m) =>
      m.id === aid
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
    );
  } else if (evt.type === "chat_tool_call_result") {
    stream.messages = stream.messages.map((m) =>
      m.id === aid
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
    );
  } else if (evt.type === "chat_file_attachment") {
    stream.messages = stream.messages.map((m) =>
      m.id === aid
        ? {
            ...m,
            attachments: (m.attachments || []).some(
              (attachment) =>
                attachment.path === (evt.path || undefined) &&
                attachment.filename === (evt.filename ?? "File"),
            )
              ? (m.attachments || [])
              : [
                  ...(m.attachments || []),
                  {
                    path: evt.path || undefined,
                    filename: evt.filename ?? "File",
                    size: evt.size,
                  },
                ],
          }
        : m,
    );
  } else if (
    evt.type === "chat_error" ||
    evt.type === "chat_interrupted"
  ) {
    stream.hadTerminalEvent = true;
    if (evt.type === "chat_interrupted") {
      stream.messages = stream.messages.map((m) =>
        m.id === aid
          ? { ...m, content: (evt.content || m.content) + "\n\n*[generation stopped]*" }
          : m,
      );
    } else {
      stream.messages = stream.messages.map((m) =>
        m.id === aid
          ? {
              ...m,
              content: m.content.trim()
                ? `${m.content}\n\n*[generation failed: ${evt.error ?? "unknown error"}]*`
                : `Generation failed: ${evt.error ?? "Unknown error"}`,
              progressStatus: undefined,
            }
          : m,
      );
    }
    saveMessages(stream);
    cleanup(stream.threadId);
    return;
  }
}

function cleanup(threadId: string) {
  const stream = _streams.get(threadId);
  if (!stream) return;
  stream.onComplete?.();
  _streams.delete(threadId);
  notify();
}

export function detachToBackground(opts: {
  ws: WebSocket;
  channelId: string;
  threadId: string;
  workflowId: string;
  assistantMessageId: string;
  messages: ChatMessage[];
}) {
  const existing = _streams.get(opts.threadId);
  if (existing) {
    existing.closedIntentionally = true;
    try { existing.ws.close(); } catch {}
    _streams.delete(opts.threadId);
  }

  const stream: BackgroundStream = {
    ws: opts.ws,
    channelId: opts.channelId,
    threadId: opts.threadId,
    workflowId: opts.workflowId,
    assistantMessageId: opts.assistantMessageId,
    messages: [...opts.messages],
  };

  opts.ws.onmessage = (event) => {
    try {
      const evt = JSON.parse(event.data) as ChatStreamEvent;
      if (evt.type === "ping") return;
      handleEvent(stream, evt);
    } catch {}
  };
  opts.ws.onclose = () => {
    finalizeDetachedStream(stream);
  };
  opts.ws.onerror = () => {
    finalizeDetachedStream(stream);
  };

  _streams.set(opts.threadId, stream);
  notify();
}

export function isStreamingInBackground(threadId: string): boolean {
  return _streams.has(threadId);
}

export function getBackgroundThreadIds(): Set<string> {
  return new Set(_streams.keys());
}

export function subscribe(listener: () => void): () => void {
  _listeners.add(listener);
  return () => _listeners.delete(listener);
}

export function cancelBackground(threadId: string) {
  const stream = _streams.get(threadId);
  if (!stream) return;
  stream.closedIntentionally = true;
  try { stream.ws.close(); } catch {}
  saveMessages(stream);
  cleanup(threadId);
}

export function shutdownAll() {
  for (const [threadId, stream] of _streams) {
    stream.closedIntentionally = true;
    try { stream.ws.close(); } catch {}
    saveMessages(stream);
    _streams.delete(threadId);
  }
  notify();
}
