import { useCallback, useRef, type Dispatch, type SetStateAction } from "react";
import { isElectron, nativeFs, nativeTerminal } from "../../lib/electronBridge";
import { type AppMode } from "../../store/useAppStore";
import { useCodeStore } from "../../store/useCodeStore";
import type {
  ChatMessage,
  ReviewableFileEdit,
} from "../../types/chat";
import { extractFileWritePaths } from "../../lib/toolCallPresentation";

interface UseModeChatSidebarNativeActionsParams {
  mode: AppMode;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
}

export interface ModeChatSidebarNativeActions {
  allowRunCodeBlocks: boolean;
  resetTurnState: () => void;
  onRunCodeBlock?: (command: string) => void;
  onReviewMultiFileEdits?: (edits: ReviewableFileEdit[]) => void;
  onToolCallStart?: (toolCall: ModeChatSidebarToolCall) => void;
  onToolCallResult?: (
    assistantMessageId: string,
    toolCall: ModeChatSidebarToolCall,
  ) => Promise<void>;
}

export interface ModeChatSidebarToolCall {
  id: string;
  toolName: string;
  argsPreview: string;
  status?: string;
  outputPreview?: string;
  durationMs?: number;
}

function mergeReviewableEdits(
  messages: ChatMessage[],
  assistantMessageId: string,
  nextEdits: ReviewableFileEdit[],
): ChatMessage[] {
  return messages.map((message) => {
    if (message.id !== assistantMessageId) return message;
    const merged = new Map(
      (message.reviewableFileEdits ?? []).map((edit) => [edit.filePath, edit]),
    );
    nextEdits.forEach((edit) => merged.set(edit.filePath, edit));
    return {
      ...message,
      reviewableFileEdits: Array.from(merged.values()),
    };
  });
}

export function useModeChatSidebarNativeActions({
  mode,
  setMessages,
}: UseModeChatSidebarNativeActionsParams): ModeChatSidebarNativeActions {
  const fileSnapshotsRef = useRef<Map<string, string | null>>(new Map());
  const fileSnapshotLoadsRef = useRef<Map<string, Promise<void>>>(new Map());
  const allowRunCodeBlocks = mode === "development";

  const resetTurnState = useCallback(() => {
    fileSnapshotsRef.current.clear();
    fileSnapshotLoadsRef.current.clear();
  }, []);

  const capturePreWriteSnapshot = useCallback(async (filePath: string) => {
    if (!allowRunCodeBlocks || !filePath || fileSnapshotsRef.current.has(filePath)) {
      return;
    }

    const pending = fileSnapshotLoadsRef.current.get(filePath);
    if (pending) {
      await pending;
      return;
    }

    const load = (async () => {
      const openFile = useCodeStore
        .getState()
        .openFiles.find((entry) => entry.path === filePath);
      if (openFile) {
        fileSnapshotsRef.current.set(filePath, openFile.content);
        return;
      }

      const exists = await nativeFs.exists(filePath);
      if (!exists) {
        if (!fileSnapshotsRef.current.has(filePath)) {
          fileSnapshotsRef.current.set(filePath, null);
        }
        return;
      }

      const content = await nativeFs.readFile(filePath);
      if (!fileSnapshotsRef.current.has(filePath)) {
        fileSnapshotsRef.current.set(filePath, content ?? null);
      }
    })();

    fileSnapshotLoadsRef.current.set(filePath, load);
    try {
      await load;
    } finally {
      fileSnapshotLoadsRef.current.delete(filePath);
    }
  }, [allowRunCodeBlocks]);

  const onRunCodeBlock = useCallback(async (command: string) => {
    if (!allowRunCodeBlocks || !isElectron()) return;
    const { pinnedRoots, setShowTerminal, addTerminal, setActiveTerminal } =
      useCodeStore.getState();
    setShowTerminal(true);
    const cwd = pinnedRoots[0] || undefined;
    const id = await nativeTerminal.create({ cwd });
    if (!id) return;
    const shortCmd = command.length > 40 ? command.slice(0, 37) + "..." : command;
    addTerminal(id, `\u26A1 ${shortCmd}`);
    setActiveTerminal(id);
    window.setTimeout(() => {
      void nativeTerminal.write(id, command + "\n");
    }, 300);
  }, [allowRunCodeBlocks]);

  const onReviewMultiFileEdits = useCallback((edits: ReviewableFileEdit[]) => {
    if (!allowRunCodeBlocks || edits.length === 0) return;
    useCodeStore.getState().openMultiFileReview(
      edits.map((edit) => ({
        filePath: edit.filePath,
        originalContent: edit.originalContent,
        modifiedContent: edit.modifiedContent,
        createdByThisTurn: edit.createdByThisTurn,
        accepted: null,
      })),
    );
  }, [allowRunCodeBlocks]);

  const onToolCallStart = useCallback((toolCall: ModeChatSidebarToolCall) => {
    if (!allowRunCodeBlocks) return;
    if (
      toolCall.toolName !== "file_write" &&
      toolCall.toolName !== "write_file" &&
      toolCall.toolName !== "edit_file"
    ) {
      return;
    }
    const filePaths = extractFileWritePaths([toolCall]);
    for (const filePath of filePaths) {
      void capturePreWriteSnapshot(filePath);
    }
  }, [allowRunCodeBlocks, capturePreWriteSnapshot]);

  const onToolCallResult = useCallback(async (
    assistantMessageId: string,
    toolCall: ModeChatSidebarToolCall,
  ) => {
    if (!allowRunCodeBlocks) return;
    if (
      toolCall.status !== "success" ||
      (
        toolCall.toolName !== "file_write" &&
        toolCall.toolName !== "write_file" &&
        toolCall.toolName !== "edit_file"
      )
    ) {
      return;
    }

    const filePaths = extractFileWritePaths([toolCall]);
    if (filePaths.length === 0) return;

    const edits = await Promise.all(
      filePaths.map(async (filePath) => {
        await capturePreWriteSnapshot(filePath);
        const originalContent = fileSnapshotsRef.current.has(filePath)
          ? (fileSnapshotsRef.current.get(filePath) ?? null)
          : null;
        const modifiedContent = await nativeFs.readFile(filePath);
        if (modifiedContent === null) return null;
        return {
          filePath,
          originalContent,
          modifiedContent,
          createdByThisTurn: originalContent === null,
        } satisfies ReviewableFileEdit;
      }),
    );

    const nextEdits = edits.filter((edit): edit is ReviewableFileEdit => edit !== null);
    if (nextEdits.length === 0) return;

    setMessages((messages) =>
      mergeReviewableEdits(messages, assistantMessageId, nextEdits),
    );
  }, [allowRunCodeBlocks, capturePreWriteSnapshot, setMessages]);

  return {
    allowRunCodeBlocks,
    resetTurnState,
    onRunCodeBlock: allowRunCodeBlocks ? onRunCodeBlock : undefined,
    onReviewMultiFileEdits: allowRunCodeBlocks ? onReviewMultiFileEdits : undefined,
    onToolCallStart: allowRunCodeBlocks ? onToolCallStart : undefined,
    onToolCallResult: allowRunCodeBlocks ? onToolCallResult : undefined,
  };
}
