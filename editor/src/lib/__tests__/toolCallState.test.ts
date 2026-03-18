import { describe, expect, it } from "vitest";

import { upsertToolCallResult, upsertToolCallStart } from "../toolCallState";

describe("toolCallState", () => {
  it("backfills a missing result-only tool call", () => {
    const next = upsertToolCallResult(undefined, {
      id: "tool-1",
      toolName: "list_directory",
      argsPreview: '{"path":"/tmp/project"}',
      status: "success",
      outputPreview: "done",
      durationMs: 18,
    });

    expect(next).toEqual([
      {
        id: "tool-1",
        toolName: "list_directory",
        argsPreview: '{"path":"/tmp/project"}',
        status: "success",
        outputPreview: "done",
        durationMs: 18,
      },
    ]);
  });

  it("does not regress a completed tool back to running", () => {
    const withResultFirst = upsertToolCallResult(undefined, {
      id: "tool-2",
      toolName: "file_read",
      argsPreview: '{"path":"/tmp/report.md"}',
      status: "success",
      outputPreview: "read",
      durationMs: 9,
    });

    const next = upsertToolCallStart(withResultFirst, {
      id: "tool-2",
      toolName: "file_read",
      argsPreview: '{"path":"/tmp/report.md"}',
    });

    expect(next).toEqual([
      {
        id: "tool-2",
        toolName: "file_read",
        argsPreview: '{"path":"/tmp/report.md"}',
        status: "success",
        outputPreview: "read",
        durationMs: 9,
      },
    ]);
  });
});
