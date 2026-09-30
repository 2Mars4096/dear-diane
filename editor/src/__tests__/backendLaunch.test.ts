import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildBackendLaunchEnv,
  resolveBackendWorkspaceRoot,
} from "../../electron/backendLaunch";

describe("backendLaunch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("defaults the backend workspace root to the parent of the graphs dir", () => {
    const env = buildBackendLaunchEnv({
      env: {},
      graphsDir: path.join("/tmp", "dan-user", "graphs"),
    });

    expect(env.DAN_PERSONAL_ENABLED).toBe("1");
    expect(env.DAN_PERSONAL_MODEL).toBeUndefined();
    expect(buildBackendLaunchEnv({ env: { DAN_PERSONAL_ENABLED: "0" }, graphsDir: "/tmp/test" }).DAN_PERSONAL_ENABLED).toBe("0");
    expect(env.DAN_GRAPHS_DIR).toBe(path.join("/tmp", "dan-user", "graphs"));
    expect(env.DAN_WORKSPACE_ROOT).toBe(path.join("/tmp", "dan-user"));
  });

  it("preserves and normalizes an explicit workspace root", () => {
    vi.spyOn(os, "homedir").mockReturnValue("/Users/tester");

    const workspaceRoot = resolveBackendWorkspaceRoot({
      env: { DAN_WORKSPACE_ROOT: "~/Desktop/dan-workspace" },
      graphsDir: path.join("/tmp", "dan-user", "graphs"),
    });

    expect(workspaceRoot).toBe("/Users/tester/Desktop/dan-workspace");
  });
});
