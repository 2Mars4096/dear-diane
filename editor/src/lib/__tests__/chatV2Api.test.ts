import { describe, expect, it } from "vitest";

import {
  CHAT_V2_ENDPOINTS,
  chatV2MessagesFromBackend,
  chatV2MessagesToBackend,
  type ChatV2TaskSnapshot,
  normalizeChatV2History,
} from "../chatV2Api";
import type { ChatMessage } from "../../types/chat";

describe("chatV2Api", () => {
  it("names the DAN chat portal endpoints used by the V2 frontend", () => {
    expect(CHAT_V2_ENDPOINTS.sendMessage).toBe("/api/v2/chat/message");
    expect(CHAT_V2_ENDPOINTS.streamEvents("chat-abc")).toBe(
      "/api/chat/chat-abc/events",
    );
    expect(CHAT_V2_ENDPOINTS.stopStream("chat-abc")).toBe(
      "/api/chat/chat-abc/stop",
    );
    expect(CHAT_V2_ENDPOINTS.createAgentRun).toBe("/api/v2/agent-runs");
    expect(CHAT_V2_ENDPOINTS.listTasks).toBe("/api/v2/tasks");
    expect(CHAT_V2_ENDPOINTS.getTask("task-1")).toBe("/api/v2/tasks/task-1");
    expect(CHAT_V2_ENDPOINTS.listThreadTasks("thread-1")).toBe(
      "/api/v2/threads/thread-1/tasks",
    );
    expect(CHAT_V2_ENDPOINTS.executeAgentRun("run-1")).toBe(
      "/api/v2/agent-runs/run-1/execute",
    );
    expect(CHAT_V2_ENDPOINTS.agentRunEvents("run-1")).toBe(
      "/api/v2/agent-runs/run-1/events",
    );
    expect(CHAT_V2_ENDPOINTS.agentRunCommands("run-1")).toBe(
      "/api/v2/agent-runs/run-1/commands",
    );
    expect(CHAT_V2_ENDPOINTS.listThreads).toBe("/api/chats");
    expect(CHAT_V2_ENDPOINTS.getThread("_scratch", "thread-1")).toBe(
      "/api/chats/_scratch/thread-1",
    );
  });

  it("keeps only non-empty user and assistant messages in request history", () => {
    const messages: ChatMessage[] = [
      {
        id: "user-1",
        role: "user",
        content: "  Build a plan  ",
        timestamp: 1,
      },
      {
        id: "assistant-empty",
        role: "assistant",
        content: " ",
        timestamp: 2,
      },
      {
        id: "system-1",
        role: "system",
        content: "Do not send this",
        timestamp: 3,
      },
      {
        id: "assistant-1",
        role: "assistant",
        content: "Done",
        timestamp: 4,
      },
    ];

    expect(normalizeChatV2History(messages)).toEqual([
      { role: "user", content: "Build a plan" },
      { role: "assistant", content: "Done" },
    ]);
  });

  it("round-trips messages through the backend persistence shape", () => {
    const messages: ChatMessage[] = [
      {
        id: "assistant-1",
        role: "assistant",
        content: "Saved",
        timestamp: Date.UTC(2026, 3, 27),
        toolCalls: [
          {
            id: "tool-1",
            toolName: "file_read",
            argsPreview: '{"path":"README.md"}',
            status: "success",
            outputPreview: "ok",
            durationMs: 5,
          },
        ],
        taskRunRef: {
          taskId: "task-1",
          runId: "arun-1",
          status: "running",
          workspaceRoot: "/tmp/workspace",
          workspaceId: "workspace-alpha",
        },
      },
    ];

    const backend = chatV2MessagesToBackend(messages);
    expect(backend[0]).toMatchObject({
      id: "assistant-1",
      role: "assistant",
      content: "Saved",
      tool_calls: [
        {
          id: "tool-1",
          tool_name: "file_read",
          status: "success",
        },
      ],
      task_run_ref: {
        task_id: "task-1",
        run_id: "arun-1",
        status: "running",
        workspace_root: "/tmp/workspace",
        workspace_id: "workspace-alpha",
      },
    });

    expect(chatV2MessagesFromBackend(backend)[0]).toMatchObject({
      id: "assistant-1",
      role: "assistant",
      content: "Saved",
      toolCalls: [
        {
          id: "tool-1",
          toolName: "file_read",
          status: "success",
        },
      ],
      taskRunRef: {
        taskId: "task-1",
        runId: "arun-1",
        status: "running",
        workspaceRoot: "/tmp/workspace",
        workspaceId: "workspace-alpha",
      },
    });
  });

  it("types V2 task snapshots with lane-specific queue metadata", () => {
    const task: ChatV2TaskSnapshot = {
      task_id: "task-1",
      thread_id: "thread-1",
      status: "running",
      phase: "worker",
      queue_position: 1,
      latest_progress: "Writing files",
      latest_artifact_refs: [],
      blocker: "",
      trace_refs: [],
      metadata: {
        active_run_id: "arun-1",
        append_queue_length: 1,
        continue_queue_length: 1,
        queue_items: [
          {
            id: "q-1",
            task_id: "task-1",
            lane: "append",
            status: "queued",
            text: "also update tests",
            position: 1,
          },
          {
            id: "q-2",
            task_id: "task-1",
            lane: "continue_after_current",
            status: "queued",
            text: "then write docs",
            position: 1,
          },
        ],
      },
    };

    expect(task.metadata.queue_items?.map((item) => item.lane)).toEqual([
      "append",
      "continue_after_current",
    ]);
  });
});
