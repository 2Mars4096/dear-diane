import { describe, expect, it } from "vitest";

import { describeLatestToolProgress } from "../../../lib/toolCallPresentation";
import type { ChatMessage, RunEventPayload, ToolCallInfo } from "../../../types/chat";
import {
  applyAssistantRunEvent,
  applyAssistantToolCallResult,
  applyAssistantToolCallStart,
  insertInjectedUserBeforeAssistant,
  shouldStopSidebarThreadSnapshotPolling,
} from "../modeChatSidebarState";

const EXISTING_USER_MESSAGE: ChatMessage = {
  id: "user-1",
  role: "user",
  content: "Please update the file.",
  timestamp: 1,
};

describe("modeChatSidebarState", () => {
  it("backfills a missing assistant row for tool-call start updates", () => {
    const next = applyAssistantToolCallStart(
      [EXISTING_USER_MESSAGE],
      "assistant-1",
      {
        id: "tool-1",
        toolName: "edit_file",
        argsPreview: '{"path":"/tmp/example.ts"}',
      },
    );

    expect(next).toHaveLength(2);
    expect(next[0]).toEqual(EXISTING_USER_MESSAGE);
    expect(next[1]).toMatchObject({
      id: "assistant-1",
      role: "assistant",
      content: "",
      toolCalls: [
        {
          id: "tool-1",
          toolName: "edit_file",
          argsPreview: '{"path":"/tmp/example.ts"}',
          status: "running",
        },
      ],
      progressFilePath: "/tmp/example.ts",
    });
  });

  it("backfills a missing assistant row for tool-call results", () => {
    const toolCall: ToolCallInfo = {
      id: "tool-2",
      toolName: "write_file",
      argsPreview: '{"path":"/tmp/output.py"}',
      status: "success",
      outputPreview: "Saved file",
      durationMs: 24,
    };

    const next = applyAssistantToolCallResult(
      [EXISTING_USER_MESSAGE],
      "assistant-2",
      toolCall,
    );
    const progress = describeLatestToolProgress([toolCall]);

    expect(next).toHaveLength(2);
    expect(next[1]).toMatchObject({
      id: "assistant-2",
      role: "assistant",
      toolCalls: [toolCall],
      progressStatus: progress?.text,
      progressFilePath: progress?.filePath,
    });
  });

  it("backfills a missing assistant row for run events", () => {
    const runEvent: RunEventPayload = {
      type: "chat_run_event",
      event_type: "run_failed",
      summary: "Run failed",
      detail: {
        run_id: "run-123",
        scope: "full",
      },
    };

    const next = applyAssistantRunEvent(
      [EXISTING_USER_MESSAGE],
      "assistant-3",
      runEvent,
    );

    expect(next).toHaveLength(2);
    expect(next[1]).toMatchObject({
      id: "assistant-3",
      role: "assistant",
      runEvents: [runEvent],
      runRef: {
        runId: "run-123",
        scope: "full",
        status: "failed",
      },
    });
  });

  it("preserves the assistant slot when an injected user message arrives first", () => {
    const injectedUserMsg: ChatMessage = {
      id: "user-2",
      role: "user",
      content: "Retry with the latest change.",
      timestamp: 2,
    };

    const next = insertInjectedUserBeforeAssistant(
      [EXISTING_USER_MESSAGE],
      "assistant-4",
      injectedUserMsg,
      3,
    );

    expect(next).toHaveLength(3);
    expect(next.map((message) => message.role)).toEqual([
      "user",
      "user",
      "assistant",
    ]);
    expect(next[1]).toEqual(injectedUserMsg);
    expect(next[2]).toMatchObject({
      id: "assistant-4",
      role: "assistant",
      content: "",
      timestamp: 3,
    });
  });

  it("stops sidebar thread polling after a confirmed 404", () => {
    expect(
      shouldStopSidebarThreadSnapshotPolling(new Error("404: Thread not found")),
    ).toBe(true);
    expect(
      shouldStopSidebarThreadSnapshotPolling(new Error("500: Internal error")),
    ).toBe(false);
  });
});
