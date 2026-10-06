import type { PdfOcrTextSpan } from './pdf-ocr';

const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)] || 0;

/** Build continuous selectable lines from cached word boxes, per printed page.
 * Recognition stays unchanged; scan-border fragments never become caret targets. */
export function ocrSelectionLines(spans: PdfOcrTextSpan[], split: number | null, aspectRatio: number): PdfOcrTextSpan[] {
  if (!spans.length) return [];
  const typicalHeight = median(spans.filter(s => !s.geometryOnly && (s.confidence ?? 0) >= .3).map(s => s.height))
    || median(spans.map(s => s.height));
  const characterWidth = typicalHeight / Math.max(.1, aspectRatio);
  const usable = spans.filter(s => s.height >= typicalHeight * .45 && s.height <= typicalHeight * 1.8);
  const regions = split === null ? [usable] : [
    usable.filter(s => s.left + s.width / 2 < split),
    usable.filter(s => s.left + s.width / 2 >= split),
  ];
  return regions.flatMap(region => {
    const rows: PdfOcrTextSpan[][] = [];
    for (const span of [...region].sort((a, b) => a.top + a.height / 2 - b.top - b.height / 2)) {
      const row = rows.at(-1);
      const center = span.top + span.height / 2;
      if (row && Math.abs(median(row.map(s => s.top + s.height / 2)) - center) < typicalHeight * .6) row.push(span);
      else rows.push([span]);
    }
    return rows.flatMap(row => {
      const runs: PdfOcrTextSpan[][] = [];
      for (const span of row.sort((a, b) => a.left - b.left)) {
        const run = runs.at(-1);
        const right = run && Math.max(...run.map(s => s.left + s.width));
        if (run && span.left - right! < characterWidth * 3) run.push(span);
        else runs.push([span]);
      }
      // Detached scan noise beside a paragraph must not widen or precede its line.
      const run = runs.sort((a, b) =>
        (Math.max(...b.map(s => s.left + s.width)) - b[0].left) -
        (Math.max(...a.map(s => s.left + s.width)) - a[0].left))[0];
      const left = run[0].left, right = Math.max(...run.map(s => s.left + s.width));
      if (right - left < characterWidth * 1.5) return [];
      const top = Math.min(...run.map(s => s.top)), bottom = Math.max(...run.map(s => s.top + s.height));
      return [{ left, top, width: right - left, height: bottom - top,
        text: run.map(s => s.text).join(' '), confidence: median(run.map(s => s.confidence ?? 0)),
        ...(run.some(s => s.geometryOnly) ? { geometryOnly: true } : {}),
      }];
    });
  });
}

/** Fill missing words using the already-rendered page pixels, without rerunning OCR. */
export function refineOcrLines(image: Pick<ImageData, 'width' | 'height' | 'data'>, lines: PdfOcrTextSpan[], split: number | null): PdfOcrTextSpan[] {
  const { width, height, data } = image;
  const groups = split === null ? [lines] : [lines.filter(s => s.left + s.width / 2 < split), lines.filter(s => s.left + s.width / 2 >= split)];
  return groups.flatMap(group => {
    const typical = median(group.map(s => s.height)) * height;
    const reliable = group.filter(s => !s.geometryOnly && (s.confidence ?? 0) >= .3 && s.width * width > typical * 8);
    if (reliable.length < 3) return group;
    const quantile = (values: number[], q: number) => values.sort((a,b) => a-b)[Math.floor((values.length - 1) * q)];
    const from = Math.max(0, Math.floor(quantile(reliable.map(s => s.left * width), .15) - typical * .6));
    const to = Math.min(width, Math.ceil(quantile(reliable.map(s => (s.left + s.width) * width), .9) + typical * .6));
    const isInk = (x: number, y: number) => {
      const i = (y * width + x) * 4;
      return data[i + 3] > 100 && Math.max(data[i], data[i + 1], data[i + 2]) < 170;
    };
    const counts = new Uint32Array(height);
    for (let y = 0; y < height; y++) for (let x = from; x < to; x++) if (isInk(x, y)) counts[y]++;
    const threshold = Math.max(3, (to - from) * .015), gap = Math.max(1, Math.round(typical * .08));
    const result: PdfOcrTextSpan[] = [];
    let top = -1, last = -1;
    for (let y = 0; y <= height + gap; y++) {
      if (y < height && counts[y] >= threshold) { if (top < 0) top = y; last = y; continue; }
      if (top < 0 || y - last <= gap) continue;
      const lineHeight = last + 1 - top;
      if (lineHeight >= typical * .5 && lineHeight < typical * 1.9) {
        let left = to, right = from;
        for (let x = from; x < to; x++) {
          let count = 0;
          for (let yy = top; yy <= last; yy++) if (isInk(x, yy)) count++;
          if (count >= Math.max(2, lineHeight * .12)) { left = Math.min(left, x); right = x + 1; }
        }
        if (right - left >= typical * 1.5) {
          const matching = group.filter(s => (s.top + s.height / 2) * height >= top - 1 && (s.top + s.height / 2) * height <= last + 2);
          result.push({ left: left / width, top: top / height, width: (right - left) / width, height: lineHeight / height,
            text: matching.map(s => s.text).join(' ') || '▯'.repeat(Math.max(2, Math.round((right-left) / typical))),
            confidence: matching.length ? median(matching.map(s => s.confidence ?? 0)) : null,
            ...(!matching.length || matching.some(s => s.geometryOnly) ? { geometryOnly: true } : {}),
          });
        }
      }
      top = -1;
    }
    return result.length ? result : group;
  });
}

/** Keep an OCR drag in reading order when Chromium hits the empty layer between lines. */
export function extendOcrSelectionAcrossGap(layer: HTMLElement, x: number, y: number): boolean {
  const selection = layer.ownerDocument.getSelection();
  const anchor = selection?.anchorNode;
  if (!anchor || !layer.contains(anchor) || !anchor.parentElement?.closest('[data-ocr-line]')) return false;
  const lines = Array.from(layer.querySelectorAll<HTMLElement>('[data-ocr-line]'))
    .map(element => ({ element, rect: element.getBoundingClientRect() }));
  if (lines.some(({ rect }) => x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom)) return false;
  // Require text both above and below at this x: margins and page gutters stay native.
  const column = lines.filter(({ rect }) => x >= rect.left && x <= rect.right);
  const above = column.filter(({ rect }) => rect.bottom < y).sort((a, b) => b.rect.bottom - a.rect.bottom)[0];
  const below = column.filter(({ rect }) => rect.top > y).sort((a, b) => a.rect.top - b.rect.top)[0];
  if (!above || !below || below.rect.top - above.rect.bottom > Math.max(above.rect.height, below.rect.height)) return false;
  const nearest = y - above.rect.bottom <= below.rect.top - y ? above : below;
  const caret = layer.ownerDocument.caretRangeFromPoint?.(x, nearest.rect.top + nearest.rect.height / 2);
  if (!caret || !nearest.element.contains(caret.startContainer)) return false;
  selection.extend(caret.startContainer, caret.startOffset);
  return true;
}
