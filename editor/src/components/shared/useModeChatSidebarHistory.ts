import {
  useCallback,
  useEffect,
  useState,
  type Dispatch,
  type MutableRefObject,
  type RefObject,
  type SetStateAction,
} from "react";
import { useAppStore, type AppMode } from "../../store/useAppStore";
import { useWorkspaceStore } from "../../store/useWorkspaceStore";
import type { ChatMessage } from "../../types/chat";
import type { ChatThreadSummary } from "../../lib/api";
import * as api from "../../lib/api";
import type { ComposerAttachmentDraft } from "../../lib/editorChat";
import {
  fromBackendMessage,
  toBackendMessage,
} from "../../lib/chatMessagePersistence";
import {
  deriveDraftThreadTitleFromMessage,
  getDisplayThreadTitle,
} from "../../lib/chatThreadTitle";
import {
  clearModeChatSession,
} from "./modeChatSidebarSession";
import type {
  RewriteBranchState,
  SidebarChatMode,
} from "./modeChatSidebarTypes";

function summarizeThreadTitle(messages: ChatMessage[]): string {
  const firstUser = messages.find(
    (message) => message.role === "user" && message.content.trim(),
  );
  if (!firstUser) return "New Chat";
  return deriveDraftThreadTitleFromMessage(firstUser.content, "New Chat");
}

export interface UseModeChatSidebarHistoryOptions {
  mode: AppMode;
  workflowId: string;
  workspaceId: string | null;
  onClose: () => void;
  streaming: boolean;
  pendingQueueLength: number;
  messagesRef: MutableRefObject<ChatMessage[]>;
  threadIdRef: MutableRefObject<string | null>;
  chatModeRef: MutableRefObject<SidebarChatMode>;
  abortRef: MutableRefObject<AbortController | null>;
  saveTimerRef: MutableRefObject<ReturnType<typeof setTimeout> | null>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  resetTransportState: () => void;
  resetTurnState: () => void;
  clearPendingQueue: () => void;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setChatMode: Dispatch<SetStateAction<SidebarChatMode>>;
  setThreadId: Dispatch<SetStateAction<string | null>>;
  setThreadTitle: Dispatch<SetStateAction<string>>;
  setDetectedMode: Dispatch<SetStateAction<SidebarChatMode | null>>;
  setInput: Dispatch<SetStateAction<string>>;
  setUserAttachments: Dispatch<SetStateAction<ComposerAttachmentDraft[]>>;
  setRewriteTarget: Dispatch<SetStateAction<RewriteBranchState | null>>;
  setPasteHint: Dispatch<
    SetStateAction<{ type: "url" | "code"; value: string } | null>
  >;
  setMentionQuery: Dispatch<SetStateAction<string | null>>;
  setMentionAnchor: Dispatch<
    SetStateAction<{ top: number; left: number } | null>
  >;
}

export interface ModeChatSidebarHistoryController {
  threads: ChatThreadSummary[];
  loadingThreads: boolean;
  showThreadList: boolean;
  pendingOpenFullChat: boolean;
  setShowThreadList: Dispatch<SetStateAction<boolean>>;
  fetchThreads: () => Promise<void>;
  loadThread: (targetThreadId: string) => Promise<void>;
  handleNewChat: () => Promise<void>;
  handleClear: () => void;
  openFullChat: () => void;
  persistThreadSnapshot: (options?: { forceCreate?: boolean }) => Promise<string | null>;
}

export function useModeChatSidebarHistory({
  mode,
  workflowId,
  workspaceId,
  onClose,
  streaming,
  pendingQueueLength,
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
}: UseModeChatSidebarHistoryOptions): ModeChatSidebarHistoryController {
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const [showThreadList, setShowThreadList] = useState(false);
  const [pendingOpenFullChat, setPendingOpenFullChat] = useState(false);

  const fetchThreads = useCallback(async () => {
    setLoadingThreads(true);
    try {
      const data = await api.listChatThreads(workflowId);
      const next = Array.isArray(data.threads)
        ? [...data.threads].sort(
            (a, b) =>
              Date.parse(b.updated_at || "") - Date.parse(a.updated_at || ""),
          )
        : [];
      setThreads(next);
    } catch (error) {
      console.warn("Failed to fetch sidebar chat threads:", error);
    } finally {
      setLoadingThreads(false);
    }
  }, [workflowId]);

  const persistThreadSnapshot = useCallback(async (options?: { forceCreate?: boolean }) => {
    const snapshot = messagesRef.current;
    const currentMode = chatModeRef.current;
    const existingThreadId = threadIdRef.current;
    const forceCreate = options?.forceCreate ?? false;

    if (snapshot.length === 0) {
      if (existingThreadId) {
        try {
          await api.updateChatThread(workflowId, existingThreadId, {
            mode: currentMode,
          });
        } catch {
          /* ignore */
        }
      }
      if (!forceCreate) {
        return existingThreadId;
      }
      const created = await api.createChatThread(workflowId, {
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
        await api.updateChatThread(workflowId, existingThreadId, payload);
        return existingThreadId;
      } catch (error) {
        console.warn("Failed to sync mode chat thread, creating a fresh one:", error);
      }
    }

    const firstUserMessage =
      snapshot.find((message) => message.role === "user" && message.content.trim())?.content ??
      "New Chat";
    const created = await api.createChatThread(workflowId, {
      title: deriveDraftThreadTitleFromMessage(firstUserMessage),
      mode: currentMode,
    });
    const nextThreadId =
      typeof created.id === "string" && created.id.trim() ? created.id : null;
    if (!nextThreadId) return null;

    await api.updateChatThread(workflowId, nextThreadId, payload);
    setThreadId(nextThreadId);
    threadIdRef.current = nextThreadId;
    return nextThreadId;
  }, [chatModeRef, messagesRef, setThreadId, threadIdRef, workflowId]);

  const loadThread = useCallback(async (targetThreadId: string) => {
    try {
      await persistThreadSnapshot();
      const data = await api.getChatThread(workflowId, targetThreadId);
      const backendMsgs = Array.isArray(data.messages)
        ? (data.messages as Record<string, unknown>[])
        : [];
      const normalized = backendMsgs.map(fromBackendMessage);
      const rawMode = typeof data.mode === "string" ? data.mode : "auto";
      const nextMode = (
        ["auto", "agent", "ask", "plan", "debug"].includes(rawMode)
          ? rawMode
          : "auto"
      ) as SidebarChatMode;
      setMessages(normalized);
      setChatMode(nextMode);
      setThreadId(targetThreadId);
      threadIdRef.current = targetThreadId;
      setThreadTitle(
        getDisplayThreadTitle(
          typeof data.title === "string" ? data.title : summarizeThreadTitle(normalized),
          "New Chat",
        ),
      );
      chatModeRef.current = nextMode;
      setDetectedMode(null);
      setInput("");
      setUserAttachments([]);
      clearPendingQueue();
      setRewriteTarget(null);
      setShowThreadList(false);
      requestAnimationFrame(() => textareaRef.current?.focus());
    } catch (error) {
      console.warn("Failed to load sidebar chat thread:", error);
    }
  }, [
    chatModeRef,
    persistThreadSnapshot,
    clearPendingQueue,
    setDetectedMode,
    setInput,
    setMessages,
    setChatMode,
    setRewriteTarget,
    setThreadId,
    setThreadTitle,
    setUserAttachments,
    textareaRef,
    threadIdRef,
    workflowId,
  ]);

  const handleNewChat = useCallback(async () => {
    try {
      await persistThreadSnapshot();
    } catch (error) {
      console.warn("Failed to persist sidebar chat before starting a new one:", error);
    }
    abortRef.current?.abort();
    setMessages([]);
    setThreadId(null);
    threadIdRef.current = null;
    setThreadTitle("New Chat");
    setDetectedMode(null);
    setInput("");
    resetTransportState();
    setPendingOpenFullChat(false);
    setUserAttachments([]);
    setPasteHint(null);
    setMentionQuery(null);
    setMentionAnchor(null);
    setRewriteTarget(null);
    setShowThreadList(false);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [
    abortRef,
    persistThreadSnapshot,
    resetTransportState,
    setDetectedMode,
    setInput,
    setMentionAnchor,
    setMentionQuery,
    setMessages,
    setPasteHint,
    setRewriteTarget,
    setThreadId,
    setThreadTitle,
    setUserAttachments,
    textareaRef,
    threadIdRef,
  ]);

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
      useAppStore.getState().setActiveChatThread(targetThreadId, workflowId);
    } else {
      useAppStore.getState().setActiveChatThread(null, workflowId);
    }
    useAppStore.getState().setMode("chat");
    onClose();
  }, [onClose, persistThreadSnapshot, threadIdRef, workflowId]);

  const openFullChat = useCallback(() => {
    if (streaming || abortRef.current || pendingQueueLength > 0) {
      setPendingOpenFullChat(true);
      return;
    }
    void completeOpenFullChat();
  }, [abortRef, completeOpenFullChat, pendingQueueLength, streaming]);

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
    resetTransportState();
    setPendingOpenFullChat(false);
    setInput("");
    resetTurnState();
    setThreadId(null);
    threadIdRef.current = null;
    setThreadTitle("New Chat");
    setDetectedMode(null);
    setPasteHint(null);
    setMentionQuery(null);
    setMentionAnchor(null);
    setRewriteTarget(null);
    setShowThreadList(false);
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    if (workspaceId) {
      clearModeChatSession(workspaceId, mode, workflowId);
    }
  }, [
    abortRef,
    messagesRef,
    mode,
    resetTransportState,
    resetTurnState,
    saveTimerRef,
    setDetectedMode,
    setInput,
    setMentionAnchor,
    setMentionQuery,
    setMessages,
    setPasteHint,
    setRewriteTarget,
    setThreadId,
    setThreadTitle,
    setUserAttachments,
    textareaRef,
    threadIdRef,
    workflowId,
    workspaceId,
  ]);

  useEffect(() => {
    void fetchThreads();
  }, [fetchThreads]);

  useEffect(() => {
    if (!showThreadList) return;
    void fetchThreads();
  }, [fetchThreads, showThreadList]);

  useEffect(() => {
    if (!pendingOpenFullChat || streaming || abortRef.current || pendingQueueLength > 0) {
      return;
    }
    setPendingOpenFullChat(false);
    void completeOpenFullChat();
  }, [
    abortRef,
    completeOpenFullChat,
    pendingOpenFullChat,
    pendingQueueLength,
    streaming,
  ]);

  return {
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
    persistThreadSnapshot,
  };
}
