import { describe, expect, it } from "vitest";

import { buildContentModeChatContext } from "../contentModeChatContext";

describe("buildContentModeChatContext", () => {
  it("summarizes the active project, page, and draft excerpt", () => {
    const draft = Array.from({ length: 205 }, (_, index) => `line-${index + 1}`).join("\n");

    const context = buildContentModeChatContext({
      workspacePaths: ["/repo", "/notes"],
      activeProjectRoot: "/repo/kb",
      activePage: {
        filePath: "/repo/kb/content/posts/editorial-desk/index.md",
        relPath: "posts/editorial-desk/index.md",
        title: "Editorial Desk",
        pageId: "page-9",
        section: "posts",
        slug: "editorial-desk",
        draft: false,
        wordCount: 420,
        excerpt: "Editorial excerpt",
        previewPath: "/posts/editorial-desk/",
      },
      activeDraft: draft,
      isDirty: true,
    });

    expect(context).toContain("[Content Workspace Context]");
    expect(context).toContain("Workspace paths: /repo, /notes");
    expect(context).toContain("Active content project: /repo/kb");
    expect(context).toContain("Active page: /repo/kb/content/posts/editorial-desk/index.md");
    expect(context).toContain("Preview route: /posts/editorial-desk/");
    expect(context).toContain("Draft state: dirty local draft");
    expect(context).toContain("pageID: page-9");
    expect(context).toContain("[Active Draft (first 200 lines)]");
    expect(context).toContain("line-200");
    expect(context).not.toContain("line-201");
  });
});
