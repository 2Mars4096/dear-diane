import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import type { AppMode } from "../../store/useAppStore";
import { useCodeStore } from "../../store/useCodeStore";
import type { ChatMessage } from "../../types/chat";
import * as api from "../../lib/api";
import {
  type ComposerAttachmentDraft,
  cloneAttachmentDraft,
  normalizeAttachmentDrafts,
  resolveAttachmentName,
  sanitizeChatHistory,
  startEditorChat,
  streamEditorChatResponse,
} from "../../lib/editorChat";
import { fromBackendMessage, safeTokenUsage } from "../../lib/chatMessagePersistence";
import {
  applyAssistantRunEvent,
  applyAssistantToolCallResult,
  applyAssistantToolCallStart,
  insertInjectedUserBeforeAssistant,
  shouldStopSidebarThreadSnapshotPolling,
  upsertAssistantMessage,
} from "./modeChatSidebarState";
import {
  deriveDraftThreadTitleFromMessage,
  getDisplayThreadTitle,
} from "../../lib/chatThreadTitle";
import { buildSurfaceContext } from "../../lib/contextBudget";
import { extractImportPaths } from "../../lib/importResolver";
import {
  detectProjectType,
  type ProjectDetection,
} from "../../lib/workspaceIntelligence";
import { nativeFs } from "../../lib/electronBridge";
import {
  parseMentions,
  type MentionRef,
} from "../../lib/mentionParser";
import type {
  BranchType,
  ModeChatSidebarToolCallResult,
  ModeChatSidebarToolCallStart,
  PendingQueueItem,
  RewriteBranchState,
  SidebarChatMode,
} from "./modeChatSidebarTypes";

interface MentionContext {
  mentioned_files: Array<{ path: string; content: string; lines: number }>;
  mentioned_symbols: string[];
  mentioned_folders: Array<{ path: string; entries: string[] }>;
  context_summary: string;
}

interface CreateBranchedThreadLineage {
  branchType: BranchType;
  branchPointMessageId?: string;
}

interface UseModeChatSidebarTransportArgs {
  mode: AppMode;
  workflowId: string;
  workspaceId: string | null;
  contextProvider?: () => string;
  chatModeRef: MutableRefObject<SidebarChatMode>;
  messagesRef: MutableRefObject<ChatMessage[]>;
  threadIdRef: MutableRefObject<string | null>;
  abortRef: MutableRefObject<AbortController | null>;
  rewriteTarget: RewriteBranchState | null;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setThreadId: Dispatch<SetStateAction<string | null>>;
  setThreadTitle: Dispatch<SetStateAction<string>>;
  setDetectedMode: Dispatch<SetStateAction<SidebarChatMode | null>>;
  setRewriteTarget: Dispatch<SetStateAction<RewriteBranchState | null>>;
  resetTurnState: () => void;
  fetchThreads: () => Promise<void>;
  createBranchedThread: (
    seedMessages: ChatMessage[],
    nextUserContent: string,
    lineage?: CreateBranchedThreadLineage,
  ) => Promise<string | null>;
  onToolCallStart?: (toolCall: ModeChatSidebarToolCallStart) => void;
  onToolCallResult?: (
    assistantId: string,
    toolCall: ModeChatSidebarToolCallResult,
  ) => Promise<void> | void;
}

const PROJECT_DETECTION_TTL_MS = 60_000;
const projectDetectionCache = new Map<
  string,
  { detectedAt: number; detection: ProjectDetection }
>();

async function getCachedProjectDetection(rootPath: string): Promise<ProjectDetection> {
  const cached = projectDetectionCache.get(rootPath);
  if (cached && Date.now() - cached.detectedAt < PROJECT_DETECTION_TTL_MS) {
    return cached.detection;
  }
  const detection = await detectProjectType(rootPath);
  projectDetectionCache.set(rootPath, {
    detectedAt: Date.now(),
    detection,
  });
  return detection;
}

function collectStructuredMentions(
  text: string,
): Array<{ type: string; identifier: string }> {
  return parseMentions(text).segments
    .filter((segment) => segment.type === "mention")
    .map((segment) => ({
      type: (segment as { type: "mention"; mention: MentionRef }).mention.type,
      identifier: (segment as { type: "mention"; mention: MentionRef }).mention.id,
    }));
}

async function expandMentionContext(
  mentions: Array<{ type: string; identifier: string }>,
): Promise<MentionContext> {
  const ctx: MentionContext = {
    mentioned_files: [],
    mentioned_symbols: [],
    mentioned_folders: [],
    context_summary: "",
  };

  const fileMentions = mentions.filter((mention) => mention.type === "file");
  const symbolMentions = mentions.filter((mention) => mention.type === "symbol");
  const folderMentions = mentions.filter((mention) => mention.type === "folder");

  const fileResults = await Promise.allSettled(
    fileMentions.map(async (mention) => {
      const content = await nativeFs.readFile(mention.identifier);
      if (content != null) {
        return {
          path: mention.identifier,
          content,
          lines: content.split("\n").length,
        };
      }
      return null;
    }),
  );
  for (const result of fileResults) {
    if (result.status === "fulfilled" && result.value) {
      ctx.mentioned_files.push(result.value);
    }
  }

  for (const mention of symbolMentions) {
    ctx.mentioned_symbols.push(mention.identifier);
  }

  const folderResults = await Promise.allSettled(
    folderMentions.map(async (mention) => {
      const entries = await nativeFs.readDir(mention.identifier);
      if (!entries) return null;
      return {
        path: mention.identifier,
        entries: entries.map((entry: { name: string; isDirectory: boolean }) =>
          `${entry.name}${entry.isDirectory ? "/" : ""}`,
        ),
      };
    }),
  );
  for (const result of folderResults) {
    if (result.status === "fulfilled" && result.value) {
      ctx.mentioned_folders.push(result.value);
    }
  }

  const parts: string[] = [];
  if (ctx.mentioned_files.length > 0) {
    parts.push(
      `Files: ${ctx.mentioned_files
        .map((file) => `${file.path} (${file.lines} lines)`)
        .join(", ")}`,
    );
  }
  if (ctx.mentioned_symbols.length > 0) {
    parts.push(`Symbols: ${ctx.mentioned_symbols.join(", ")}`);
  }
  if (ctx.mentioned_folders.length > 0) {
    parts.push(
      `Folders: ${ctx.mentioned_folders
        .map((folder) => `${folder.path} (${folder.entries.length} items)`)
        .join(", ")}`,
    );
  }
  ctx.context_summary = parts.length > 0 ? `[Context: ${parts.join("; ")}]` : "";

  return ctx;
}

function summarizeThreadTitle(messages: ChatMessage[]): string {
  const firstUser = messages.find(
    (message) => message.role === "user" && message.content.trim(),
  );
  if (!firstUser) return "New Chat";
  return deriveDraftThreadTitleFromMessage(firstUser.content, "New Chat");
}

export function useModeChatSidebarTransport({
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
  fetchThreads,
  createBranchedThread,
  onToolCallStart,
  onToolCallResult,
}: UseModeChatSidebarTransportArgs) {
  const [streaming, setStreaming] = useState(false);
  const [pendingQueue, setPendingQueue] = useState<PendingQueueItem[]>([]);
  const [activeChannelId, setActiveChannelId] = useState<string | null>(null);
  const activeChannelIdRef = useRef<string | null>(null);
  const activeRequestModeRef = useRef<SidebarChatMode | null>(null);

  const finishStream = useCallback(() => {
    setStreaming(false);
    setActiveChannelId(null);
    activeChannelIdRef.current = null;
    activeRequestModeRef.current = null;
    abortRef.current = null;
  }, [abortRef]);

  const resetTransportState = useCallback(() => {
    setPendingQueue([]);
    finishStream();
  }, [finishStream]);

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
    [chatModeRef],
  );

  const sendNow = useCallback(
    async (
      rawText: string,
      attachmentDrafts: ComposerAttachmentDraft[],
      modeOverride?: SidebarChatMode,
      historyOverride?: ChatMessage[],
      threadIdOverride?: string | null,
    ) => {
      const trimmed = rawText.trim();
      if (!trimmed && attachmentDrafts.length === 0) return;
      const effectiveMode = modeOverride ?? chatModeRef.current;

      if (rewriteTarget && !historyOverride) {
        const branchedThreadId = await createBranchedThread(
          rewriteTarget.historyBefore,
          trimmed,
          {
            branchType: rewriteTarget.branchType,
            branchPointMessageId: rewriteTarget.sourceMessageId,
          },
        );
        if (!branchedThreadId) return;
        await sendNow(
          rawText,
          attachmentDrafts.map(cloneAttachmentDraft),
          modeOverride,
          rewriteTarget.historyBefore,
          branchedThreadId,
        );
        return;
      }

      resetTurnState();
      const attachments = await normalizeAttachmentDrafts(attachmentDrafts);
      const contextSummary = contextProvider?.() ?? "";
      const structuredMentions = collectStructuredMentions(trimmed);
      const backendMentions = structuredMentions.filter(
        (mention) =>
          mention.type !== "file" &&
          mention.type !== "symbol" &&
          mention.type !== "folder",
      );
      const codeMentions = structuredMentions.filter(
        (mention) =>
          mention.type === "file" ||
          mention.type === "symbol" ||
          mention.type === "folder",
      );

      let mentionContext: MentionContext | null = null;
      if (codeMentions.length > 0) {
        mentionContext = await expandMentionContext(codeMentions);
      }

      const contextParts = [contextSummary];
      if (mentionContext?.context_summary) {
        contextParts.push(mentionContext.context_summary);
      }
      const combinedContext = contextParts.filter(Boolean).join("\n");
      const fullMessage = combinedContext
        ? trimmed
          ? `${combinedContext}\n\n${trimmed}`
          : combinedContext
        : trimmed;

      let surfaceContext: Record<string, unknown> = {
        mode,
        workspace_id: workspaceId ?? "_scratch",
      };
      if (mode === "development") {
        const codeState = useCodeStore.getState();
        const activeFile = codeState.openFiles.find(
          (file) => file.path === codeState.activeFilePath,
        );
        const root = codeState.pinnedRoots[0];
        const detection = root ? await getCachedProjectDetection(root) : null;
        surfaceContext = {
          ...buildSurfaceContext({
            activeFilePath: activeFile?.path ?? null,
            activeFileContent: activeFile?.content ?? null,
            activeFileLanguage: activeFile?.language ?? null,
            selectionText: null,
            openFilePaths: codeState.openFiles.map((file) => file.path),
            importNeighbors:
              activeFile != null
                ? extractImportPaths(activeFile.content, activeFile.path)
                : [],
            project: detection
              ? {
                  type: detection.type,
                  name: detection.name,
                  frameworks: detection.frameworks,
                  package_manager: detection.packageManager,
                }
              : null,
            mode,
            workspace_id: workspaceId ?? "_scratch",
          }),
        };
        if (root) {
          surfaceContext = {
            ...surfaceContext,
            workspace_root: root,
          };
        }
      }
      if (mentionContext) {
        surfaceContext = {
          ...surfaceContext,
          mentioned_files: mentionContext.mentioned_files.map((file) => ({
            path: file.path,
            lines: file.lines,
            content: file.content,
          })),
          mentioned_symbols: mentionContext.mentioned_symbols,
          mentioned_folders: mentionContext.mentioned_folders,
        };
      }

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

      if (!(threadIdOverride ?? threadIdRef.current)) {
        setThreadTitle(deriveDraftThreadTitleFromMessage(userMsg.content, "New Chat"));
      }

      const assistantId = crypto.randomUUID();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };

      const baseHistory = historyOverride ?? messagesRef.current;
      setMessages([...baseHistory, userMsg, assistantMsg]);
      setStreaming(true);
      setDetectedMode(null);
      setRewriteTarget(null);

      const controller = new AbortController();
      abortRef.current = controller;
      activeRequestModeRef.current = effectiveMode;

      try {
        const { threadId: nextThreadId, response } = await startEditorChat({
          message: fullMessage,
          workflowId,
          history: sanitizeChatHistory(
            baseHistory.flatMap((message) =>
              message.role === "user" || message.role === "assistant"
                ? [{ role: message.role, content: message.content }]
                : [],
            ),
          ),
          threadId: threadIdOverride ?? threadIdRef.current,
          mode: effectiveMode,
          scope: `mode-chat:${mode}`,
          attachments,
          mentions: backendMentions.length > 0 ? backendMentions : undefined,
          signal: controller.signal,
          surfaceContext,
        });
        setThreadId(nextThreadId);
        threadIdRef.current = nextThreadId;
        setThreadTitle((prev) =>
          getDisplayThreadTitle(prev, summarizeThreadTitle(baseHistory)),
        );
        void fetchThreads();

        const nextChatChannel =
          typeof response.stream_channel_id === "string" &&
          response.stream_channel_id.startsWith("chat-")
            ? response.stream_channel_id
            : null;
        setActiveChannelId(nextChatChannel);
        activeChannelIdRef.current = nextChatChannel;

        if (response.type === "run_started" && typeof response.run_id === "string") {
          const runId = response.run_id;
          const runScope = response.scope ?? "full";
          setMessages((prev) =>
            upsertAssistantMessage(prev, assistantId, (message) => ({
              ...message,
              runRef: {
                runId,
                scope: runScope,
                status: "running",
              },
            })),
          );
        }

        const ws = streamEditorChatResponse(response, {
          onQueued: (position) => {
            setMessages((prev) =>
              upsertAssistantMessage(prev, assistantId, (message) => ({
                ...message,
                content:
                  position > 1
                    ? `Queued behind ${position} earlier messages...`
                    : "Queued behind an earlier message...",
              })),
            );
          },
          onProgress: (content) => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              upsertAssistantMessage(prev, assistantId, (message) => ({
                ...message,
                content,
                progressStatus: undefined,
                progressFilePath: undefined,
              })),
            );
          },
          onProgressStatus: (status) => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              upsertAssistantMessage(prev, assistantId, (message) => ({
                ...message,
                progressStatus: status || message.progressStatus,
                progressFilePath: undefined,
              })),
            );
          },
          onToolCallStart: (toolCall) => {
            onToolCallStart?.(toolCall);
            setMessages((prev) =>
              applyAssistantToolCallStart(prev, assistantId, toolCall),
            );
          },
          onToolCallResult: (toolCall) => {
            void onToolCallResult?.(assistantId, toolCall);
            setMessages((prev) =>
              applyAssistantToolCallResult(prev, assistantId, toolCall),
            );
          },
          onRunEvent: (runEvent) => {
            setMessages((prev) => applyAssistantRunEvent(prev, assistantId, runEvent));
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
            setMessages((prev) =>
              insertInjectedUserBeforeAssistant(
                prev,
                assistantId,
                injectedUserMsg,
                Date.now(),
              ),
            );
          },
          onNotice: (content) => {
            if (controller.signal.aborted || !content.trim()) return;
            setMessages((prev) => [
              ...prev,
              {
                id: crypto.randomUUID(),
                role: "system",
                content,
                timestamp: Date.now(),
              },
            ]);
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
              upsertAssistantMessage(prev, assistantId, (message) => ({
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
              })),
            );
            if (
              effectiveMode === "auto" &&
              "detected_mode" in event &&
              typeof event.detected_mode === "string" &&
              event.detected_mode !== "progress_ack" &&
              event.detected_mode in {
                auto: true,
                agent: true,
                ask: true,
                plan: true,
                debug: true,
              }
            ) {
              setDetectedMode(event.detected_mode as SidebarChatMode);
            }
            const nextChannel =
              "stream_channel_id" in event &&
              typeof event.stream_channel_id === "string"
                ? event.stream_channel_id.trim()
                : "";
            if (!nextChannel) {
              finishStream();
            }
          },
          onError: (message) => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              upsertAssistantMessage(prev, assistantId, (entry) => ({
                ...entry,
                content: message || "Failed to connect to DAN server.",
                progressStatus: undefined,
                progressFilePath: undefined,
              })),
            );
            finishStream();
          },
          onCloseWithoutTerminalEvent: () => {
            if (controller.signal.aborted) return;
            setMessages((prev) =>
              upsertAssistantMessage(prev, assistantId, (entry) =>
                entry.content
                  ? entry
                  : {
                      ...entry,
                      content: "Connection lost. Please try again.",
                      progressStatus: undefined,
                      progressFilePath: undefined,
                    },
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

        if (nextChatChannel) {
          void (async () => {
            for (let attempt = 0; attempt < 12; attempt += 1) {
              await new Promise((resolve) => setTimeout(resolve, 5000));
              if (controller.signal.aborted) return;
              if (activeChannelIdRef.current !== nextChatChannel) return;
              try {
                const data = await api.getChatThread(workflowId, nextThreadId);
                const backendMessages = Array.isArray(data.messages)
                  ? (data.messages as Record<string, unknown>[])
                  : [];
                const normalized = backendMessages.map(fromBackendMessage);
                const lastMessage = normalized.at(-1);
                if (lastMessage?.role === "assistant" && lastMessage.content.trim()) {
                  setMessages(normalized);
                  finishStream();
                  return;
                }
              } catch (error) {
                if (shouldStopSidebarThreadSnapshotPolling(error)) {
                  return;
                }
              }
            }
          })();
        }
      } catch {
        setMessages((prev) =>
          upsertAssistantMessage(prev, assistantId, (entry) =>
            entry.content
              ? entry
              : { ...entry, content: "Failed to connect to DAN server." },
          ),
        );
        finishStream();
      }
    },
    [
      abortRef,
      chatModeRef,
      contextProvider,
      createBranchedThread,
      fetchThreads,
      finishStream,
      messagesRef,
      mode,
      onToolCallResult,
      onToolCallStart,
      resetTurnState,
      rewriteTarget,
      setDetectedMode,
      setMessages,
      setRewriteTarget,
      setThreadId,
      setThreadTitle,
      threadIdRef,
      workflowId,
      workspaceId,
    ],
  );

  const injectPendingQueueItem = useCallback(async (item: PendingQueueItem) => {
    const channelId = activeChannelIdRef.current;
    if (!channelId) return;
    try {
      await api.injectChatMessage(channelId, item.content, item.id);
      setPendingQueue((prev) => prev.filter((entry) => entry.id !== item.id));
    } catch (error) {
      console.warn("Failed to inject queued mode-chat message:", error);
    }
  }, []);

  useEffect(() => {
    if (streaming || pendingQueue.length === 0) return;
    const [next] = pendingQueue;
    setPendingQueue((prev) => prev.slice(1));
    void sendNow(next.content, next.attachments, next.mode);
  }, [pendingQueue, sendNow, streaming]);

  const handleStop = useCallback(async () => {
    const channelId = activeChannelIdRef.current;
    if (!channelId) return;
    try {
      await api.stopChatStream(channelId);
    } catch (error) {
      if (api.isApiStatusError(error, 404)) {
        if (abortRef.current) {
          abortRef.current.abort();
        } else {
          finishStream();
        }
        return;
      }
      console.warn("Failed to stop mode chat stream:", error);
    }
  }, [abortRef, finishStream]);

  const canPushIntoCurrentTurn = useCallback(
    (item: PendingQueueItem) =>
      streaming &&
      Boolean(activeChannelId) &&
      item.attachments.length === 0 &&
      item.mentions.length === 0 &&
      item.mode === activeRequestModeRef.current,
    [activeChannelId, streaming],
  );

  return {
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
  };
}
