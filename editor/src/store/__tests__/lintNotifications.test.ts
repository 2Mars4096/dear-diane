// @vitest-environment happy-dom

import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "../useAppStore";
import { useGraphStore } from "../useGraphStore";

describe("live lint notifications", () => {
  beforeEach(() => {
    window.location.hash = "#chat";
    useAppStore.setState({
      activeMode: "chat",
      notifications: [],
      unreadCount: 0,
    });
    useGraphStore.setState({
      runId: "run-live-lint",
      runStatus: "running",
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
      nodeIterations: {},
      streamingOutputs: {},
      pendingHumanInput: null,
    });
  });

  it("emits a cross-mode notification for blocking lint failures and deep-links to logs", () => {
    useGraphStore.getState().handleRunEvent({
      event_type: "lint_failed",
      run_id: "run-live-lint",
      node_id: "source",
      timestamp: 10,
      data: {
        edge_id: "e1",
        source_port: "result",
        target_node_id: "reviewer",
        target_port: "draft",
        severity: "error",
        handoff_committed: false,
        attempt: 1,
        diagnostics: [{ message: "Missing summary field" }],
      },
    });

    const notification = useAppStore.getState().notifications[0];
    expect(notification).toBeTruthy();
    expect(notification.type).toBe("error");
    expect(notification.source).toBe("run");
    expect(notification.title).toContain("Lint blocked handoff");
    expect(notification.message).toContain("result -> reviewer.draft");
    expect(notification.message).toContain("Missing summary field");

    notification.action?.callback();

    expect(useAppStore.getState().activeMode).toBe("operations");
    expect(useGraphStore.getState().logFocusCounter).toBe(1);
  });
});
