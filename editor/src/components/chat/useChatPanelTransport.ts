import { useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import * as api from "../../lib/api";
import { progressAckText } from "../../lib/chatProgress";
import {
  buildAutoApplyPreviewMessage,
  parseRunIdFromStreamChannel,
  shouldAutoApplyMutation,
} from "../../lib/chatMutation";
import {
  getStreamDisconnectError,
  getStreamReconnectDelayMs,
  shouldReconnectStream,
} from "../../lib/chatStreamLifecycle";
import { formatInterruptedAssistantContent } from "../../lib/chatInterrupted";
import { safeTokenUsage } from "../../lib/chatMessagePersistence";
import {
  detachToBackground,
} from "../../lib/backgroundStreamRegistry";
import {
  mergeRunEventPayloads,
  recoverTerminalRunStateFromSnapshot,
} from "../../lib/runStreamRecovery";
import { describeLatestToolProgress } from "../../lib/toolCallPresentation";
import { upsertToolCallResult, upsertToolCallStart } from "../../lib/toolCallState";
import type { ChatMessage, ChatStreamEvent } from "../../types/chat";

type BackendDisconnectState = "unknown" | "unavailable" | "restarted";

type RunReference = {
  runId: string;
  scope: string;
  status: string;
};

type ToastType = "success" | "error" | "info" | "warning";

interface UseChatPanelTransportArgs {
  graphIdRef: MutableRefObject<string | null>;
  activeThreadIdRef: MutableRefObject<string | null>;
  activeAssistantIdRef: MutableRefObject<string | null>;
  activeChannelIdRef: MutableRefObject<string | null>;
  activeRunChannelIdRef: MutableRefObject<string | null>;
  messagesRef: MutableRefObject<ChatMessage[]>;
  runStreamHandoffRef: MutableRefObject<boolean>;
  wsRef: MutableRefObject<WebSocket | null>;
  setActiveChannelId: Dispatch<SetStateAction<string | null>>;
  setIsStreaming: Dispatch<SetStateAction<boolean>>;
  setIsRunStreaming: Dispatch<SetStateAction<boolean>>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setContextWindow: Dispatch<SetStateAction<number>>;
  setDetectedMode: Dispatch<SetStateAction<string | null>>;
  setStaleRevision: Dispatch<SetStateAction<boolean>>;
  persistThreadMessages: (
    workflowId: string | null,
    threadId: string | null | undefined,
    nextMessages: ChatMessage[],
    options?: { silent?: boolean; label?: string },
  ) => void;
  scheduleThreadPersist: (
    workflowId: string | null,
    threadId: string | null | undefined,
    nextMessages: ChatMessage[],
    delayMs?: number,
  ) => void;
  flushScheduledThreadPersist: (
    fallbackWorkflowId?: string | null,
    fallbackThreadId?: string | null,
    fallbackMessages?: ChatMessage[],
  ) => void;
  classifyBackendDisconnectState: (
    closeCode: number,
    hadTransportError: boolean,
  ) => Promise<BackendDisconnectState>;
  restoreThreadSnapshotFromServer: (
    workflowId: string | null,
    threadId: string | null,
  ) => Promise<boolean>;
  fetchThreads: (workflowId: string) => Promise<unknown> | void;
  loadGraph: (graphId: string) => Promise<void>;
  mutationConfirmMode: boolean;
  applyMutationForMessage: (
    message: ChatMessage,
    options?: { auto?: boolean },
  ) => Promise<void> | void;
  addToast: (toast: { type: ToastType; message: string }) => void;
}

function isTerminalRunEvent(eventType: string): boolean {
  return (
    eventType === "run_completed" ||
    eventType === "run_failed" ||
    eventType === "run_cancelled"
  );
}

export function useChatPanelTransport({
  graphIdRef,
  activeThreadIdRef,
  activeAssistantIdRef,
  activeChannelIdRef,
  activeRunChannelIdRef,
  messagesRef,
  runStreamHandoffRef,
  wsRef,
  setActiveChannelId,
  setIsStreaming,
  setIsRunStreaming,
  setMessages,
  setError,
  setContextWindow,
  setDetectedMode,
  setStaleRevision,
  persistThreadMessages,
  scheduleThreadPersist,
  flushScheduledThreadPersist,
  classifyBackendDisconnectState,
  restoreThreadSnapshotFromServer,
  fetchThreads,
  loadGraph,
  mutationConfirmMode,
  applyMutationForMessage,
  addToast,
}: UseChatPanelTransportArgs) {
  const setTrackedActiveChannelId = useCallback((nextChannelId: string | null) => {
    activeChannelIdRef.current = nextChannelId;
    setActiveChannelId(nextChannelId);
  }, [activeChannelIdRef, setActiveChannelId]);

  const detachCurrentStream = useCallback(() => {
    const ws = wsRef.current;
    const threadId = activeThreadIdRef.current;
    const assistantId = activeAssistantIdRef.current;
    if (!ws || ws.readyState >= WebSocket.CLOSING || !threadId || !assistantId) return;
    const workflowId = graphIdRef.current;
    if (!workflowId) return;
    detachToBackground({
      ws,
      channelId: activeChannelIdRef.current ?? "",
      threadId,
      workflowId,
      assistantMessageId: assistantId,
      messages: messagesRef.current,
    });
    wsRef.current = null;
    activeAssistantIdRef.current = null;
  }, [
    activeAssistantIdRef,
    activeChannelIdRef,
    activeThreadIdRef,
    graphIdRef,
    messagesRef,
    wsRef,
  ]);

  const attachRunStream = useCallback((
    initialStreamChannelId: string,
    assistantId: string,
    capturedGraphId: string | null,
    initialRunRef?: RunReference | null,
    options?: { reconnectCount?: number },
  ) => {
    const connectRunStream = (streamChannelId: string, reconnectCount = 0) => {
      activeRunChannelIdRef.current = streamChannelId;
      const runWs = new WebSocket(
        api.buildApiWebSocketUrl(`/api/chat/${streamChannelId}/events`),
      );
      wsRef.current = runWs;
      setTrackedActiveChannelId(null);
      setIsStreaming(false);
      setIsRunStreaming(true);
      let runWsClosedIntentionally = false;
      let runWsHadTransportError = false;
      let runWsHadTerminalEvent = false;

      runWs.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data);
          if (parsed.type !== "chat_run_event" || !parsed.run_event) {
            return;
          }
          const runEvent = parsed.run_event as {
            event_type: string;
            node_id?: string | null;
            summary: string;
            detail?: Record<string, unknown>;
          };
          const terminalRunEvent = isTerminalRunEvent(runEvent.event_type);
          setMessages((previous) => {
            const updated = previous.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content: message.content
                      ? `${message.content}\n${runEvent.summary}`
                      : runEvent.summary,
                    runEvents: [
                      ...(message.runEvents || []),
                      {
                        type: "run_event",
                        event_type: runEvent.event_type,
                        node_id: runEvent.node_id,
                        summary: runEvent.summary,
                        detail: runEvent.detail,
                      },
                    ],
                    runRef: message.runRef
                      ? {
                          ...message.runRef,
                          status:
                            runEvent.event_type === "run_completed"
                              ? "completed"
                              : runEvent.event_type === "run_failed"
                                ? "failed"
                                : runEvent.event_type === "run_cancelled"
                                  ? "cancelled"
                                  : message.runRef.status,
                        }
                      : initialRunRef ?? message.runRef,
                  }
                : message,
            );
            const threadId = activeThreadIdRef.current;
            if (terminalRunEvent) {
              persistThreadMessages(capturedGraphId, threadId, updated);
            } else {
              scheduleThreadPersist(capturedGraphId, threadId, updated);
            }
            return updated;
          });

          if (!terminalRunEvent) {
            return;
          }

          runWsHadTerminalEvent = true;
          runWsClosedIntentionally = true;
          activeRunChannelIdRef.current = null;
          setIsRunStreaming(false);
          runWs.close();
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
          const runId =
            initialRunRef?.runId ?? parseRunIdFromStreamChannel(streamChannelId);
          const recoveredScope = initialRunRef?.scope ?? "full";
          if (backendState !== "unavailable" && runId) {
            try {
              const [runInfo, eventResponse] = await Promise.all([
                api.getRun(runId),
                api.getRunEvents(runId).catch(() => ({
                  events: [] as Array<Record<string, unknown>>,
                  source: "memory" as const,
                })),
              ]);
              const recoveredRun = recoverTerminalRunStateFromSnapshot({
                runInfo,
                rawEvents: eventResponse.events,
                scope: recoveredScope,
              });
              if (recoveredRun) {
                setError(null);
                setMessages((previous) => {
                  const updated = previous.map((message) => {
                    if (message.id !== assistantId) return message;
                    const baseRunRef = message.runRef ?? initialRunRef ?? {
                      runId,
                      scope: recoveredScope,
                      status: recoveredRun.status,
                    };
                    return {
                      ...message,
                      runEvents: mergeRunEventPayloads(
                        message.runEvents,
                        recoveredRun.runEvents,
                      ),
                      runRef: {
                        ...baseRunRef,
                        status: recoveredRun.status,
                      },
                    };
                  });
                  persistThreadMessages(
                    capturedGraphId,
                    activeThreadIdRef.current,
                    updated,
                  );
                  return updated;
                });
                return;
              }
            } catch {
              /* fall back to generic disconnect banner */
            }
          }
          const disconnectError = getStreamDisconnectError({
            closedIntentionally: runWsClosedIntentionally,
            closeCode: event.code,
            hadTransportError: runWsHadTransportError,
            hadTerminalEvent: runWsHadTerminalEvent,
            streamLabel: "Run stream connection",
            recoveryHint:
              backendState === "unavailable"
                ? "Wait a few seconds for DAN to come back, then ask for status or rerun."
                : "Ask for status or rerun if needed.",
            backendState,
          });
          if (!disconnectError) {
            return;
          }
          setError(disconnectError);
          addToast({
            type: "error",
            message:
              backendState === "unavailable"
                ? "Local DAN backend unavailable"
                : "Run stream disconnected",
          });
        })();
      };
    };

    connectRunStream(initialStreamChannelId, options?.reconnectCount ?? 0);
  }, [
    activeRunChannelIdRef,
    activeThreadIdRef,
    addToast,
    classifyBackendDisconnectState,
    flushScheduledThreadPersist,
    messagesRef,
    persistThreadMessages,
    scheduleThreadPersist,
    setError,
    setIsRunStreaming,
    setIsStreaming,
    setMessages,
    setTrackedActiveChannelId,
    wsRef,
  ]);

  const connectChatStream = useCallback((
    initialChannelId: string,
    assistantId: string,
    capturedGraphId: string | null,
    threadId: string | null | undefined,
  ) => {
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
        setTrackedActiveChannelId(null);
        return;
      }
      if (!isReconnect) {
        seenStreamChannels.add(channelId);
      }
      setTrackedActiveChannelId(channelId);

      const ws = new WebSocket(
        api.buildApiWebSocketUrl(`/api/chat/${channelId}/events`),
      );
      wsRef.current = ws;
      let wsClosedIntentionally = false;
      let wsHadTransportError = false;
      let wsHadTerminalEvent = false;

      ws.onmessage = (event) => {
        try {
          const streamEvent: ChatStreamEvent = JSON.parse(event.data);

          if (
            ["node_started", "node_completed", "artifact_created"].includes(
              streamEvent.type,
            )
          ) {
            window.dispatchEvent(
              new CustomEvent("dan:engine-event-raw", { detail: streamEvent }),
            );
          }

          if (streamEvent.type === "chat_queued") {
            const nextChannel = (streamEvent.stream_channel_id ?? "").trim();
            const queuePosition =
              typeof streamEvent.queue_position === "number"
                ? streamEvent.queue_position
                : 0;

            setMessages((previous) => {
              const updated = previous.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content:
                        queuePosition > 1
                          ? `Queued behind ${queuePosition} earlier messages...`
                          : "Queued behind an earlier message...",
                    }
                  : message,
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

          if (streamEvent.type === "chat_token") {
            setMessages((previous) => {
              const updated = previous.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content:
                        streamEvent.accumulated ?? message.content + (streamEvent.delta ?? ""),
                      progressStatus: undefined,
                    }
                  : message,
              );
              scheduleThreadPersist(
                capturedGraphId,
                threadId ?? activeThreadIdRef.current,
                updated,
              );
              return updated;
            });
            return;
          }

          if (streamEvent.type === "chat_complete") {
            if (streamEvent.revision_mismatch) {
              setStaleRevision(true);
            }
            const isProgressAck = streamEvent.detected_mode === "progress_ack";
            const progressText = isProgressAck ? progressAckText(streamEvent) : null;
            if (streamEvent.context_window) {
              setContextWindow(streamEvent.context_window);
            }
            if (streamEvent.detected_mode && !isProgressAck) {
              setDetectedMode(streamEvent.detected_mode);
            }
            setMessages((previous) => {
              const updated = previous.map((message) =>
                message.id === assistantId
                  ? isProgressAck
                    ? {
                        ...message,
                        progressStatus: progressText || message.progressStatus,
                        progressFilePath: undefined,
                        tokenUsage:
                          safeTokenUsage(streamEvent.token_usage) ??
                          message.tokenUsage ??
                          null,
                      }
                    : {
                        ...message,
                        content: streamEvent.content || message.content,
                        progressStatus: undefined,
                        tokenUsage:
                          safeTokenUsage(streamEvent.token_usage) ??
                          message.tokenUsage ??
                          null,
                        estimatedCost:
                          typeof streamEvent.estimated_cost === "number"
                            ? streamEvent.estimated_cost
                            : message.estimatedCost ?? null,
                      }
                  : message,
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
              return;
            }
            wsHadTerminalEvent = true;
            wsClosedIntentionally = true;
            if (streamEvent.stream_channel_id) {
              runStreamHandoffRef.current = true;
              ws.close();
              const runId = parseRunIdFromStreamChannel(
                streamEvent.stream_channel_id,
              );
              attachRunStream(
                streamEvent.stream_channel_id,
                assistantId,
                capturedGraphId,
                runId ? { runId, scope: "full", status: "running" } : null,
              );
            } else {
              ws.close();
              setIsStreaming(false);
              setTrackedActiveChannelId(null);
              if (capturedGraphId) {
                void fetchThreads(capturedGraphId);
              }
            }
            return;
          }

          if (streamEvent.type === "chat_mutation") {
            if (streamEvent.revision_mismatch) {
              setStaleRevision(true);
            }
            if (streamEvent.context_window) {
              setContextWindow(streamEvent.context_window);
            }
            if (streamEvent.detected_mode) {
              setDetectedMode(streamEvent.detected_mode);
            }
            const nextMutationStatus = streamEvent.applied ? "applied" : "proposed";
            const nextMutationDryRunResult = streamEvent.dry_run_result ?? null;
            let nextMutationMessage: ChatMessage | null = null;
            setMessages((previous) => {
              const updated = previous.map((message) =>
                message.id === assistantId
                  ? (() => {
                      const built = buildAutoApplyPreviewMessage(
                        message,
                        streamEvent.mutation_plan ?? null,
                        nextMutationDryRunResult,
                        streamEvent.message_id ?? null,
                        nextMutationStatus,
                        streamEvent.content ?? message.content,
                        safeTokenUsage(streamEvent.token_usage) ??
                          message.tokenUsage ??
                          null,
                      );
                      nextMutationMessage = built;
                      return built;
                    })()
                  : message,
              );
              persistThreadMessages(
                capturedGraphId,
                threadId ?? activeThreadIdRef.current,
                updated,
              );
              return updated;
            });
            setIsStreaming(false);
            setTrackedActiveChannelId(null);
            wsHadTerminalEvent = true;
            wsClosedIntentionally = true;
            ws.close();
            if (capturedGraphId) {
              void fetchThreads(capturedGraphId);
            }
            if (
              nextMutationMessage &&
              shouldAutoApplyMutation(
                nextMutationDryRunResult,
                mutationConfirmMode,
                nextMutationStatus,
              )
            ) {
              const autoMessage = nextMutationMessage;
              setTimeout(() => {
                void applyMutationForMessage(autoMessage, { auto: true });
              }, 0);
            }
            return;
          }

          if (streamEvent.type === "chat_interrupted") {
            setMessages((previous) => {
              const updated = previous.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content: formatInterruptedAssistantContent(
                        message,
                        streamEvent.content,
                      ),
                      tokenUsage:
                        safeTokenUsage(streamEvent.token_usage) ??
                        message.tokenUsage ??
                        null,
                    }
                  : message,
              );
              persistThreadMessages(
                capturedGraphId,
                threadId ?? activeThreadIdRef.current,
                updated,
              );
              return updated;
            });
            setIsStreaming(false);
            setTrackedActiveChannelId(null);
            wsHadTerminalEvent = true;
            wsClosedIntentionally = true;
            ws.close();
            if (capturedGraphId) {
              void fetchThreads(capturedGraphId);
            }
            return;
          }

          if (streamEvent.type === "chat_tool_call_start") {
            setMessages((previous) => {
              const updated = previous.map((message) =>
                message.id === assistantId
                  ? (() => {
                      const nextToolCalls = upsertToolCallStart(
                        message.toolCalls,
                        {
                          id: streamEvent.tool_call_id!,
                          toolName: streamEvent.tool_name!,
                          argsPreview: streamEvent.args_preview ?? "",
                        },
                      );
                      const progress = describeLatestToolProgress(nextToolCalls);
                      return {
                        ...message,
                        progressStatus: progress?.text ?? message.progressStatus,
                        progressFilePath:
                          progress?.filePath ?? message.progressFilePath,
                        toolCalls: nextToolCalls,
                      };
                    })()
                  : message,
              );
              scheduleThreadPersist(
                capturedGraphId,
                threadId ?? activeThreadIdRef.current,
                updated,
              );
              return updated;
            });
            return;
          }

          if (streamEvent.type === "chat_tool_call_result") {
            setMessages((previous) => {
              const updated = previous.map((message) =>
                message.id === assistantId
                  ? (() => {
                      const nextToolCalls = upsertToolCallResult(
                        message.toolCalls,
                        {
                          id: streamEvent.tool_call_id!,
                          toolName: streamEvent.tool_name!,
                          argsPreview: streamEvent.args_preview,
                          status: streamEvent.status,
                          outputPreview: streamEvent.output_preview,
                          durationMs: streamEvent.duration_ms,
                        },
                      );
                      const progress = describeLatestToolProgress(nextToolCalls);
                      return {
                        ...message,
                        progressStatus: progress?.text ?? message.progressStatus,
                        progressFilePath:
                          progress?.filePath ?? message.progressFilePath,
                        toolCalls: nextToolCalls,
                      };
                    })()
                  : message,
              );
              scheduleThreadPersist(
                capturedGraphId,
                threadId ?? activeThreadIdRef.current,
                updated,
              );
              return updated;
            });
            return;
          }

          if (streamEvent.type === "chat_graph_created") {
            if (capturedGraphId) {
              void loadGraph(capturedGraphId);
            }
            return;
          }

          if (streamEvent.type === "chat_validation_result") {
            if (streamEvent.success === false && streamEvent.errors?.length) {
              const errorSummary = streamEvent.errors.slice(0, 3).join("; ");
              addToast({
                type: "error",
                message: `Validation failed: ${errorSummary}`,
              });
            }
            return;
          }

          if (streamEvent.type === "chat_file_attachment") {
            setMessages((previous) => {
              const updated = previous.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      attachments: (message.attachments || []).some(
                        (attachment) => attachment.path === (streamEvent.path ?? ""),
                      )
                        ? message.attachments
                        : [
                            ...(message.attachments || []),
                            {
                              path: streamEvent.path ?? "",
                              filename: streamEvent.filename ?? "File",
                              size: streamEvent.size,
                            },
                          ],
                    }
                  : message,
              );
              scheduleThreadPersist(
                capturedGraphId,
                threadId ?? activeThreadIdRef.current,
                updated,
              );
              return updated;
            });
            return;
          }

          if (streamEvent.type === "chat_injected_message") {
            const injectedUserMessage: ChatMessage = {
              id: streamEvent.inject_id ?? crypto.randomUUID(),
              role: "user",
              content: streamEvent.content ?? "",
              timestamp: Date.now(),
            };
            setMessages((previous) => {
              const assistantIndex = previous.findIndex(
                (message) => message.id === assistantId,
              );
              if (assistantIndex === -1) {
                return [...previous, injectedUserMessage];
              }
              const before = previous.slice(0, assistantIndex);
              const after = previous.slice(assistantIndex);
              return [...before, injectedUserMessage, ...after];
            });
            return;
          }

          if (streamEvent.type === "ping") {
            return;
          }

          if (streamEvent.type === "chat_notice") {
            const notice = (streamEvent.content ?? "").trim();
            if (!notice) {
              return;
            }
            setMessages((previous) => [
              ...previous,
              {
                id: crypto.randomUUID(),
                role: "system",
                content: notice,
                timestamp: Date.now(),
              },
            ]);
            return;
          }

          if (streamEvent.type === "chat_error") {
            if (streamEvent.error?.includes("revision_mismatch")) {
              setStaleRevision(true);
            }
            flushScheduledThreadPersist(
              capturedGraphId,
              threadId ?? activeThreadIdRef.current,
              messagesRef.current,
            );
            setError(streamEvent.error ?? "Unknown error");
            setIsStreaming(false);
            setTrackedActiveChannelId(null);
            wsHadTerminalEvent = true;
            wsClosedIntentionally = true;
            ws.close();
            if (capturedGraphId) {
              void fetchThreads(capturedGraphId);
            }
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
          setTrackedActiveChannelId(null);
          return;
        }
        setIsStreaming(false);
        setTrackedActiveChannelId(null);
        void (async () => {
          const activeThreadForRecovery = threadId ?? activeThreadIdRef.current ?? null;
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
            hadTerminalEvent: wsHadTerminalEvent,
            backendState,
            restoredSnapshot,
            recoveryHint:
              backendState === "unavailable"
                ? "Wait a few seconds for DAN to come back, then click Retry."
                : "Click Retry to continue from the latest saved thread state.",
          });
          if (disconnectError) {
            setError(disconnectError);
            addToast({
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

    connectToChatStream(initialChannelId);
  }, [
    activeChannelIdRef,
    activeThreadIdRef,
    addToast,
    applyMutationForMessage,
    attachRunStream,
    classifyBackendDisconnectState,
    fetchThreads,
    flushScheduledThreadPersist,
    loadGraph,
    messagesRef,
    mutationConfirmMode,
    persistThreadMessages,
    restoreThreadSnapshotFromServer,
    runStreamHandoffRef,
    scheduleThreadPersist,
    setContextWindow,
    setDetectedMode,
    setError,
    setIsStreaming,
    setMessages,
    setStaleRevision,
    setTrackedActiveChannelId,
    wsRef,
  ]);

  const handleStop = useCallback(async () => {
    const channelId = activeChannelIdRef.current;
    if (!channelId) return;
    try {
      await api.stopChatStream(channelId);
    } catch (error) {
      console.warn("Failed to stop stream:", error);
    }
  }, [activeChannelIdRef]);

  return {
    attachRunStream,
    connectChatStream,
    detachCurrentStream,
    handleStop,
    setTrackedActiveChannelId,
  };
}
