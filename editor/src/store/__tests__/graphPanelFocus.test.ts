// @vitest-environment happy-dom

import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "../useAppStore";
import { useGraphStore } from "../useGraphStore";

describe("graph panel focus actions", () => {
  beforeEach(() => {
    window.location.hash = "#chat";
    useAppStore.setState({ activeMode: "chat" });
    useGraphStore.setState({
      logFocusCounter: 0,
      historyFocusCounter: 0,
      historyFocusRunId: null,
    });
  });

  it("switches to operations mode when focusing logs", () => {
    useGraphStore.getState().focusLogPanel();

    expect(useAppStore.getState().activeMode).toBe("operations");
    expect(useGraphStore.getState().logFocusCounter).toBe(1);
  });

  it("switches to operations mode and stores the run id when focusing history", () => {
    useGraphStore.getState().focusHistoryPanel("run-123");

    expect(useAppStore.getState().activeMode).toBe("operations");
    expect(useGraphStore.getState().historyFocusCounter).toBe(1);
    expect(useGraphStore.getState().historyFocusRunId).toBe("run-123");
  });
});
