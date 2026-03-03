import { describe, expect, it } from "vitest";

import type { RunSummary } from "../api";
import { resolveCompareSelection } from "../runHistory";

function makeRun(runId: string, graphId = "wf-1"): RunSummary {
  return {
    run_id: runId,
    graph_id: graphId,
    status: "completed",
    node_statuses: {},
    started_at: 1,
    finished_at: 2,
    success: true,
    errors: {},
    outputs: {},
    total_tokens: 0,
    total_cost: 0,
    elapsed_seconds: 1,
    node_usage: {},
  };
}

describe("resolveCompareSelection", () => {
  it("uses explicitly selected source run", () => {
    const runs = [makeRun("run-a"), makeRun("run-b"), makeRun("run-c")];
    const result = resolveCompareSelection(runs, "run-c", makeRun("run-b"));
    expect(result).not.toBeNull();
    expect(result!.runA.run_id).toBe("run-c");
    expect(result!.runB.run_id).toBe("run-b");
  });

  it("returns null when selecting the same run for both sides", () => {
    const runs = [makeRun("run-a"), makeRun("run-b")];
    const result = resolveCompareSelection(runs, "run-b", makeRun("run-b"));
    expect(result).toBeNull();
  });

  it("returns null when source run is missing", () => {
    const runs = [makeRun("run-a"), makeRun("run-b")];
    const result = resolveCompareSelection(runs, "run-x", makeRun("run-b"));
    expect(result).toBeNull();
  });
});
