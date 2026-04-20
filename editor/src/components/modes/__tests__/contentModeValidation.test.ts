import { describe, expect, it } from "vitest";

import { validateContentDraft } from "../contentModeValidation";

describe("validateContentDraft", () => {
  it("flags missing closing frontmatter delimiters", () => {
    const issues = validateContentDraft({
      filePath: "/repo/content/posts/test.md",
      relPath: "posts/test.md",
      content: "---\ntitle: Broken\npageID: page-1\n",
    });

    expect(issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "frontmatter:missing-close",
          severity: "error",
        }),
      ]),
    );
  });

  it("detects malformed yaml-like frontmatter lines and duplicate page ids", () => {
    const issues = validateContentDraft({
      filePath: "/repo/content/posts/test.md",
      relPath: "posts/test.md",
      content: `---
title Broken
pageID: shared-page
---

Body`,
      knownPages: [
        {
          filePath: "/repo/content/notes/other.md",
          pageId: "shared-page",
        },
      ],
    });

    expect(issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          category: "frontmatter",
          severity: "error",
        }),
        expect.objectContaining({
          id: "knowledge-base:duplicate-pageid:shared-page",
          severity: "warning",
        }),
      ]),
    );
  });

  it("stays quiet for a normal markdown page", () => {
    const issues = validateContentDraft({
      filePath: "/repo/content/posts/editorial-desk/index.md",
      relPath: "posts/editorial-desk/index.md",
      content: `---
title: Editorial Desk
pageID: page-editorial-desk
draft: false
---

## Intro

Body copy.`,
      knownPages: [],
    });

    expect(issues).toEqual([]);
  });

  it("warns when a page is missing a stable pageID", () => {
    const issues = validateContentDraft({
      filePath: "/repo/content/posts/untagged.md",
      relPath: "posts/untagged.md",
      content: `---
title: Untagged Draft
---

Body copy.`,
      knownPages: [],
    });

    expect(issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "knowledge-base:missing-pageid",
          severity: "warning",
        }),
      ]),
    );
  });
});
