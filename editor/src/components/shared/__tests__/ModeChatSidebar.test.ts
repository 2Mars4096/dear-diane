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
import {
  buildLegacyModeChatStorageKey,
  buildModeChatStorageKey,
} from "../modeChatSidebarSession";
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

async function renderSidebar(mode: "development" | "research" = "development") {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const onClose = vi.fn();
  useAppStore.setState({ activeMode: mode });

  await act(async () => {
    root.render(
      React.createElement(ModeChatSidebar, {
        mode,
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
      buildLegacyModeChatStorageKey("ws-test", "development"),
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

  it("switches compact draft state when the active workflow changes", async () => {
    localStorage.setItem(
      buildModeChatStorageKey("ws-test", "development", "workflow-alpha"),
      JSON.stringify({
        threadId: "thread-alpha",
        chatMode: "agent",
        messages: [
          {
            id: "alpha-user",
            role: "user",
            content: "Alpha workflow draft",
            timestamp: Date.now(),
          },
        ],
      }),
    );
    localStorage.setItem(
      buildModeChatStorageKey("ws-test", "development", "workflow-beta"),
      JSON.stringify({
        threadId: "thread-beta",
        chatMode: "plan",
        messages: [
          {
            id: "beta-user",
            role: "user",
            content: "Beta workflow draft",
            timestamp: Date.now() + 1,
          },
        ],
      }),
    );
    useGraphStore.setState({ graphId: "workflow-alpha" });

    const { container, root } = await renderSidebar();

    expect(listChatThreadsMock).toHaveBeenCalledWith("workflow-alpha");
    expect(container.textContent).toContain("Alpha workflow draft");
    expect(container.textContent).not.toContain("Beta workflow draft");

    listChatThreadsMock.mockClear();

    await act(async () => {
      useGraphStore.setState({ graphId: "workflow-beta" });
      await Promise.resolve();
    });

    expect(listChatThreadsMock).toHaveBeenCalledWith("workflow-beta");
    expect(container.textContent).toContain("Beta workflow draft");
    expect(container.textContent).not.toContain("Alpha workflow draft");
    expect(container.textContent).toContain("workflow-beta");

    await act(async () => {
      root.unmount();
    });
  });

  it("restores the workspace-scoped compact session when the active workspace changes", async () => {
    localStorage.setItem(
      buildModeChatStorageKey("ws-test", "development", "_scratch"),
      JSON.stringify({
        threadId: "thread-workspace-a",
        chatMode: "agent",
        messages: [
          {
            id: "workspace-a-user",
            role: "user",
            content: "Workspace A draft",
            timestamp: Date.now(),
          },
        ],
      }),
    );
    localStorage.setItem(
      buildModeChatStorageKey("ws-second", "development", "_scratch"),
      JSON.stringify({
        threadId: "thread-workspace-b",
        chatMode: "plan",
        messages: [
          {
            id: "workspace-b-user",
            role: "user",
            content: "Workspace B draft",
            timestamp: Date.now() + 1,
          },
        ],
      }),
    );
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
        {
          id: "ws-second",
          name: "Workspace 2",
          pinnedPaths: [],
          lastActiveMode: "development",
          openThreadIds: [],
          activeThreadId: null,
          createdAt: 1,
          lastAccessedAt: 1,
        },
      ],
      activeWorkspaceId: "ws-test",
    });

    const { container, root } = await renderSidebar();

    expect(container.textContent).toContain("Workspace A draft");
    expect(container.textContent).not.toContain("Workspace B draft");

    await act(async () => {
      useWorkspaceStore.setState({ activeWorkspaceId: "ws-second" });
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Workspace B draft");
    expect(container.textContent).not.toContain("Workspace A draft");

    await act(async () => {
      root.unmount();
    });
  });

  it("only exposes shell run controls for the development sidecar", async () => {
    const storedSession = JSON.stringify({
      threadId: "thread-code",
      chatMode: "agent",
      messages: [
        {
          id: "assistant-1",
          role: "assistant",
          content: "```sh\necho compact-run\n```",
          timestamp: Date.now(),
        },
      ],
    });
    localStorage.setItem(
      buildModeChatStorageKey("ws-test", "development", "_scratch"),
      storedSession,
    );
    localStorage.setItem(
      buildModeChatStorageKey("ws-test", "research", "_scratch"),
      storedSession,
    );

    const developmentSidebar = await renderSidebar("development");
    expect(
      developmentSidebar.container.querySelector("[data-run-code]"),
    ).not.toBeNull();

    await act(async () => {
      developmentSidebar.root.unmount();
    });

    const researchSidebar = await renderSidebar("research");
    expect(
      researchSidebar.container.querySelector("[data-run-code]"),
    ).toBeNull();

    await act(async () => {
      researchSidebar.root.unmount();
    });
  });

  it("hands the current compact conversation off into full chat", async () => {
    localStorage.setItem(
      buildModeChatStorageKey("ws-test", "development", "_scratch"),
      JSON.stringify({
        threadId: null,
        chatMode: "agent",
        messages: [
          {
            id: "user-1",
            role: "user",
            content: "Carry this thread into full chat",
            timestamp: Date.now(),
          },
        ],
      }),
    );
    createChatThreadMock.mockResolvedValue({
      id: "thread-full",
      created_at: "2026-04-02T00:00:00.000Z",
      updated_at: "2026-04-02T00:00:00.000Z",
    });

    const { container, root, onClose } = await renderSidebar("development");

    await act(async () => {
      (
        container.querySelector(
          'button[title="Open full Chat mode"]',
        ) as HTMLButtonElement
      ).click();
      await Promise.resolve();
    });

    expect(createChatThreadMock).toHaveBeenCalledWith(
      "_scratch",
      expect.objectContaining({
        mode: "agent",
      }),
    );
    expect(updateChatThreadMock).toHaveBeenCalledWith(
      "_scratch",
      "thread-full",
      expect.objectContaining({
        mode: "agent",
        messages: [
          expect.objectContaining({
            role: "user",
            content: "Carry this thread into full chat",
          }),
        ],
      }),
    );
    expect(useAppStore.getState().activeMode).toBe("chat");
    expect(useAppStore.getState().activeChatThreadId).toBe("thread-full");
    expect(useAppStore.getState().activeChatWorkflowId).toBe("_scratch");
    expect(
      useWorkspaceStore
        .getState()
        .workspaces.find((workspace) => workspace.id === "ws-test")?.activeThreadId,
    ).toBe("thread-full");
    expect(onClose).toHaveBeenCalled();

    await act(async () => {
      root.unmount();
    });
  });
});
