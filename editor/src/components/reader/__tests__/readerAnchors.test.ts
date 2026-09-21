import { describe, expect, it } from "vitest";
import { createTextAnchor, fingerprintText, locateVisualSelection, resolveTextAnchor } from "../lib/paper-anchors";
import { countWords, parseTesseractTsv, shouldOcrPage } from "../lib/ocr";
import { normalizePaperComments } from "../lib/paper-comments";
import { addPaperCommentsToPdf } from "../lib/annotated-pdf";
import { PDFDocument, PDFName } from "pdf-lib";

describe("text anchors", () => {
  const page = "Alpha beta gamma delta. The scheduler chooses the next action. Epsilon zeta.";
  it("fingerprints normalized text as sha256", () => {
    expect(fingerprintText("a  b")).toBe(fingerprintText("a b"));
    expect(fingerprintText("abc")).toBe("sha256-ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
  it("locates a visual selection ignoring whitespace and rejects ambiguous quotes", () => {
    const located = locateVisualSelection(page, "scheduler  chooses");
    expect(located.status).toBe("exact");
    if (located.status === "exact") expect(located.selection).toMatchObject({ quote: "scheduler chooses", prefix: "Alpha beta gamma delta. The " });
    expect(locateVisualSelection("x y x y", "x").status).toBe("missing");
  });
  it("re-resolves an anchor after the surrounding text changes", () => {
    const anchor = createTextAnchor(page, 28, 45)!;
    expect(anchor.quote).toBe("scheduler chooses");
    expect(resolveTextAnchor(page, anchor).status).toBe("exact");
    const moved = resolveTextAnchor("NEW INTRO. " + page, anchor);
    expect(moved.status).toBe("review");
    expect(resolveTextAnchor("Completely different text.", anchor).status).toBe("missing");
  });
  it("keeps anchors on saved comments", () => {
    const anchor = { ...createTextAnchor(page, 28, 45)!, pageFingerprint: fingerprintText(page) };
    const [saved] = normalizePaperComments([{ commentId: "c", createdAt: "2026-09-21T00:00:00Z", updatedAt: "2026-09-21T00:00:00Z", pageNumber: 1, quote: "scheduler chooses", rects: [{ left: 0.1, top: 0.1, width: 0.2, height: 0.02 }], rotation: 0, text: "", anchor }], "m");
    expect(saved.anchor).toMatchObject({ quote: "scheduler chooses", pageFingerprint: anchor.pageFingerprint });
  });
});

describe("ocr", () => {
  it("decides which pages need OCR by word count", () => {
    expect(countWords("one two three")).toBe(3);
    expect(shouldOcrPage("")).toBe(true);
    expect(shouldOcrPage("word ".repeat(25))).toBe(false);
  });
  it("groups tesseract tsv words into line spans", () => {
    const tsv = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
      "1\t1\t0\t0\t0\t0\t0\t0\t1000\t2000\t-1\t", "5\t1\t1\t1\t1\t1\t100\t100\t80\t20\t90\tHello", "5\t1\t1\t1\t1\t2\t190\t100\t80\t20\t80\tworld",
      "5\t1\t1\t1\t2\t1\t100\t150\t50\t20\t70\tNext"].join("\n");
    const { spans, text } = parseTesseractTsv(tsv);
    expect(text).toBe("Hello world\nNext");
    expect(spans[0]).toMatchObject({ text: "Hello world", left: 0.1, top: 0.05, width: 0.17, height: 0.01 });
    expect(spans[0].confidence).toBeCloseTo(0.85);
  });
});

it("exports comments as PDF highlight annotations", async () => {
  const source = await PDFDocument.create();
  source.addPage([600, 800]);
  const bytes = await source.save();
  const out = await addPaperCommentsToPdf(bytes, [{ commentId: "c1", createdAt: "2026-09-21T00:00:00.000Z", updatedAt: "2026-09-21T00:00:00.000Z", materialId: "m", pageNumber: 1, quote: "q", rects: [{ left: 0.1, top: 0.1, width: 0.5, height: 0.05 }], rotation: 0, text: "note" }]);
  const doc = await PDFDocument.load(out);
  const annots = doc.getPage(0).node.lookupMaybe(PDFName.of("Annots"), (await import("pdf-lib")).PDFArray);
  expect(annots?.size()).toBe(1);
});
