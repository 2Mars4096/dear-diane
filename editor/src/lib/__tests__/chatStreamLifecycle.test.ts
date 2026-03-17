import { describe, expect, it } from "vitest";

import {
  getStreamDisconnectError,
  getStreamReconnectDelayMs,
  isAssistantBubbleStreaming,
  shouldReconnectStream,
  shouldShowAssistantLoadingPlaceholder,
} from "../chatStreamLifecycle";

describe("chatStreamLifecycle", () => {
  it("treats run-streaming assistant bubbles as active", () => {
    expect(
      isAssistantBubbleStreaming({
        role: "assistant",
        isLastMessage: true,
        isStreaming: false,
        isRunStreaming: true,
      }),
    ).toBe(true);
  });

  it("keeps the loading placeholder visible before assistant text arrives", () => {
    expect(
      shouldShowAssistantLoadingPlaceholder({
        isUser: false,
        isStreaming: true,
        content: "",
        toolCallCount: 3,
      }),
    ).toBe(true);
  });

  it("retries transient run-stream disconnects while under the retry cap", () => {
    expect(
      shouldReconnectStream({
        closedIntentionally: false,
        activeChannelId: "run-123",
        channelId: "run-123",
        reconnectCount: 1,
        closeCode: 1006,
        hadTransportError: true,
      }),
    ).toBe(true);
  });

  it("stops retrying once the backend has forgotten the stream", () => {
    expect(
      shouldReconnectStream({
        closedIntentionally: false,
        activeChannelId: "chat-123",
        channelId: "chat-123",
        reconnectCount: 1,
        closeCode: 4004,
        hadTransportError: false,
      }),
    ).toBe(false);
  });

  it("does not reconnect after a clean close when a terminal event was received", () => {
    expect(
      shouldReconnectStream({
        closedIntentionally: false,
        activeChannelId: "chat-123",
        channelId: "chat-123",
        reconnectCount: 1,
        closeCode: 1000,
        hadTransportError: true,
        hadTerminalEvent: true,
      }),
    ).toBe(false);
  });

  it("reconnects after a premature clean close when no terminal event was received", () => {
    expect(
      shouldReconnectStream({
        closedIntentionally: false,
        activeChannelId: "chat-123",
        channelId: "chat-123",
        reconnectCount: 1,
        closeCode: 1000,
        hadTransportError: false,
        hadTerminalEvent: false,
      }),
    ).toBe(true);
  });

  it("backs off reconnect delays for longer local restarts", () => {
    expect(getStreamReconnectDelayMs(0)).toBe(500);
    expect(getStreamReconnectDelayMs(1)).toBe(1000);
    expect(getStreamReconnectDelayMs(4)).toBe(4000);
  });

  it("does not show a disconnect error for clean closes", () => {
    expect(
      getStreamDisconnectError({
        closedIntentionally: false,
        closeCode: 1000,
        hadTransportError: true,
        hadTerminalEvent: true,
        streamLabel: "Run stream",
      }),
    ).toBeNull();
  });

  it("shows a disconnect error for premature clean closes without a terminal event", () => {
    expect(
      getStreamDisconnectError({
        closedIntentionally: false,
        closeCode: 1000,
        hadTransportError: false,
        hadTerminalEvent: false,
      }),
    ).toContain("may be incomplete");
  });

  it("explains local backend restarts and mentions restored snapshots", () => {
    expect(
      getStreamDisconnectError({
        closedIntentionally: false,
        closeCode: 4004,
        hadTransportError: false,
        backendState: "restarted",
        restoredSnapshot: true,
      }),
    ).toContain("restored the latest saved partial response");
  });
});
