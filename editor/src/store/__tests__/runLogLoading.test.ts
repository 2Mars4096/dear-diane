// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from "vitest";

const { getRunMock, getRunEventsMock } = vi.hoisted(() => ({
  getRunMock: vi.fn(),
  getRunEventsMock: vi.fn(),
}));

vi.mock("../../lib/api", () => ({
  getRun: getRunMock,
  getRunEvents: getRunEventsMock,
}));

import { useAppStore } from "../useAppStore";
import { useGraphStore } from "../useGraphStore";

describe("run log loading", () => {
  beforeEach(() => {
    getRunMock.mockReset();
    getRunEventsMock.mockReset();
    useAppStore.setState({ activeMode: "chat", notifications: [], unreadCount: 0 });
    useGraphStore.setState({
      runId: null,
      runStatus: null,
      nodeStatuses: {},
      nodeOutputs: {},
      nodeTimings: {},
      nodeUsage: {},
      nodeCosts: {},
      nodeTiers: {},
      activeExecutionPath: new Set(),
      logs: [],
      runSummary: null,
      logFocusCounter: 0,
      addToast: vi.fn(),
    });
  });

  it("hydrates the log panel from a historical run", async () => {
    getRunMock.mockResolvedValue({
      run_id: "run-123",
      graph_id: "wf-1",
      status: "failed",
      node_statuses: { exception: "node_failed" },
      started_at: 1,
      finished_at: 2,
      success: false,
      errors: { exception: "Missing required input 'watchlist_path'" },
      outputs: {},
      total_prompt_tokens: 10,
      total_completion_tokens: 5,
      total_tokens: 15,
      elapsed_seconds: 0.2,
    });
    getRunEventsMock.mockResolvedValue({
      source: "persisted",
      events: [
        {
          event_type: "run_started",
          run_id: "run-123",
          timestamp: 1,
          data: {},
        },
        {
          event_type: "node_failed",
          run_id: "run-123",
          node_id: "exception",
          timestamp: 1.1,
          data: { error: "Missing required input 'watchlist_path'" },
        },
        {
          event_type: "run_failed",
          run_id: "run-123",
          timestamp: 1.2,
          data: { errors: { exception: "Missing required input 'watchlist_path'" } },
        },
      ],
    });

    await useGraphStore.getState().loadRunLogs("run-123");

    expect(useAppStore.getState().activeMode).toBe("operations");
    expect(useGraphStore.getState().logFocusCounter).toBe(1);
    expect(useGraphStore.getState().runId).toBe("run-123");
    expect(useGraphStore.getState().runStatus).toBe("failed");
    expect(useGraphStore.getState().nodeStatuses.exception).toBe("node_failed");
    expect(useGraphStore.getState().logs.map((entry) => entry.event_type)).toEqual([
      "run_started",
      "node_failed",
      "run_failed",
    ]);
    expect(useGraphStore.getState().runSummary?.total_tokens).toBe(15);
  });

  it("does not emit live notifications when hydrating historical lint failures", async () => {
    getRunMock.mockResolvedValue({
      run_id: "run-lint-history",
      graph_id: "wf-1",
      status: "failed",
      node_statuses: { source: "node_failed" },
      started_at: 1,
      finished_at: 2,
      success: false,
      errors: { source: "Lint blocked the handoff" },
      outputs: {},
      total_prompt_tokens: 10,
      total_completion_tokens: 5,
      total_tokens: 15,
      elapsed_seconds: 0.2,
    });
    getRunEventsMock.mockResolvedValue({
      source: "persisted",
      events: [
        {
          event_type: "lint_failed",
          run_id: "run-lint-history",
          node_id: "source",
          timestamp: 1.1,
          data: {
            edge_id: "e1",
            source_port: "result",
            target_node_id: "reviewer",
            target_port: "draft",
            severity: "error",
            handoff_committed: false,
            attempt: 1,
            diagnostics: [{ message: "Missing summary" }],
          },
        },
        {
          event_type: "run_failed",
          run_id: "run-lint-history",
          timestamp: 1.2,
          data: { errors: { source: "Lint blocked the handoff" } },
        },
      ],
    });

    await useGraphStore.getState().loadRunLogs("run-lint-history");

    expect(useGraphStore.getState().logs.map((entry) => entry.event_type)).toEqual([
      "lint_failed",
      "run_failed",
    ]);
    expect(useAppStore.getState().notifications).toEqual([]);
  });
});
