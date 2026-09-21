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
      return tesseract.createWorker("eng", 1, { workerPath: `${base}/worker.min.js`, corePath: base, langPath: base, gzip: true, logger: () => undefined }) as unknown as Promise<Worker>;
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

export async function ocrPage(document: PDFDocumentProxy, pageNumber: number): Promise<MaterialPdfOcrPage> {
  const page = await document.getPage(pageNumber);
  const viewport = page.getViewport({ scale: OCR_RENDER_SCALE });
  const canvas = window.document.createElement("canvas");
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("Canvas unavailable for OCR");
  await page.render({ canvas, canvasContext: context, viewport }).promise;
  const engine = await worker();
  const result = await engine.recognize(canvas, {}, { tsv: true });
  const { spans } = parseTesseractTsv(result.data.tsv ?? "");
  canvas.width = canvas.height = 1;
  return { page_number: pageNumber, spans: spans as PdfOcrTextSpan[] };
}

export function ocrPageText(page: MaterialPdfOcrPage): string {
  return page.spans.map((span) => span.text).join(" ").replace(/\s+/g, " ").trim();
}
