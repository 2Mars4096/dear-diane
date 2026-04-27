import { describe, expect, it } from "vitest";

import {
  CHAT_V2_ENDPOINTS,
  chatV2MessagesFromBackend,
  chatV2MessagesToBackend,
  normalizeChatV2History,
} from "../chatV2Api";
import type { ChatMessage } from "../../types/chat";

describe("chatV2Api", () => {
  it("names the DAN chat portal endpoints used by the V2 frontend", () => {
    expect(CHAT_V2_ENDPOINTS.sendMessage).toBe("/api/chat/message");
    expect(CHAT_V2_ENDPOINTS.streamEvents("chat-abc")).toBe(
      "/api/chat/chat-abc/events",
    );
    expect(CHAT_V2_ENDPOINTS.stopStream("chat-abc")).toBe(
      "/api/chat/chat-abc/stop",
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
    });
  });
});
