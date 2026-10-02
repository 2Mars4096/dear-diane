import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/pdf';
import type { PdfSelectionAnchor, PdfSelectionRect } from './paper-comments';

export function selectionBounds(rects: PdfSelectionRect[]): PdfSelectionRect {
  const left = Math.min(...rects.map(r => r.left));
  const top = Math.min(...rects.map(r => r.top));
  return { left, top, width: Math.max(...rects.map(r => r.left + r.width)) - left,
    height: Math.max(...rects.map(r => r.top + r.height)) - top };
}

/** Render directly into a bounded crop, independently of reader zoom and OCR. */
export async function selectionImage(document: PDFDocumentProxy, selection: PdfSelectionAnchor, selectedOnly = false): Promise<Blob> {
  if (!selection.rects.length) throw Error('Select an area first.');
  const bounds = selectionBounds(selection.rects);
  const page = await document.getPage(selection.pageNumber);
  const natural = page.getViewport({ scale: 1, rotation: selection.rotation });
  const scale = Math.min(3, 2400 / Math.max(natural.width * bounds.width, natural.height * bounds.height));
  const viewport = page.getViewport({ scale, rotation: selection.rotation });
  const canvas = window.document.createElement('canvas');
  canvas.width = Math.max(1, Math.ceil(viewport.width * bounds.width));
  canvas.height = Math.max(1, Math.ceil(viewport.height * bounds.height));
  const context = canvas.getContext('2d', { alpha: false });
  if (!context) throw Error('Cannot capture the selected area.');
  try {
    context.fillStyle = 'white'; context.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvas, canvasContext: context, viewport,
      transform: [1, 0, 0, 1, -bounds.left * viewport.width, -bounds.top * viewport.height] }).promise;
    let output = canvas;
    if (selectedOnly) {
      // Stack the selected line fragments in reading order. Relative blank offsets can
      // make a vision model read the second wrapped line before the first.
      const strips = selection.rects.map(rect => ({ x: (rect.left - bounds.left) * viewport.width, y: (rect.top - bounds.top) * viewport.height, width: rect.width * viewport.width, height: rect.height * viewport.height }));
      const stitched = window.document.createElement('canvas');
      stitched.width = Math.ceil(Math.max(...strips.map(r => r.width))) + 16;
      stitched.height = Math.ceil(strips.reduce((sum, r) => sum + r.height + 8, 8));
      const inkContext = stitched.getContext('2d', { alpha: false });
      if (!inkContext) throw Error('Cannot capture the selected text.');
      inkContext.fillStyle = 'white'; inkContext.fillRect(0, 0, stitched.width, stitched.height);
      let y = 8;
      for (const strip of strips) { inkContext.drawImage(canvas, strip.x, strip.y, strip.width, strip.height, 8, y, strip.width, strip.height); y += strip.height + 8; }
      const pixels = inkContext.getImageData(0, 0, stitched.width, stitched.height).data;
      let ink = 0;
      for (let i = 0; i < pixels.length; i += 4) if (pixels[i] + pixels[i + 1] + pixels[i + 2] < 600) ink++;
      if (ink < 8) throw Error('No text pixels were captured. Select the passage again.');
      output = stitched;
    }
    return await new Promise<Blob>((resolve, reject) => output.toBlob(blob => blob ? resolve(blob) : reject(Error('Cannot capture the selected area.')), 'image/png'));
  } finally { canvas.width = canvas.height = 1; }
}
