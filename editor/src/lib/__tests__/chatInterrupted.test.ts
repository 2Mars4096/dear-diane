import { describe, expect, it } from "vitest";

import { formatInterruptedAssistantContent } from "../chatInterrupted";

describe("chatInterrupted", () => {
  it("keeps tool-only interrupted turns textless so the fallback UI can render", () => {
    expect(
      formatInterruptedAssistantContent(
        {
          content: "",
          toolCalls: [
            {
              id: "tool-1",
              toolName: "file_grep",
              argsPreview: '{"pattern":"chat_tool_call_result"}',
              status: "success",
            },
          ],
          attachments: undefined,
          runEvents: undefined,
        },
        "",
      ),
    ).toBe("");
  });

  it("preserves real interrupted text and appends the stop marker", () => {
    expect(
      formatInterruptedAssistantContent(
        {
          content: "Partial answer",
          toolCalls: undefined,
          attachments: undefined,
          runEvents: undefined,
        },
        "",
      ),
    ).toBe("Partial answer\n\n*[generation stopped]*");
  });
});
