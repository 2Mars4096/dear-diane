import type { PdfOcrTextSpan } from './pdf-ocr';

/** Recover selectable horizontal lines from ink, independently of OCR language.
 * Call separately for each printed page so a gutter never joins two lines. */
export function scanLines(image: Pick<ImageData, 'width' | 'height' | 'data'>, recognized: PdfOcrTextSpan[]): PdfOcrTextSpan[] {
  const { width, height, data } = image;
  const ink = new Uint8Array(width * height), columns = new Uint32Array(width);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4;
    if (data[i + 3] > 100 && (data[i] + data[i + 1] + data[i + 2]) / 3 < 155) {
      ink[y * width + x] = 1; columns[x]++;
    }
  }
  // Exclude scan borders/binding rules without losing indented paragraph starts.
  const counts = new Uint32Array(height);
  for (let y = 0; y < height; y++) for (let x = 2; x < width - 2; x++) {
    if (columns[x] < height * .5) counts[y] += ink[y * width + x];
  }
  const threshold = Math.max(3, width * .012);
  const gap = Math.max(1, Math.round(height / 1000));
  const bands: Array<[number, number]> = [];
  let start = -1, last = -1;
  for (let y = 0; y <= height + gap; y++) {
    if (y < height && counts[y] >= threshold) { if (start < 0) start = y; last = y; }
    else if (start >= 0 && y - last > gap) { if (last - start >= 3) bands.push([start, last + 1]); start = -1; }
  }
  const spans: PdfOcrTextSpan[] = [...recognized];
  const heights = recognized.map(s => s.height * height).sort((a, b) => a - b);
  const typicalHeight = heights[Math.floor(heights.length / 2)] || height * .018;
  for (const [top, bottom] of bands) {
    const lineHeight = bottom - top;
    // Pictures and large solid blocks are not text lines.
    if (lineHeight > typicalHeight * 1.8 || lineHeight < typicalHeight * .55) continue;
    let left = width, right = 0;
    for (let x = 2; x < width - 2; x++) {
      if (columns[x] >= height * .5) continue;
      let count = 0;
      for (let y = top; y < bottom; y++) count += ink[y * width + x];
      if (count >= Math.max(2, lineHeight * .1)) { left = Math.min(left, x); right = x + 1; }
    }
    if (right - left < Math.max(lineHeight, width * .04)) continue;
    const matches = recognized.filter(s => {
      const center = (s.top + s.height / 2) * height;
      return center >= top - 1 && center <= bottom + 1 && s.height * height < lineHeight * 1.8;
    }).sort((a, b) => a.left - b.left);
    if (!matches.length) {
      spans.push({ left: left / width, top: top / height, width: (right - left) / width,
        height: lineHeight / height, confidence: null,
        // Caret positions only; never send these placeholders as recognized text.
        text: '▯'.repeat(Math.max(2, Math.round((right - left) / lineHeight))), geometryOnly: true });
    }
  }
  // Sort whole rows, then words within each row; slight scan skew must not interleave lines.
  const rows: PdfOcrTextSpan[][] = [];
  for (const span of spans.sort((a, b) => a.top + a.height / 2 - b.top - b.height / 2)) {
    const row = rows[rows.length - 1];
    if (row && Math.abs((row[0].top + row[0].height / 2) - (span.top + span.height / 2)) < typicalHeight / height * .65) row.push(span);
    else rows.push([span]);
  }
  return rows.flatMap(row => row.sort((a, b) => a.left - b.left));
}
