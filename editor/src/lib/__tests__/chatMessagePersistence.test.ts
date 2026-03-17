import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../../types/chat";
import {
  fromBackendMessage,
  toBackendMessage,
} from "../chatMessagePersistence";

describe("chatMessagePersistence", () => {
  it("preserves attachments across backend serialization", () => {
    const message: ChatMessage = {
      id: "msg-1",
      role: "assistant",
      content: "Saved the report to disk.",
      timestamp: Date.parse("2026-03-12T08:30:00.000Z"),
      estimatedCost: 1.25,
      runRef: {
        runId: "run-1",
        scope: "node",
        status: "completed",
        targetNodeId: "save_report",
        targetSubgraphKey: "draft_loop",
      },
      toolCalls: [
        {
          id: "call-1",
          toolName: "file_read",
          argsPreview: '{"path":"/tmp/report.tex"}',
          status: "success",
          outputPreview: "Loaded report.tex",
          durationMs: 87,
        },
      ],
      runEvents: [
        {
          type: "node_completed",
          event_type: "node_completed",
          summary: "Completed save_report",
        },
      ],
      attachments: [
        {
          path: "/tmp/report.tex",
          filename: "report.tex",
          size: 2048,
          mimeType: "application/x-tex",
          kind: "file",
        },
      ],
    };

    const payload = toBackendMessage(message);
    expect(payload.estimated_cost).toBe(1.25);
    expect(payload.run_ref).toEqual({
      run_id: "run-1",
      scope: "node",
      status: "completed",
      target_node_id: "save_report",
      target_subgraph_key: "draft_loop",
    });
    expect(payload.tool_calls).toEqual([
      {
        id: "call-1",
        tool_name: "file_read",
        args_preview: '{"path":"/tmp/report.tex"}',
        status: "success",
        output_preview: "Loaded report.tex",
        duration_ms: 87,
      },
    ]);
    expect(payload.run_events).toEqual(message.runEvents);
    expect(payload.attachments).toEqual(message.attachments);

    const roundTripped = fromBackendMessage({
      ...payload,
      timestamp: "2026-03-12T08:30:00.000Z",
    });

    expect(roundTripped.toolCalls).toEqual(message.toolCalls);
    expect(roundTripped.runEvents).toEqual(message.runEvents);
    expect(roundTripped.attachments).toEqual(message.attachments);
    expect(roundTripped.estimatedCost).toBe(1.25);
    expect(roundTripped.runRef).toEqual(message.runRef);
  });

  it("falls back for invalid timestamps and missing tool call ids", () => {
    const roundTripped = fromBackendMessage({
      id: "msg-2",
      role: "assistant",
      content: "Still working",
      timestamp: "not-a-date",
      tool_calls: [
        {
          tool_name: "file_read",
          args_preview: '{"path":"/tmp/report.tex"}',
          status: "running",
        },
      ],
    });

    expect(Number.isFinite(roundTripped.timestamp)).toBe(true);
    expect(roundTripped.toolCalls?.[0]?.id).toBe("tool-call-0");
  });
});
