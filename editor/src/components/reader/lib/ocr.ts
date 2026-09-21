// Ported from learning-assistant lib/material-pdf-extractor.ts (OCR parts). DAN runs
// tesseract.js in the browser against the page pdf.js already rendered, so scanned
// pages get a selectable text layer without any system dependency.
import type { PdfOcrTextSpan } from "./pdf-ocr";

export const OCR_SPARSE_WORD_THRESHOLD = 20;
export const MAX_OCR_PAGES = 40;
export const OCR_RENDER_SCALE = 2.4; // ≈ 170–200 dpi for typical page sizes

export function countWords(text: string): number {
  const words = text.trim().match(/\b[\p{L}\p{N}'-]+\b/gu);
  return words ? words.length : 0;
}

export function shouldOcrPage(text: string): boolean {
  return countWords(text) < OCR_SPARSE_WORD_THRESHOLD;
}

function normalizeWhitespace(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

export function normalizeOcrSpan(value: unknown): PdfOcrTextSpan | null {
  if (!value || typeof value !== "object") return null;
  const entry = value as Partial<PdfOcrTextSpan>;
  const text = typeof entry.text === "string" ? entry.text.replace(/\s+/g, " ").trim() : "";
  const numbers = [entry.left, entry.top, entry.width, entry.height];
  if (!text || numbers.some((number) => typeof number !== "number" || !Number.isFinite(number) || number < 0 || number > 1)) return null;
  const left = entry.left as number;
  const top = entry.top as number;
  const width = entry.width as number;
  const height = entry.height as number;
  if (width <= 0 || height <= 0 || left + width > 1.002 || top + height > 1.002) return null;
  return {
    confidence: typeof entry.confidence === "number" && Number.isFinite(entry.confidence) ? Math.min(1, Math.max(0, entry.confidence)) : null,
    height,
    left,
    text,
    top,
    width
  };
}

/** Group Tesseract TSV words into line spans with page-relative fractions. */
export function parseTesseractTsv(tsv: string): { spans: PdfOcrTextSpan[]; text: string } {
  const rows = tsv.trim().split(/\r?\n/);
  const hasHeader = rows[0]?.startsWith("level\t") ?? false;
  if (!hasHeader && !/^\d+\t/.test(rows[0] ?? "")) {
    return { spans: [], text: normalizeWhitespace(tsv) };
  }
  let pageWidth = 0;
  let pageHeight = 0;
  const lines = new Map<string, { bottom: number; confidenceTotal: number; left: number; right: number; top: number; words: string[] }>();
  rows.slice(hasHeader ? 1 : 0).forEach((row) => {
    const fields = row.split("\t");
    if (fields.length < 12) return;
    const [level, page, block, paragraph, line, , left, top, width, height, confidence] = fields.slice(0, 11).map(Number);
    if (level === 1) {
      pageWidth = width;
      pageHeight = height;
      return;
    }
    const word = fields.slice(11).join("\t").replace(/\s+/g, " ").trim();
    if (level !== 5 || !word || width <= 0 || height <= 0) return;
    const key = `${page}:${block}:${paragraph}:${line}`;
    const current = lines.get(key) ?? { bottom: top + height, confidenceTotal: 0, left, right: left + width, top, words: [] };
    current.left = Math.min(current.left, left);
    current.top = Math.min(current.top, top);
    current.right = Math.max(current.right, left + width);
    current.bottom = Math.max(current.bottom, top + height);
    current.confidenceTotal += Number.isFinite(confidence) ? Math.max(0, confidence) : 0;
    current.words.push(word);
    lines.set(key, current);
  });
  if (pageWidth <= 0 || pageHeight <= 0) return { spans: [], text: "" };
  const spans = [...lines.values()].flatMap((line) => {
    const span = normalizeOcrSpan({
      confidence: line.words.length > 0 ? line.confidenceTotal / line.words.length / 100 : null,
      height: (line.bottom - line.top) / pageHeight,
      left: line.left / pageWidth,
      text: line.words.join(" "),
      top: line.top / pageHeight,
      width: (line.right - line.left) / pageWidth
    });
    return span ? [span] : [];
  }).sort((left, right) => (Math.abs(left.top - right.top) > 0.01 ? left.top - right.top : left.left - right.left));
  return { spans, text: spans.map((span) => span.text).join("\n") };
}
