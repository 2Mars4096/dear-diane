import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import { resolveContentBootstrapRoots } from "../../electron/contentBootstrap";

describe("resolveContentBootstrapRoots", () => {
  it("includes configured roots before heuristic fallbacks", () => {
    vi.stubEnv(
      "DAN_DEFAULT_CONTENT_ROOTS",
      [
        "~/kb-one",
        "/tmp/kb-two",
      ].join(path.delimiter),
    );

    const roots = resolveContentBootstrapRoots({
      cwd: "/Volumes/data/Dropbox/Projects/deep-agent-network/editor",
      appPath: "/Volumes/data/Dropbox/Projects/deep-agent-network/editor",
      homedir: "/Users/tester",
    });

    expect(roots[0]).toBe("/Users/tester/kb-one");
    expect(roots[1]).toBe("/tmp/kb-two");
    expect(roots).toContain("/Volumes/data/Dropbox/Projects/my-knowledge-base");
  });

  it("deduplicates overlapping heuristic candidates", () => {
    const roots = resolveContentBootstrapRoots({
      env: { DAN_DEFAULT_CONTENT_ROOTS: "" },
      cwd: "/Volumes/data/Dropbox/Projects/deep-agent-network",
      appPath: "/Volumes/data/Dropbox/Projects/deep-agent-network",
      homedir: "/Users/tester",
    });

    expect(roots).toEqual([
      "/Volumes/data/Dropbox/Projects/my-knowledge-base",
      "/Users/tester/Dropbox/Projects/my-knowledge-base",
      "/Users/tester/Projects/my-knowledge-base",
      "/Volumes/data/Dropbox/my-knowledge-base",
    ]);
  });
});
