import { useCallback, useEffect, useRef } from "react";

import * as api from "../../lib/api";
import {
  toBackendMessage,
} from "../../lib/chatMessagePersistence";
import {
  createThreadPersistenceCoordinator,
  type ThreadPersistOptions,
} from "../../lib/threadPersistenceCoordinator";
import type { ChatMessage } from "../../types/chat";

type SaveThreadMessagesOptions = ThreadPersistOptions;

async function saveChatThreadMessages(
  workflowId: string,
  threadId: string,
  nextMessages: ChatMessage[],
  options?: SaveThreadMessagesOptions,
): Promise<void> {
  try {
    await api.updateChatThread(workflowId, threadId, {
      messages: nextMessages.map(toBackendMessage),
    });
  } catch (error) {
    if (!options?.silent) {
      console.warn(options?.label ?? "Failed to save thread:", error);
    }
  }
}

export function useChatPanelThreadPersistence() {
  const persistenceCoordinatorRef = useRef<
    ReturnType<typeof createThreadPersistenceCoordinator<ChatMessage[]>> | null
  >(null);

  if (!persistenceCoordinatorRef.current) {
    persistenceCoordinatorRef.current =
      createThreadPersistenceCoordinator<ChatMessage[]>({
        persist: saveChatThreadMessages,
      });
  }

  useEffect(() => {
    return () => {
      persistenceCoordinatorRef.current?.dispose();
    };
  }, []);

  const saveThreadMessages = useCallback(
    async (
      workflowId: string | null,
      threadId: string | null | undefined,
      nextMessages: ChatMessage[],
      options?: SaveThreadMessagesOptions,
    ): Promise<void> => {
      if (!workflowId || !threadId) return;
      return saveChatThreadMessages(workflowId, threadId, nextMessages, options);
    },
    [],
  );

  const persistThreadMessages = useCallback(
    (
      workflowId: string | null,
      threadId: string | null | undefined,
      nextMessages: ChatMessage[],
      options?: SaveThreadMessagesOptions,
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

  const cancelThreadPersistence = useCallback(
    (workflowId: string, threadId: string) => {
      persistenceCoordinatorRef.current?.cancelForThread(workflowId, threadId);
    },
    [],
  );

  return {
    cancelThreadPersistence,
    flushScheduledThreadPersist,
    persistThreadMessages,
    saveThreadMessages,
    scheduleThreadPersist,
  };
}
