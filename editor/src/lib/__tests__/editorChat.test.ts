import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  connectChatStream: vi.fn(),
}));

import { connectChatStream } from "../api";

import {
  chatAttachmentToComposerDraft,
  composerDraftToChatAttachment,
  fileToAttachmentDraft,
  resolveAttachmentName,
  sanitizeChatHistory,
  streamEditorChatResponse,
} from "../editorChat";

afterEach(() => {
  vi.mocked(connectChatStream).mockReset();
});

describe("editorChat attachment helpers", () => {
  it("provides a fallback label for unnamed pasted images", () => {
    expect(resolveAttachmentName("", "image/png")).toBe("Pasted Image.png");
  });

  it("preserves explicit attachment names", () => {
    expect(resolveAttachmentName("diagram.png", "image/png")).toBe("diagram.png");
  });

  it("applies the fallback label when clipboard files have no name", () => {
    const file = new File(["pixels"], "", { type: "image/png" });
    expect(fileToAttachmentDraft(file).name).toBe("Pasted Image.png");
  });

  it("drops empty assistant placeholders from serialized history", () => {
    expect(
      sanitizeChatHistory([
        { role: "user", content: "Summarize this" },
        { role: "assistant", content: "" },
        { role: "assistant", content: "Working on it" },
        { role: "assistant", content: "   " },
      ]),
    ).toEqual([
      { role: "user", content: "Summarize this" },
      { role: "assistant", content: "Working on it" },
    ]);
  });

  it("round-trips persisted chat attachments back into composer drafts", () => {
    const draft = chatAttachmentToComposerDraft({
      filename: "figure.png",
      path: "/tmp/figure.png",
      size: 128,
      mimeType: "image/png",
      kind: "figure",
      caption: "Example figure",
      source: "clipboard",
    });

    expect(composerDraftToChatAttachment(draft)).toEqual({
      filename: "figure.png",
      path: "/tmp/figure.png",
      size: 128,
      mimeType: "image/png",
      kind: "figure",
      caption: "Example figure",
      source: "clipboard",
    });
  });

  it("forwards streamed run events to callers", () => {
    const close = vi.fn();
    vi.mocked(connectChatStream).mockImplementation(
      (_channelId, onEvent) => {
        onEvent({
          type: "chat_run_event",
          run_event: {
            type: "chat_run_event",
            event_type: "node_started",
            node_id: "node-1",
            summary: "Started node-1",
          },
        });
        onEvent({
          type: "chat_complete",
          content: "Done",
        });
        return { close } as unknown as WebSocket;
      },
    );

    const onRunEvent = vi.fn();
    const onComplete = vi.fn();

    streamEditorChatResponse(
      {
        message_id: "msg-1",
        stream_channel_id: "chat-123",
      },
      {
        onRunEvent,
        onComplete,
      },
    );

    expect(onRunEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        event_type: "node_started",
        node_id: "node-1",
      }),
    );
    expect(onComplete).toHaveBeenCalledWith(
      "Done",
      expect.objectContaining({ type: "chat_complete" }),
    );
  });

  it("follows queued replacement channels", () => {
    const onQueued = vi.fn();
    const onComplete = vi.fn();
    const onChannelChange = vi.fn();

    vi.mocked(connectChatStream).mockImplementation((channelId, onEvent, onClose) => {
      if (channelId === "chat-1") {
        return {
          close: () => {
            onClose?.();
          },
        } as unknown as WebSocket;
      }
      if (channelId === "chat-2") {
        onEvent({
          type: "chat_complete",
          content: "Done after queue",
        });
        return {
          close: () => {
            onClose?.();
          },
        } as unknown as WebSocket;
      }
      throw new Error(`Unexpected channel ${channelId}`);
    });

    streamEditorChatResponse(
      {
        message_id: "msg-queued",
        stream_channel_id: "chat-1",
      },
      {
        onQueued,
        onComplete,
        onChannelChange,
      },
    );

    const firstOnEvent = vi.mocked(connectChatStream).mock.calls[0]?.[1];
    firstOnEvent?.({
      type: "chat_queued",
      queue_position: 2,
      stream_channel_id: "chat-2",
    });

    expect(onQueued).toHaveBeenCalledWith(2);
    expect(onChannelChange).toHaveBeenCalledWith("chat-2");
    expect(onComplete).toHaveBeenCalledWith(
      "Done after queue",
      expect.objectContaining({ type: "chat_complete" }),
    );
  });

  it("forwards injected messages to callers", () => {
    vi.mocked(connectChatStream).mockImplementation((_channelId, _onEvent, onClose) => {
      return {
        close: () => {
          onClose?.();
        },
      } as unknown as WebSocket;
    });

    const onInjectedMessage = vi.fn();

    streamEditorChatResponse(
      {
        message_id: "msg-injected",
        stream_channel_id: "chat-1",
      },
      {
        onInjectedMessage,
      },
    );

    const onEvent = vi.mocked(connectChatStream).mock.calls[0]?.[1];
    onEvent?.({
      type: "chat_injected_message",
      inject_id: "inject-1",
      content: "Follow up here",
    });

    expect(onInjectedMessage).toHaveBeenCalledWith({
      id: "inject-1",
      content: "Follow up here",
    });
  });

  it("treats terminal run events as clean closes", () => {
    let closeCallback: (() => void) | undefined;
    vi.mocked(connectChatStream).mockImplementation((_channelId, onEvent, onClose) => {
      closeCallback = onClose;
      onEvent({
        type: "chat_run_event",
        run_event: {
          type: "run_event",
          event_type: "run_completed",
          summary: "Run completed successfully",
        },
      });
      return {
        close: () => {
          onClose?.();
        },
      } as unknown as WebSocket;
    });

    const onRunEvent = vi.fn();
    const onCloseWithoutTerminalEvent = vi.fn();

    streamEditorChatResponse(
      {
        message_id: "msg-run",
        stream_channel_id: "run-123",
      },
      {
        onRunEvent,
        onCloseWithoutTerminalEvent,
      },
    );

    closeCallback?.();

    expect(onRunEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        event_type: "run_completed",
      }),
    );
    expect(onCloseWithoutTerminalEvent).not.toHaveBeenCalled();
  });
});
