import { describe, expect, it } from "vitest";

import {
  buildContentFolderTree,
  buildManagedPreviewUrl,
  buildContentPageSummary,
  extractFrontmatterSummary,
  getContentFolderAncestorPaths,
  inferPreviewPathFromRelPath,
  resolvePreviewPath,
  summarizeHeadings,
} from "../contentModeModel";

describe("contentModeModel", () => {
  it("extracts common frontmatter fields and preserves the markdown body", () => {
    const source = `---
title: "A Better Desk"
pageID: page-123
date: 2026-04-19
draft: true
slug: custom-desk
url: /custom/editorial-desk/
---

## Intro

Body copy.`;

    const summary = extractFrontmatterSummary(source);

    expect(summary.title).toBe("A Better Desk");
    expect(summary.pageId).toBe("page-123");
    expect(summary.date).toBe("2026-04-19");
    expect(summary.draft).toBe(true);
    expect(summary.slug).toBe("custom-desk");
    expect(summary.url).toBe("/custom/editorial-desk/");
    expect(summary.body).toContain("## Intro");
  });

  it("builds a page summary with a Hugo-style preview route and readable excerpt", () => {
    const page = buildContentPageSummary(
      "/kb/content",
      "/kb/content/posts/editorial-desk/index.md",
      `---
title: Editorial Desk
pageID: post-editorial-desk
---

Welcome to the [desk](/desk).

{{< callout >}}
Hidden chrome
{{< /callout >}}

<aside>Rendered elsewhere</aside>
`,
    );

    expect(page.relPath).toBe("posts/editorial-desk/index.md");
    expect(page.section).toBe("posts");
    expect(page.slug).toBe("editorial-desk");
    expect(page.previewPath).toBe("/posts/editorial-desk/");
    expect(page.pageId).toBe("post-editorial-desk");
    expect(page.excerpt).toContain("Welcome to the desk.");
    expect(page.excerpt).not.toContain("$1");
  });

  it("summarizes deeper headings and derives preview routes for flat pages", () => {
    expect(inferPreviewPathFromRelPath("notes/weekly-review.md")).toBe(
      "/notes/weekly-review/",
    );
    expect(
      resolvePreviewPath("posts/editorial-desk/index.md", { slug: "field-notes", url: null }),
    ).toBe("/posts/field-notes/");
    expect(
      resolvePreviewPath("notes/weekly-review.md", {
        slug: null,
        url: "https://example.com/custom/review/",
      }),
    ).toBe("/custom/review/");
    expect(
      buildManagedPreviewUrl(
        "http://127.0.0.1:1313/",
        "/posts/editorial-desk/?preview=1#top",
      ),
    ).toBe("http://127.0.0.1:1313/posts/editorial-desk/?preview=1#top");
    expect(
      buildManagedPreviewUrl(
        "http://127.0.0.1:1313/",
        "/posts/editorial-desk/?preview=1#top",
        { embedded: true },
      ),
    ).toBe(
      "http://127.0.0.1:1313/posts/editorial-desk/?preview=1&dan_preview=1#top",
    );
    expect(
      buildManagedPreviewUrl("http://127.0.0.1:1313/", "https://evil.test/"),
    ).toBeNull();
    expect(
      summarizeHeadings("# Title\n## Section\n### Subsection\nText"),
    ).toEqual([
      { depth: 2, text: "Section" },
      { depth: 3, text: "Subsection" },
    ]);
  });

  it("builds a nested folder tree for tiered knowledge bases", () => {
    const pages = [
      buildContentPageSummary(
        "/kb/content",
        "/kb/content/blogs/reviews/possession/index.md",
        "---\ntitle: Possession\n---\nA review.",
      ),
      buildContentPageSummary(
        "/kb/content",
        "/kb/content/blogs/reviews/stalker/index.md",
        "---\ntitle: Stalker\n---\nAnother review.",
      ),
      buildContentPageSummary(
        "/kb/content",
        "/kb/content/blogs/notes/field-log.md",
        "---\ntitle: Field Log\n---\nNotes.",
      ),
      buildContentPageSummary(
        "/kb/content",
        "/kb/content/inbox.md",
        "---\ntitle: Inbox\n---\nRoot page.",
      ),
    ];

    const tree = buildContentFolderTree(pages);

    expect(tree.pages.map((page) => page.relPath)).toEqual(["inbox.md"]);
    expect(tree.folders.map((folder) => folder.relPath)).toEqual(["blogs"]);
    expect(tree.folders[0]?.pageCount).toBe(3);
    expect(tree.folders[0]?.folders.map((folder) => folder.relPath)).toEqual([
      "blogs/notes",
      "blogs/reviews",
    ]);
    expect(
      tree.folders[0]?.folders[1]?.folders[0]?.pages[0]?.relPath,
    ).toBe("blogs/reviews/possession/index.md");
    expect(
      getContentFolderAncestorPaths("blogs/reviews/possession/index.md"),
    ).toEqual(["blogs", "blogs/reviews", "blogs/reviews/possession"]);
  });
});
