import type { ComposerAttachmentDraft, EditorChatMode } from "../../lib/editorChat";
import type { ChatMessage } from "../../types/chat";

export type SidebarChatMode = Exclude<EditorChatMode, "conversation">;

export interface PendingQueueItem {
  id: string;
  content: string;
  timestamp: number;
  attachments: ComposerAttachmentDraft[];
  mode: SidebarChatMode;
  mentions: Array<{ type: string; identifier: string }>;
}

export type BranchType = "edit" | "regenerate" | "explore";

export interface RewriteBranchState {
  sourceMessageId: string;
  historyBefore: ChatMessage[];
  branchType: BranchType;
}

export interface ModeChatSidebarToolCallStart {
  id: string;
  toolName: string;
  argsPreview: string;
}

export interface ModeChatSidebarToolCallResult
  extends ModeChatSidebarToolCallStart {
  status: string;
  outputPreview?: string;
  durationMs?: number;
}
