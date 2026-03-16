import { describe, expect, it } from "vitest";

import { fileToAttachmentDraft, resolveAttachmentName } from "../editorChat";

describe("editorChat attachment helpers", () => {
  it("provides a fallback label for unnamed pasted images", () => {
    expect(resolveAttachmentName("", "image/png")).toBe("Pasted Image.png");
  });

  it("preserves explicit attachment names", () => {
    expect(resolveAttachmentName("diagram.png", "image/png")).toBe("diagram.png");
  });

  it("applies the fallback label when clipboard files have no name", () => {
    const file = new File(["pixels"], "", { type: "image/png" });
    expect(fileToAttachmentDraft(file).name).toBe("Pasted Image.png");
  });
});
