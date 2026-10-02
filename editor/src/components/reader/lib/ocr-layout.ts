import type { MaterialPdfOcrPage, PdfOcrTextSpan } from './pdf-ocr';
export const OCR_LAYOUT_VERSION = 2;
export type OcrLayout = 'auto' | 'single' | 'spread';

/** Find a central low-ink gutter. Ignore isolated binding lines and scan speckles. */
export function detectOcrSplit(image: Pick<ImageData, 'data' | 'width' | 'height'>): number | null {
  const { data, width, height } = image;
  if (width < 40 || height < 40) return null;
  const density = Array.from({ length: width }, (_, x) => {
    let ink = 0, samples = 0;
    for (let y = Math.floor(height * .08); y < height * .92; y += 2) {
      const i = (y * width + x) * 4;
      if (data[i + 3] > 100 && (data[i] + data[i + 1] + data[i + 2]) / 3 < 170) ink++;
      samples++;
    }
    return ink / Math.max(1, samples);
  });
  const average = (from: number, to: number) => { const part = density.slice(Math.floor(width * from), Math.ceil(width * to)); return part.reduce((a, b) => a + b, 0) / part.length; };
  const sideInk = Math.min(average(.1, .4), average(.6, .9));
  if (sideInk < .015) return null;
  const radius = Math.max(2, Math.round(width * .012));
  let best = .5, score = Infinity, ink = Infinity;
  for (let x = Math.floor(width * .42); x <= width * .58; x++) {
    const band = density.slice(x - radius, x + radius + 1).sort((a, b) => a - b);
    const keep = Math.max(1, Math.floor(band.length * .8));
    const mean = band.slice(0, keep).reduce((a, b) => a + b, 0) / keep;
    const value = mean + Math.abs(x / width - .5) * .02;
    if (value < score) { best = x / width; score = value; ink = mean; }
  }
  return ink < sideInk * .22 ? best : null;
}
export function mapOcrRegion(spans: PdfOcrTextSpan[], left: number, width: number): PdfOcrTextSpan[] {
  return spans.map(span => ({ ...span, left: left + span.left * width, width: span.width * width }));
}
export function validOcrLayout(value: unknown): OcrLayout { return value === 'single' || value === 'spread' ? value : 'auto'; }

/** Page-wide caches from the previous recognizer must never restore cross-gutter spans. */
export function currentOcrPages(value: unknown, layout: OcrLayout): MaterialPdfOcrPage[] {
  if (!Array.isArray(value)) return [];
  return value.filter((page): page is MaterialPdfOcrPage => page && page.layout_version === OCR_LAYOUT_VERSION
    && page.layout_mode === layout && Number.isInteger(page.page_number) && page.page_number > 0 && Array.isArray(page.spans));
}
