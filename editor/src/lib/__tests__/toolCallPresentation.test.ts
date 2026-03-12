import { describe, expect, it } from "vitest";

import { groupToolCallsForDisplay } from "../toolCallPresentation";

describe("toolCallPresentation", () => {
  it("collapses repeated file reads of the same file", () => {
    const items = groupToolCallsForDisplay([
      {
        id: "a",
        toolName: "file_read",
        argsPreview:
          '{"path":"/tmp/report.tex","start_line":1,"end_line":20}',
        status: "success",
        outputPreview: "first chunk",
        durationMs: 120,
      },
      {
        id: "b",
        toolName: "file_read",
        argsPreview:
          '{"path":"/tmp/report.tex","start_line":30,"end_line":60}',
        status: "success",
        outputPreview: "second chunk",
        durationMs: 80,
      },
      {
        id: "c",
        toolName: "web_search",
        argsPreview: '{"query":"report"}',
        status: "success",
        outputPreview: "done",
        durationMs: 50,
      },
    ]);

    expect(items).toHaveLength(2);
    expect(items[0].fileReadGroup).toMatchObject({
      path: "/tmp/report.tex",
      label: "report.tex",
      readCount: 2,
      summaries: ["L1-20", "L30-60"],
    });
    expect(items[0].toolCall.durationMs).toBe(200);
    expect(items[1].toolCall.toolName).toBe("web_search");
  });

  it("leaves unrelated file reads separate", () => {
    const items = groupToolCallsForDisplay([
      {
        id: "a",
        toolName: "file_read",
        argsPreview: '{"path":"/tmp/a.py"}',
        status: "success",
      },
      {
        id: "b",
        toolName: "file_read",
        argsPreview: '{"path":"/tmp/b.py"}',
        status: "success",
      },
    ]);

    expect(items).toHaveLength(2);
    expect(items[0].fileReadGroup?.path).toBe("/tmp/a.py");
    expect(items[1].fileReadGroup?.path).toBe("/tmp/b.py");
  });
});
