import { describe, expect, it } from "vitest";
import { leadActivity, resultLine, stripWorkers, taskTitle, workerState, type TeamWorker } from "../teamPresentation";

const worker = (id: string, status: string, extra: Partial<TeamWorker> = {}): TeamWorker => ({ worker_id: id, parent_run_id: "run", backend: "codex", status, prompt: "Check dates", response: "", error: "", native_session_id: "", ...extra });

describe("team progress presentation", () => {
  it("names a task by its first sentence", () => {
    expect(taskTitle("Compare availability across the three venues. Then list prices.")).toBe("Compare availability across the three venues");
    expect(taskTitle("## Review findings\nwith details")).toBe("Review findings");
  });
  it("shows the live action for running work and a plain state otherwise", () => {
    expect(workerState(worker("a", "running", { activity: "Running npm test" }))).toBe("running npm test");
    expect(workerState(worker("a", "needs_input"))).toBe("needs your input");
    expect(workerState(worker("a", "completed"))).toBe("done");
  });
  it("prioritises attention, keeps start order, and counts overflow", () => {
    const rows = [worker("a", "running"), worker("b", "completed"), worker("c", "running"), worker("d", "failed"), worker("e", "running")];
    const strip = stripWorkers(rows);
    expect(strip.shown.map((row) => row.worker_id)).toEqual(["d", "a"]);
    expect(strip).toMatchObject({ more: 2, settled: 1 });
  });
  it("derives the lead's action from the team instead of a permanent placeholder", () => {
    expect(leadActivity([worker("a", "running")], true)).toBe("coordinating");
    expect(leadActivity([worker("a", "completed")], true)).toBe("combining results");
    expect(leadActivity([worker("a", "running")], false)).toBe("");
  });
  it("settles a finished lane to its first line of output", () => {
    expect(resultLine(worker("a", "completed", { response: "\n## Two venues are free on Friday\nMore" }))).toBe("Two venues are free on Friday");
    expect(resultLine(worker("a", "failed", { response: "partial", error: "Login required" }))).toBe("Login required");
  });
});
