import { scanLines } from "./scan-lines";
import { detectOcrSplit, mapOcrRegion, OCR_LAYOUT_VERSION, type OcrLayout } from "./ocr-layout";
// Runs tesseract.js in the browser against pages pdf.js renders, producing the same
// line spans learning-assistant's server OCR produced. Assets come from public/tesseract.
import type { PDFDocumentProxy } from "pdfjs-dist/types/src/pdf";
import type { MaterialPdfOcrPage, PdfOcrTextSpan } from "./pdf-ocr";
import { MAX_OCR_PAGES, OCR_RENDER_SCALE, parseTesseractTsv, shouldOcrPage } from "./ocr";

type Worker = { recognize: (image: HTMLCanvasElement, options?: object, output?: { tsv?: boolean }) => Promise<{ data: { tsv?: string } }>; terminate: () => Promise<unknown> };
let workerPromise: Promise<Worker> | null = null;

async function worker(): Promise<Worker> {
  if (!workerPromise) {
    workerPromise = (async () => {
      const tesseract = await import("tesseract.js");
      const base = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/tesseract`;
      const engine = await tesseract.createWorker("chi_sim+eng", 1, { workerPath: `${base}/worker.min.js`, corePath: base, langPath: base, gzip: true, logger: () => undefined });
      await engine.setParameters({ tessedit_pageseg_mode: tesseract.PSM.SINGLE_BLOCK });
      return engine as unknown as Worker;
    })().catch((error) => { workerPromise = null; throw error; });
  }
  return workerPromise;
}

/** Text pdf.js can extract for each page; pages below the word threshold need OCR. */
export async function extractPageTexts(document: PDFDocumentProxy): Promise<Map<number, string>> {
  const texts = new Map<number, string>();
  for (let number = 1; number <= document.numPages; number += 1) {
    try {
      const page = await document.getPage(number);
      const content = await page.getTextContent();
      texts.set(number, content.items.map((item) => ("str" in item ? item.str : "")).join(" ").replace(/\s+/g, " ").trim());
    } catch { texts.set(number, ""); }
  }
  return texts;
}

export function sparsePages(texts: Map<number, string>): number[] {
  return [...texts.entries()].filter(([, text]) => shouldOcrPage(text)).map(([number]) => number).slice(0, MAX_OCR_PAGES);
}

export async function ocrPage(document: PDFDocumentProxy, pageNumber: number, layout: OcrLayout = "auto"): Promise<MaterialPdfOcrPage> {
  const page = await document.getPage(pageNumber);
  const viewport = page.getViewport({ scale: OCR_RENDER_SCALE });
  const canvas = window.document.createElement("canvas");
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("Canvas unavailable for OCR");
  await page.render({ canvas, canvasContext: context, viewport }).promise;
  const sample = window.document.createElement("canvas");
  sample.width = 320; sample.height = Math.max(40, Math.round(320 * canvas.height / canvas.width));
  const sampleContext = sample.getContext("2d", { willReadFrequently: true });
  let split: number | null = layout === "spread" ? .5 : null;
  if (layout === "auto" && sampleContext) {
    sampleContext.drawImage(canvas, 0, 0, sample.width, sample.height);
    split = detectOcrSplit(sampleContext.getImageData(0, 0, sample.width, sample.height));
  }
  sample.width = sample.height = 1;
  const boundary = split === null ? canvas.width : Math.round(canvas.width * split);
  const regions = split === null ? [[0, canvas.width]] : [[0, boundary], [boundary, canvas.width - boundary]];
  const spans: PdfOcrTextSpan[] = [];
  try {
    const engine = await worker();
    // Process each printed page separately; preserve left-page then right-page order.
    for (const [left, width] of regions) {
      const crop = window.document.createElement("canvas"); crop.width = width; crop.height = canvas.height;
      const cropContext = crop.getContext("2d", { alpha: false });
      if (!cropContext) throw Error("Canvas unavailable for OCR region");
      try {
        cropContext.drawImage(canvas, left, 0, width, canvas.height, 0, 0, width, canvas.height);
        const result = await engine.recognize(crop, {}, { tsv: true });
        const detected = parseTesseractTsv(result.data.tsv ?? "", "word").spans;
        const geometry = window.document.createElement("canvas");
        geometry.width = Math.min(1000, width); geometry.height = Math.round(canvas.height * geometry.width / width);
        const geometryContext = geometry.getContext("2d", { willReadFrequently: true });
        let lines = detected;
        if (geometryContext) {
          geometryContext.drawImage(crop, 0, 0, geometry.width, geometry.height);
          lines = scanLines(geometryContext.getImageData(0, 0, geometry.width, geometry.height), detected);
        }
        geometry.width = geometry.height = 1;
        spans.push(...mapOcrRegion(lines, left / canvas.width, width / canvas.width));
      } finally { crop.width = crop.height = 1; }
    }
    return { page_number: pageNumber, spans, layout_version: OCR_LAYOUT_VERSION, layout_mode: layout, split };
  } finally { canvas.width = canvas.height = 1; }
}

export function ocrPageText(page: MaterialPdfOcrPage): string {
  return page.spans.filter(span => !span.geometryOnly).map((span) => span.text).join(" ").replace(/\s+/g, " ").trim();
}
