// @vitest-environment happy-dom

import React, { act, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  isElectronMock,
  nativeFsExistsMock,
  nativeFsReadFileMock,
  nativeTerminalCreateMock,
  nativeTerminalWriteMock,
} = vi.hoisted(() => ({
  isElectronMock: vi.fn(),
  nativeFsExistsMock: vi.fn(),
  nativeFsReadFileMock: vi.fn(),
  nativeTerminalCreateMock: vi.fn(),
  nativeTerminalWriteMock: vi.fn(),
}));

vi.mock("../../../lib/electronBridge", () => ({
  isElectron: isElectronMock,
  nativeFs: {
    exists: nativeFsExistsMock,
    readFile: nativeFsReadFileMock,
  },
  nativeTerminal: {
    create: nativeTerminalCreateMock,
    write: nativeTerminalWriteMock,
  },
}));

import { useCodeStore } from "../../../store/useCodeStore";
import type { AppMode } from "../../../store/useAppStore";
import type { ChatMessage, ToolCallInfo } from "../../../types/chat";
import {
  useModeChatSidebarNativeActions,
  type ModeChatSidebarNativeActions,
} from "../useModeChatSidebarNativeActions";

let latestAdapter: ModeChatSidebarNativeActions | null = null;
let latestMessages: ChatMessage[] = [];

function HookHarness({
  mode,
  initialMessages,
}: {
  mode: AppMode;
  initialMessages: ChatMessage[];
}) {
  const [messages, setMessages] = useState(initialMessages);
  const adapter = useModeChatSidebarNativeActions({ mode, setMessages });

  useEffect(() => {
    latestAdapter = adapter;
  }, [adapter]);

  useEffect(() => {
    latestMessages = messages;
  }, [messages]);

  return React.createElement("div", null, "native-actions-harness");
}

async function renderHarness(mode: AppMode, initialMessages: ChatMessage[]) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(HookHarness, { mode, initialMessages }));
  });
  return { root };
}

describe("useModeChatSidebarNativeActions", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.useFakeTimers();
    latestAdapter = null;
    latestMessages = [];
    isElectronMock.mockReset();
    nativeFsExistsMock.mockReset();
    nativeFsReadFileMock.mockReset();
    nativeTerminalCreateMock.mockReset();
    nativeTerminalWriteMock.mockReset();
    isElectronMock.mockReturnValue(true);
    nativeFsExistsMock.mockResolvedValue(true);
    nativeTerminalCreateMock.mockResolvedValue("term-1");
    nativeTerminalWriteMock.mockResolvedValue(undefined);
    useCodeStore.setState({
      pinnedRoots: ["/repo"],
      openFiles: [
        {
          path: "/repo/app.ts",
          content: "const before = true;\n",
          language: "typescript",
          dirty: false,
          originalContent: "const before = true;\n",
        },
      ],
      terminals: [],
      activeTerminalId: null,
      showTerminal: false,
      multiFileEdits: [],
      showMultiFileReview: false,
    });
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("keeps shell-run and file-review actions inside the development adapter", async () => {
    nativeFsReadFileMock.mockResolvedValue("const after = true;\n");

    const { root } = await renderHarness("development", [
      {
        id: "assistant-1",
        role: "assistant",
        content: "Updated the file.",
        timestamp: Date.now(),
      },
    ]);

    expect(latestAdapter?.allowRunCodeBlocks).toBe(true);

    await act(async () => {
      await latestAdapter?.onRunCodeBlock?.("npm test");
    });

    expect(useCodeStore.getState().showTerminal).toBe(true);
    expect(useCodeStore.getState().activeTerminalId).toBe("term-1");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(nativeTerminalWriteMock).toHaveBeenCalledWith("term-1", "npm test\n");

    const toolCall: ToolCallInfo = {
      id: "tool-1",
      toolName: "edit_file",
      argsPreview: '{"path":"/repo/app.ts"}',
      status: "running",
    };

    await act(async () => {
      latestAdapter?.onToolCallStart?.(toolCall);
      await Promise.resolve();
    });

    await act(async () => {
      await latestAdapter?.onToolCallResult?.("assistant-1", {
        ...toolCall,
        status: "success",
      });
    });

    expect(latestMessages[0]?.reviewableFileEdits).toEqual([
      {
        filePath: "/repo/app.ts",
        originalContent: "const before = true;\n",
        modifiedContent: "const after = true;\n",
        createdByThisTurn: false,
      },
    ]);

    await act(async () => {
      latestAdapter?.onReviewMultiFileEdits?.(
        latestMessages[0]?.reviewableFileEdits ?? [],
      );
    });

    expect(useCodeStore.getState().showMultiFileReview).toBe(true);
    expect(useCodeStore.getState().multiFileEdits).toHaveLength(1);

    await act(async () => {
      root.unmount();
    });
  });

  it("disables development-native affordances outside development mode", async () => {
    const { root } = await renderHarness("research", [
      {
        id: "assistant-1",
        role: "assistant",
        content: "```sh\necho hi\n```",
        timestamp: Date.now(),
      },
    ]);

    expect(latestAdapter?.allowRunCodeBlocks).toBe(false);
    expect(latestAdapter?.onRunCodeBlock).toBeUndefined();
    expect(latestAdapter?.onReviewMultiFileEdits).toBeUndefined();
    expect(latestAdapter?.onToolCallStart).toBeUndefined();
    expect(latestAdapter?.onToolCallResult).toBeUndefined();

    await act(async () => {
      root.unmount();
    });
  });
});
