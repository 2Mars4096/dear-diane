import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/pdf';
import type { PdfSelectionAnchor, PdfSelectionRect } from './paper-comments';

export function selectionBounds(rects: PdfSelectionRect[]): PdfSelectionRect {
  const left = Math.min(...rects.map(r => r.left));
  const top = Math.min(...rects.map(r => r.top));
  return { left, top, width: Math.max(...rects.map(r => r.left + r.width)) - left,
    height: Math.max(...rects.map(r => r.top + r.height)) - top };
}

/** Render directly into a bounded crop, independently of reader zoom and OCR. */
export async function selectionImage(document: PDFDocumentProxy, selection: PdfSelectionAnchor): Promise<Blob> {
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
    await page.render({ canvas, canvasContext: context, viewport,
      transform: [1, 0, 0, 1, -bounds.left * viewport.width, -bounds.top * viewport.height] }).promise;
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(Error('Cannot capture the selected area.')), 'image/png'));
  } finally { canvas.width = canvas.height = 1; }
}
