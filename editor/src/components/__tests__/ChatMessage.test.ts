import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../../types/chat";
import ChatMessageBubble from "../ChatMessage";

describe("ChatMessageBubble", () => {
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
});
