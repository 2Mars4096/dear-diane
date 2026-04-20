import { describe, expect, it } from "vitest";

import {
  applyPreviewEditTarget,
  extractMarkdownEditableBlocks,
  findPreviewEditTarget,
  type PreviewEditableSelection,
} from "../contentModePreviewEditing";

describe("contentModePreviewEditing", () => {
  const sample = `---
title: "Preview Title"
pageID: "sample-page"
---

# Body Heading

First paragraph line
continues here.

- Alpha bullet
- Beta bullet

> Quoted line one
> Quoted line two
`;

  it("extracts common markdown blocks in document order", () => {
    const blocks = extractMarkdownEditableBlocks(sample);
    expect(blocks.map((block) => [block.kind, block.text])).toEqual([
      ["heading", "Body Heading"],
      ["paragraph", "First paragraph line continues here."],
      ["list_item", "Alpha bullet"],
      ["list_item", "Beta bullet"],
      ["blockquote", "Quoted line one\nQuoted line two"],
    ]);
  });

  it("maps preview heading selections to the frontmatter title when appropriate", () => {
    const selection: PreviewEditableSelection = {
      kind: "heading",
      text: "Preview Title",
      allIndex: 0,
      kindIndex: 0,
      tagName: "H1",
    };

    const target = findPreviewEditTarget(sample, selection);
    expect(target?.targetType).toBe("frontmatter-field");
    expect(target?.text).toBe("Preview Title");
  });

  it("matches paragraph and list selections back to markdown blocks", () => {
    const paragraph = findPreviewEditTarget(sample, {
      kind: "paragraph",
      text: "First paragraph line continues here.",
      allIndex: 1,
      kindIndex: 0,
      tagName: "P",
    });
    const listItem = findPreviewEditTarget(sample, {
      kind: "list_item",
      text: "Beta bullet",
      allIndex: 3,
      kindIndex: 1,
      tagName: "LI",
    });

    expect(paragraph?.targetType).toBe("markdown-block");
    expect(paragraph?.text).toBe("First paragraph line continues here.");
    expect(listItem?.targetType).toBe("markdown-block");
    expect(listItem?.text).toBe("Beta bullet");
  });

  it("applies frontmatter, paragraph, and blockquote edits back into markdown", () => {
    const titleTarget = findPreviewEditTarget(sample, {
      kind: "heading",
      text: "Preview Title",
      allIndex: 0,
      kindIndex: 0,
      tagName: "H1",
    });
    const paragraphTarget = findPreviewEditTarget(sample, {
      kind: "paragraph",
      text: "First paragraph line continues here.",
      allIndex: 1,
      kindIndex: 0,
      tagName: "P",
    });
    const quoteTarget = findPreviewEditTarget(sample, {
      kind: "blockquote",
      text: "Quoted line one Quoted line two",
      allIndex: 4,
      kindIndex: 0,
      tagName: "BLOCKQUOTE",
    });

    expect(titleTarget).not.toBeNull();
    expect(paragraphTarget).not.toBeNull();
    expect(quoteTarget).not.toBeNull();

    const retitled = applyPreviewEditTarget(
      sample,
      titleTarget!,
      "Retitled From Preview",
    );
    const reparagraphed = applyPreviewEditTarget(
      retitled,
      paragraphTarget!,
      "One clean paragraph from the preview surface.",
    );
    const requoted = applyPreviewEditTarget(
      reparagraphed,
      quoteTarget!,
      "Quote replacement\nSecond line",
    );

    expect(requoted).toContain('title: "Retitled From Preview"');
    expect(requoted).toContain("One clean paragraph from the preview surface.");
    expect(requoted).toContain("> Quote replacement\n> Second line");
  });
});
