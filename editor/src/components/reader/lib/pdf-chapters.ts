import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/pdf';
import type { MaterialPdfOcrPage } from './pdf-ocr';

export type ReaderChapter = { id: string; label: string; page: number; depth: number; detected?: boolean };

/** Resolve nested PDF bookmarks, retaining children of non-clickable headings. */
export async function pdfOutline(document: PDFDocumentProxy): Promise<ReaderChapter[]> {
  const outline = await document.getOutline();
  const chapters: ReaderChapter[] = [];
  async function visit(items: NonNullable<typeof outline>, depth: number) {
    for (const item of items) {
      try {
        const destination = typeof item.dest === 'string' ? await document.getDestination(item.dest) : item.dest;
        if (Array.isArray(destination) && destination[0] != null) {
          const index = typeof destination[0] === 'number' ? destination[0] : await document.getPageIndex(destination[0]);
          if (Number.isInteger(index) && index >= 0 && index < document.numPages && item.title.trim()) {
            chapters.push({ id: `outline-${chapters.length}`, label: item.title.trim(), page: index + 1, depth });
          }
        }
      } catch { /* A broken bookmark must not discard usable siblings. */ }
      if (item.items?.length) await visit(item.items, depth + 1);
    }
  }
  await visit(outline || [], 0);
  return chapters;
}

/** Conservative local fallback: explicit chapter headings, never invented page groups. */
export function detectPdfChapters(texts: Map<number, string>, ocr: MaterialPdfOcrPage[] = []): ReaderChapter[] {
  const pages = new Map(texts);
  for (const page of ocr) {
    if (/\p{L}/u.test(pages.get(page.page_number) || '')) continue;
    const lines: { top: number; text: string }[] = [];
    for (const span of page.spans.filter(s => !s.geometryOnly)) {
      const line = lines.at(-1);
      if (line && Math.abs(line.top - span.top) < Math.min(span.height * .5, .012)) line.text += ` ${span.text}`;
      else lines.push({ top: span.top, text: span.text });
    }
    pages.set(page.page_number, lines.map(line => line.text).join('\n'));
  }
  const found: ReaderChapter[] = [];
  const seen = new Set<string>();
  for (const [page, text] of [...pages].sort((a, b) => a[0] - b[0])) {
    const lines = text.split(/\n+/).map(line => line.trim()).filter(Boolean);
    // Contents pages commonly contain several chapter labels and page-number leaders.
    if (lines.slice(0, 5).some(line => /^(?:table of )?contents$|^目\s*录$/i.test(line))) continue;
    const candidates = lines.slice(0, 8).filter(line => line.length <= 120 && !/\.{2,}|…|\s\d+\s*$/.test(line.replace(/^(?:chapter|part)\s+\S+$/i, '')) && /^(?:(?:chapter|part)\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b|第[零〇一二三四五六七八九十百千万\d]+[章节篇部]|(?:introduction|preface|prologue|epilogue|conclusion|appendix)(?:\s+[A-Z])?$)/i.test(line));
    if (candidates.length !== 1) continue;
    const label = candidates[0];
    const identity = label.toLowerCase().replace(/\s+/g, ' ');
    if (seen.has(identity)) continue;
    seen.add(identity);
    found.push({ id: `detected-${page}`, label, page, depth: 0, detected: true });
  }
  return found;
}
