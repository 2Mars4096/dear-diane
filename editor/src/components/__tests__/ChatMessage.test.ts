// @vitest-environment happy-dom

import React from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../types/chat";
import { useGraphStore } from "../../store/useGraphStore";
import ChatMessageBubble from "../ChatMessage";

describe("ChatMessageBubble", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    useGraphStore.setState({
      addToast: vi.fn(),
      openTab: vi.fn(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("preserves mention syntax inside fenced code blocks", () => {
    const message: ChatMessage = {
      id: "msg-1",
      role: "assistant",
      content: "```sh\n@[foo](file:bar)\n```",
      timestamp: Date.now(),
    };

    const html = renderToStaticMarkup(
      React.createElement(ChatMessageBubble, {
        message,
        allowRunCodeBlocks: true,
      }),
    );

    expect(html).toContain("data-code-block");
    expect(html).toContain("@[foo](file:bar)");
    expect(html).not.toContain("data-mention-type=\"file\"");
  });

  it("promotes a distilled workflow from the assistant message id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "promoted_workflow",
        workflow_id: "distilled-climate-grants",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const addToast = vi.fn();
    const openTab = vi.fn().mockResolvedValue(undefined);
    useGraphStore.setState({
      addToast,
      openTab,
    });

    const message: ChatMessage = {
      id: "turn-promote-1",
      role: "assistant",
      content: "Finished the research and wrote the deliverable.",
      timestamp: Date.now(),
      toolCalls: [
        {
          id: "tool-1",
          toolName: "search_web",
          argsPreview: "{\"query\":\"climate grants\"}",
          status: "success",
        },
        {
          id: "tool-2",
          toolName: "write_file",
          argsPreview: "{\"path\":\"/tmp/grants.md\"}",
          status: "success",
        },
      ],
    };

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(React.createElement(ChatMessageBubble, { message }));
    });

    const saveButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Save distilled workflow"),
    );
    expect(saveButton).toBeTruthy();

    await act(async () => {
      saveButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/experiences/trace-draft/promote",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ turn_id: "turn-promote-1" }),
      }),
    );
    expect(container.textContent).toContain('Saved as "distilled-climate-grants".');
    expect(container.textContent).toContain("Open workflow");
    expect(addToast).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "success",
        message: 'Saved distilled workflow "distilled-climate-grants"',
      }),
    );

    const openButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Open workflow"),
    );
    expect(openButton).toBeTruthy();

    await act(async () => {
      openButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(openTab).toHaveBeenCalledWith("distilled-climate-grants");

    await act(async () => {
      root.unmount();
    });
  });
});
