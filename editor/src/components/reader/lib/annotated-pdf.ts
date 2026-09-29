// Ported from learning-assistant lib/annotated-pdf.ts + annotated-pdf-core.ts.
// Writes comments as standard PDF highlight annotations, so they open in any PDF viewer.
import { PDFArray, PDFDocument, PDFHexString, PDFName, PDFString } from "pdf-lib";
import type { PaperComment, PdfSelectionRect } from "./paper-comments";

type PdfRect = [number, number, number, number];

function mapRectToPdf(
  rect: PdfSelectionRect,
  pageWidth: number,
  pageHeight: number,
  rotation: PaperComment["rotation"]
): PdfRect {
  const displayCorners: Array<[number, number]> = [
    [rect.left, rect.top],
    [rect.left + rect.width, rect.top],
    [rect.left, rect.top + rect.height],
    [rect.left + rect.width, rect.top + rect.height]
  ];
  const pdfCorners = displayCorners.map(([x, y]): [number, number] => {
    if (rotation === 90) return [y * pageWidth, x * pageHeight];
    if (rotation === 180) return [(1 - x) * pageWidth, y * pageHeight];
    if (rotation === 270) return [(1 - y) * pageWidth, (1 - x) * pageHeight];
    return [x * pageWidth, (1 - y) * pageHeight];
  });
  const xs = pdfCorners.map(([x]) => x);
  const ys = pdfCorners.map(([, y]) => y);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function pdfDate(isoDate: string): string {
  return `D:${isoDate.replace(/[-:T]/g, "").replace(/\.\d{3}Z$/, "Z")}`;
}

export async function addPaperCommentsToPdf(source: Uint8Array, comments: PaperComment[]): Promise<Uint8Array> {
  const document = await PDFDocument.load(source, { updateMetadata: false });
  const pages = document.getPages();

  comments.forEach((comment) => {
    const page = pages[comment.pageNumber - 1];
    if (!page) return;
    const rectangles = comment.rects.map((rect) => mapRectToPdf(rect, page.getWidth(), page.getHeight(), comment.rotation));
    if (!rectangles.length) return;
    const bounds: PdfRect = [
      Math.min(...rectangles.map(([x1]) => x1)),
      Math.min(...rectangles.map(([, y1]) => y1)),
      Math.max(...rectangles.map(([, , x2]) => x2)),
      Math.max(...rectangles.map(([, , , y2]) => y2))
    ];
    const quadPoints = rectangles.flatMap(([x1, y1, x2, y2]) => [x1, y2, x2, y2, x1, y1, x2, y1]);
    const annotation = document.context.obj({
      C: [0.96, 0.72, 0.2],
      CA: 0.32,
      Contents: PDFHexString.fromText(comment.text),
      F: 4,
      M: PDFString.of(pdfDate(comment.updatedAt)),
      NM: PDFString.of(comment.commentId),
      P: page.ref,
      QuadPoints: quadPoints,
      Rect: bounds,
      Subtype: "Highlight",
      T: PDFHexString.fromText("Diane"),
      Type: "Annot"
    });
    const annotationRef = document.context.register(annotation);
    let annotations = page.node.lookupMaybe(PDFName.of("Annots"), PDFArray);
    if (!annotations) {
      annotations = document.context.obj([]) as PDFArray;
      page.node.set(PDFName.of("Annots"), annotations);
    }
    annotations.push(annotationRef);
  });

  return document.save({ useObjectStreams: false });
}

export async function createAnnotatedPdf(sourceUrl: string, comments: PaperComment[]): Promise<Blob> {
  const response = await fetch(sourceUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error("The original PDF could not be loaded for export.");
  }
  const bytes = await addPaperCommentsToPdf(new Uint8Array(await response.arrayBuffer()), comments);
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return new Blob([buffer], { type: "application/pdf" });
}
