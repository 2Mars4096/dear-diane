import { describe, expect, it } from "vitest";
import { readPaperComments, writePaperComments, type PaperComment } from "../lib/paper-comments";
import { paperHighlightDisplayPath } from "../lib/paper-highlight-display";
import { movePaperReferenceTag, type PaperReferenceTag } from "../lib/paper-reference-tags";
import { readPaperPdfPosition, writePaperPdfPosition } from "../lib/paper-reading-position";

const memory = () => {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => void values.set(key, value), removeItem: (key: string) => void values.delete(key), values };
};

describe("ported reader libraries", () => {
  it("round-trips comments per PDF under Diane keys", () => {
    const storage = memory();
    const material = { material_id: "/project/paper.pdf" };
    const comment: PaperComment = { commentId: "c1", createdAt: "2026-09-19T00:00:00Z", updatedAt: "2026-09-19T00:00:00Z", materialId: material.material_id,
      pageNumber: 2, quote: "a passage", rects: [{ left: 0.1, top: 0.2, width: 0.3, height: 0.02 }], rotation: 0, text: "note" };
    writePaperComments(material, [comment], storage);
    expect([...storage.values.keys()][0]).toMatch(/^dan\.reader:paper-comments:/);
    expect(readPaperComments(material, storage)).toMatchObject([{ commentId: "c1", pageNumber: 2, text: "note" }]);
    expect(readPaperComments({ material_id: "/project/other.pdf" }, storage)).toEqual([]);
  });
  it("remembers and validates the reading position", () => {
    const storage = memory();
    writePaperPdfPosition("paper", { page: 3, top: 0.4, left: 0, zoom: 1.2 }, storage);
    expect(readPaperPdfPosition("paper", storage)).toEqual({ page: 3, top: 0.4, left: 0, zoom: 1.2 });
    storage.setItem("dan.reader:pdf-position:v1:bad", JSON.stringify({ page: 0, top: 0, left: 0, zoom: 9 }));
    expect(readPaperPdfPosition("bad", storage)).toBeNull();
  });
  it("moves reference tags between lines and before a target", () => {
    const tag = (tagId: string, lineId: string): PaperReferenceTag => ({ tagId, lineId, label: tagId, pageNumber: 1, materialId: "m", createdAt: "" });
    const tags = [tag("a", "saved"), tag("b", "saved"), tag("c", "other")];
    expect(movePaperReferenceTag(tags, "a", "other", null).map((item) => `${item.tagId}:${item.lineId}`)).toEqual(["b:saved", "c:other", "a:other"]);
    expect(movePaperReferenceTag(tags, "b", "saved", "a").map((item) => item.tagId)).toEqual(["b", "a", "c"]);
  });
  it("draws highlight paths in page percentages", () => {
    expect(paperHighlightDisplayPath([{ left: 0.1, top: 0.2, width: 0.3, height: 0.02 }])).toMatch(/^M\d/);
  });
});
