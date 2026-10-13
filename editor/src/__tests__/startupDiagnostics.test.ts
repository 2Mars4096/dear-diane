import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, it, vi } from "vitest";
import { reportStartupFailure, startupDiagnostics } from "../../electron/startupDiagnostics";

it("keeps startup diagnostics bounded and starts a fresh launch receipt", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "diane-startup-"));
  try {
    const log = startupDiagnostics(root);
    log.record("x".repeat(40_000)); log.record("backend exited");
    expect(fs.readFileSync(log.file, "utf8").length).toBeLessThanOrEqual(32_768);
    expect(fs.readFileSync(log.file, "utf8")).toContain("backend exited");
    startupDiagnostics(root).record("new launch");
    expect(fs.readFileSync(log.file, "utf8")).not.toContain("backend exited");
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

it("returns control while the error dialog is open, then quits on dismissal", async () => {
  let dismiss!: () => void;
  const show = vi.fn(() => new Promise<void>(resolve => { dismiss = resolve; }));
  const quit = vi.fn();
  const pending = reportStartupFailure(show, quit, Error("backend exited"), "/tmp/startup.log");
  expect(show).toHaveBeenCalledWith(expect.objectContaining({ buttons: ["Quit"], detail: expect.stringContaining("/tmp/startup.log") }));
  expect(quit).not.toHaveBeenCalled();
  dismiss(); await pending;
  expect(quit).toHaveBeenCalledOnce();
});
