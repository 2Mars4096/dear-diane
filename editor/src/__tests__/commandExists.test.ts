import { describe, expect, it } from "vitest";

import { commandExists } from "../../electron/commandExists";

function _spawnResult(status: number): { status: number } {
  return { status };
}

describe("commandExists", () => {
  it("uses which on unix-like platforms", () => {
    const calls: Array<{ command: string; args?: readonly string[] }> = [];
    const result = commandExists("gh", {
      platform: "linux",
      spawn: (command, args) => {
        calls.push({ command, args });
        return _spawnResult(0) as any;
      },
    });

    expect(result).toBe(true);
    expect(calls).toEqual([{ command: "which", args: ["gh"] }]);
  });

  it("retries with .exe on windows when needed", () => {
    const calls: Array<{ command: string; args?: readonly string[] }> = [];
    const result = commandExists("gh", {
      platform: "win32",
      spawn: (command, args) => {
        calls.push({ command, args });
        return _spawnResult(calls.length === 1 ? 1 : 0) as any;
      },
    });

    expect(result).toBe(true);
    expect(calls).toEqual([
      { command: "where", args: ["gh"] },
      { command: "where", args: ["gh.exe"] },
    ]);
  });

  it("does not append .exe twice on windows", () => {
    const calls: Array<{ command: string; args?: readonly string[] }> = [];
    const result = commandExists("gh.exe", {
      platform: "win32",
      spawn: (command, args) => {
        calls.push({ command, args });
        return _spawnResult(1) as any;
      },
    });

    expect(result).toBe(false);
    expect(calls).toEqual([{ command: "where", args: ["gh.exe"] }]);
  });

  it("returns false when the probe throws", () => {
    const result = commandExists("gh", {
      platform: "linux",
      spawn: () => {
        throw new Error("boom");
      },
    });

    expect(result).toBe(false);
  });
});
