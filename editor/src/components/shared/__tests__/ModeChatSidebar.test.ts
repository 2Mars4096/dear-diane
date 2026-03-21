// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  startEditorChatMock,
  streamEditorChatResponseMock,
  getChatThreadMock,
  listChatThreadsMock,
  createChatThreadMock,
  updateChatThreadMock,
} = vi.hoisted(() => ({
  startEditorChatMock: vi.fn(),
  streamEditorChatResponseMock: vi.fn(),
  getChatThreadMock: vi.fn(),
  listChatThreadsMock: vi.fn(),
  createChatThreadMock: vi.fn(),
  updateChatThreadMock: vi.fn(),
}));

vi.mock("../../../lib/editorChat", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/editorChat")>(
    "../../../lib/editorChat",
  );
  return {
    ...actual,
    startEditorChat: startEditorChatMock,
    streamEditorChatResponse: streamEditorChatResponseMock,
  };
});

vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>(
    "../../../lib/api",
  );
  return {
    ...actual,
    getChatThread: getChatThreadMock,
    listChatThreads: listChatThreadsMock,
    createChatThread: createChatThreadMock,
    updateChatThread: updateChatThreadMock,
  };
});

vi.mock("../modeChatSidebarState", async () => {
  const actual = await vi.importActual<typeof import("../modeChatSidebarState")>(
    "../modeChatSidebarState",
  );
  return {
    ...actual,
    applyAssistantToolCallResult: vi.fn(actual.applyAssistantToolCallResult),
    shouldStopSidebarThreadSnapshotPolling: vi.fn(
      actual.shouldStopSidebarThreadSnapshotPolling,
    ),
  };
});

import ModeChatSidebar, { sendToModeChat } from "../ModeChatSidebar";
import * as modeChatSidebarState from "../modeChatSidebarState";
import { useAppStore } from "../../../store/useAppStore";
import { useCodeStore } from "../../../store/useCodeStore";
import { useGraphStore } from "../../../store/useGraphStore";
import { useWorkspaceStore } from "../../../store/useWorkspaceStore";

function resetStores() {
  useAppStore.setState({
    activeMode: "development",
    activeChatThreadId: null,
    activeChatWorkflowId: null,
    globalPaletteVisible: false,
    notifications: [],
    unreadCount: 0,
  });
  useWorkspaceStore.setState({
    workspaces: [
      {
        id: "ws-test",
        name: "Workspace 1",
        pinnedPaths: [],
        lastActiveMode: "development",
        openThreadIds: [],
        activeThreadId: null,
        createdAt: 0,
        lastAccessedAt: 0,
      },
    ],
    activeWorkspaceId: "ws-test",
  });
  useCodeStore.setState({
    pinnedRoots: [],
    openFiles: [],
    activeFilePath: null,
    multiFileEdits: [],
    showMultiFileReview: false,
  });
  useGraphStore.setState({ graphId: null });
}

async function renderSidebar() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const onClose = vi.fn();

  await act(async () => {
    root.render(
      React.createElement(ModeChatSidebar, {
        mode: "development",
        onClose,
      }),
    );
  });

  return { container, root, onClose };
}

describe("ModeChatSidebar", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    localStorage.clear();
    if (!document.doctype) {
      document.insertBefore(
        document.implementation.createDocumentType("html", "", ""),
        document.documentElement,
      );
    }
    resetStores();
    listChatThreadsMock.mockResolvedValue({ threads: [] });
    createChatThreadMock.mockResolvedValue({
      id: "branch-thread",
      created_at: "2026-03-19T00:00:00.000Z",
      updated_at: "2026-03-19T00:00:00.000Z",
    });
    updateChatThreadMock.mockResolvedValue({ status: "ok" });
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal(
      "requestAnimationFrame",
      ((callback: FrameRequestCallback) =>
        setTimeout(() => callback(0), 0)) as unknown as typeof requestAnimationFrame,
    );
    vi.stubGlobal(
      "cancelAnimationFrame",
      ((handle: number) => clearTimeout(handle)) as typeof cancelAnimationFrame,
    );
  });

  afterEach(async () => {
    await vi.runOnlyPendingTimersAsync();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    localStorage.clear();
    document.body.innerHTML = "";
  });

  it("renders streamed tool output and stops fallback polling after the first 404", async () => {
    startEditorChatMock.mockResolvedValue({
      threadId: "thread-test",
      response: {
        message_id: "message-test",
        stream_channel_id: "chat-test",
      },
    });
    streamEditorChatResponseMock.mockImplementation(
      (
        _response: unknown,
        handlers: {
          onToolCallResult?: (toolCall: {
            id: string;
            toolName: string;
            argsPreview: string;
            status: string;
            outputPreview?: string;
            durationMs?: number;
          }) => void;
        },
      ) => {
        handlers.onToolCallResult?.({
          id: "tool-1",
          toolName: "web_search",
          argsPreview: '{"query":"component test"}',
          status: "success",
          outputPreview: "ok",
          durationMs: 11,
        });
        return { close: vi.fn() } as unknown as WebSocket;
      },
    );
    getChatThreadMock.mockRejectedValue(new Error("404: Thread not found"));

    const { container, root } = await renderSidebar();
    await act(async () => {
      sendToModeChat("development", "Run the compact sidebar test");
      await Promise.resolve();
    });

    expect(startEditorChatMock).toHaveBeenCalledTimes(1);
    expect(streamEditorChatResponseMock).toHaveBeenCalledTimes(1);
    expect(
      vi.mocked(modeChatSidebarState.applyAssistantToolCallResult),
    ).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("Run the compact sidebar test");
    expect(container.textContent).toContain("web_search");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    expect(getChatThreadMock).toHaveBeenCalledTimes(1);
    expect(
      vi.mocked(modeChatSidebarState.shouldStopSidebarThreadSnapshotPolling),
    ).toHaveBeenCalledTimes(1);

    await act(async () => {
      root.unmount();
    });
  });

  it("uses the active graph workflow instead of forcing _scratch", async () => {
    useGraphStore.setState({ graphId: "daily_equity_research" });
    startEditorChatMock.mockResolvedValue({
      threadId: "thread-test",
      response: {
        message_id: "message-test",
        stream_channel_id: "chat-test",
      },
    });
    streamEditorChatResponseMock.mockImplementation(
      (_response: unknown, handlers: { onComplete?: (content: string, event: Record<string, unknown>) => void }) => {
        handlers.onComplete?.("done", { type: "chat_complete", content: "done" });
        return { close: vi.fn() } as unknown as WebSocket;
      },
    );

    const { root } = await renderSidebar();

    expect(listChatThreadsMock).toHaveBeenCalledWith("daily_equity_research");

    await act(async () => {
      sendToModeChat("development", "Build daily equity research workflow");
      await Promise.resolve();
    });

    expect(startEditorChatMock).toHaveBeenCalledWith(
      expect.objectContaining({
        workflowId: "daily_equity_research",
      }),
    );

    await act(async () => {
      root.unmount();
    });
  });

  it("shows history entries and loads a selected sidebar thread", async () => {
    listChatThreadsMock.mockResolvedValue({
      threads: [
        {
          id: "thread-a",
          title: "First thread",
          workflow_id: "_scratch",
          message_count: 2,
          created_at: "2026-03-19T00:00:00.000Z",
          updated_at: "2026-03-19T00:00:00.000Z",
        },
      ],
    });
    getChatThreadMock.mockResolvedValue({
      title: "First thread",
      mode: "agent",
      messages: [
        {
          id: "msg-1",
          role: "user",
          content: "hello",
          timestamp: "2026-03-19T00:00:00.000Z",
        },
        {
          id: "msg-2",
          role: "assistant",
          content: "loaded from history",
          timestamp: "2026-03-19T00:00:01.000Z",
        },
      ],
    });

    const { container, root } = await renderSidebar();

    await act(async () => {
      (
        container.querySelector('button[title="Chat history"]') as HTMLButtonElement
      ).click();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("First thread");

    await act(async () => {
      (
        Array.from(container.querySelectorAll("button")).find((button) =>
          button.textContent?.includes("First thread"),
        ) as HTMLButtonElement
      ).click();
      await Promise.resolve();
    });

    expect(getChatThreadMock).toHaveBeenCalledWith("_scratch", "thread-a");
    expect(container.textContent).toContain("loaded from history");

    await act(async () => {
      root.unmount();
    });
  });

  it("renders edit and regenerate history actions for existing messages", async () => {
    localStorage.setItem(
      "dan-chat-ws-test-development",
      JSON.stringify({
        threadId: "thread-a",
        chatMode: "agent",
        messages: [
          {
            id: "user-1",
            role: "user",
            content: "Original request",
            timestamp: Date.now(),
          },
          {
            id: "assistant-1",
            role: "assistant",
            content: "Assistant answer",
            timestamp: Date.now() + 1,
          },
        ],
      }),
    );

    const { container, root } = await renderSidebar();

    expect(
      container.querySelector('button[title="Edit and resend in new branch"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('button[title="Regenerate in new branch"]'),
    ).not.toBeNull();

    await act(async () => {
      (
        container.querySelector(
          'button[title="Edit and resend in new branch"]',
        ) as HTMLButtonElement
      ).click();
      await Promise.resolve();
    });

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea.value).toContain("Original request");

    await act(async () => {
      root.unmount();
    });
  });
});
