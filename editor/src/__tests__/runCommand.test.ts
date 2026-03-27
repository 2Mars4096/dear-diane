import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";

import { COMMAND_NOT_FOUND_EXIT_CODE, runCommand } from "../../electron/runCommand";

class FakeStream extends EventEmitter {
  emitData(value: string) {
    this.emit("data", Buffer.from(value));
  }
}

class FakeChildProcess extends EventEmitter {
  readonly stdout = new FakeStream() as unknown as NodeJS.ReadableStream;
  readonly stderr = new FakeStream() as unknown as NodeJS.ReadableStream;
  readonly kill = vi.fn(() => true);
}

describe("runCommand", () => {
  it("captures stdout/stderr and exit code on close", async () => {
    const child = new FakeChildProcess();

    const resultPromise = runCommand("git", ["status"], {
      cwd: "/tmp/project",
      timeoutMs: 1000,
      spawnFn: () => {
        queueMicrotask(() => {
          (child.stdout as unknown as FakeStream).emitData("main-output");
          (child.stderr as unknown as FakeStream).emitData("warn-output");
          child.emit("close", 0);
        });
        return child as any;
      },
    });

    await expect(resultPromise).resolves.toEqual({
      stdout: "main-output",
      stderr: "warn-output",
      code: 0,
    });
  });

  it("returns a structured missing-command result on spawn ENOENT", async () => {
    const child = new FakeChildProcess();

    const resultPromise = runCommand("git", ["status"], {
      cwd: "/tmp/project",
      timeoutMs: 1000,
      spawnFn: () => {
        queueMicrotask(() => {
          const err = Object.assign(new Error("spawn git ENOENT"), { code: "ENOENT" });
          child.emit("error", err);
        });
        return child as any;
      },
    });

    await expect(resultPromise).resolves.toMatchObject({
      stdout: "",
      code: COMMAND_NOT_FOUND_EXIT_CODE,
    });

    const result = await resultPromise;
    expect(result.stderr).toContain("git is not installed or not available");
    expect(result.stderr).toContain("Install git and restart DAN.");
  });
});
