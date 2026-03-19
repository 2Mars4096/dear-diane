import { describe, expect, it } from "vitest";

import type { RunInfo } from "../api";
import {
  mapRawRunEventToChatPayload,
  mergeRunEventPayloads,
  recoverTerminalRunStateFromSnapshot,
} from "../runStreamRecovery";

const FAILED_RUN_INFO: RunInfo = {
  run_id: "run-123",
  graph_id: "_scratch",
  status: "failed",
  node_statuses: { node_news_tool: "failed" },
  started_at: 1,
  finished_at: 2,
  success: false,
  errors: {
    node_news_tool: "Tool 'web_search' failed: missing query",
  },
  outputs: {},
};

describe("runStreamRecovery", () => {
  it("maps raw node failures into chat payloads", () => {
    expect(
      mapRawRunEventToChatPayload(
        {
          event_type: "node_failed",
          run_id: "run-123",
          node_id: "node_final_report",
          error: "Unexpected error calling LLM",
          data: { error: "Unexpected error calling LLM" },
        },
        "full",
      ),
    ).toEqual({
      type: "run_event",
      event_type: "node_failed",
      node_id: "node_final_report",
      summary: "Node 'node_final_report' failed: Unexpected error calling LLM",
      detail: {
        run_id: "run-123",
        scope: "full",
        target: null,
        data: { error: "Unexpected error calling LLM" },
      },
    });
  });

  it("synthesizes node and terminal failures when only run info exists", () => {
    const recovered = recoverTerminalRunStateFromSnapshot({
      runInfo: FAILED_RUN_INFO,
      rawEvents: [],
      scope: "full",
    });

    expect(recovered).toEqual({
      status: "failed",
      runEvents: [
        {
          type: "run_event",
          event_type: "node_failed",
          node_id: "node_news_tool",
          summary: "Node 'node_news_tool' failed: Tool 'web_search' failed: missing query",
          detail: {
            run_id: "run-123",
            scope: "full",
            target: null,
            data: { error: "Tool 'web_search' failed: missing query" },
          },
        },
        {
          type: "run_event",
          event_type: "run_failed",
          summary: "Run failed: Tool 'web_search' failed: missing query",
          detail: {
            run_id: "run-123",
            scope: "full",
            target: null,
            data: { error: "Tool 'web_search' failed: missing query" },
          },
        },
      ],
    });
  });

  it("deduplicates recovered run events against live ones", () => {
    const event = {
      type: "run_event" as const,
      event_type: "run_cancelled",
      summary: "Run cancelled: user_cancelled",
      detail: {
        run_id: "run-123",
        scope: "full",
        target: null,
        data: { reason: "user_cancelled" },
      },
    };

    expect(mergeRunEventPayloads([event], [event])).toEqual([event]);
  });
});
