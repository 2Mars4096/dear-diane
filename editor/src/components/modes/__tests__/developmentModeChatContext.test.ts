import { describe, expect, it } from "vitest";

import { buildDevelopmentModeChatContext } from "../developmentModeChatContext";

describe("buildDevelopmentModeChatContext", () => {
  it("builds a compact workspace summary around the active file", () => {
    const context = buildDevelopmentModeChatContext({
      activeFilePath: "/repo/src/app.ts",
      currentBranch: "feature/ui-hardening",
      openFiles: [
        {
          path: "/repo/src/app.ts",
          language: "typescript",
          content: "line-1\nline-2\nline-3",
        },
        {
          path: "/repo/README.md",
          language: "markdown",
          content: "docs",
        },
      ],
      pinnedRoots: ["/repo"],
    });

    expect(context).toContain("[Workspace Context]");
    expect(context).toContain("Git branch: feature/ui-hardening");
    expect(context).toContain("Active file: /repo/src/app.ts (typescript, 3 lines)");
    expect(context).toContain("Open files: app.ts, README.md");
    expect(context).toContain("Workspace roots: /repo");
    expect(context).toContain("[Active File Content (first 200 lines)]");
    expect(context).toContain("line-1\nline-2\nline-3");
  });
});
