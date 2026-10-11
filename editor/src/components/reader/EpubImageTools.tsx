import { useEffect, useRef, useState } from 'react';
import type { PdfSelectionRect } from './lib/paper-comments';
import type { PdfOcrTextSpan } from './lib/pdf-ocr';
import { readBrowserOcr, saveBrowserOcr } from './lib/ocr-cache';
import { OCR_LAYOUT_VERSION } from './lib/ocr-layout';

/** Image-only books retain local OCR and cropped-image questions without altering chapter layout. */
export function EpubImageTools({ image, onSelection }: { image: string; onSelection: (quote: string, rects: PdfSelectionRect[], crop: Blob) => void }) {
  const content = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [spans, setSpans] = useState<PdfOcrTextSpan[]>([]);
  const [status, setStatus] = useState('Recognizing image text…');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setSpans([]); setStatus('Recognizing image text…');
    void (async () => {
      const blob = await (await fetch(image)).blob();
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())), v => v.toString(16).padStart(2, '0')).join('');
      const identity = `epub-image:v1:${OCR_LAYOUT_VERSION}:${hash}`;
      const img = new Image(); img.src = image; await img.decode();
      if (cancelled) return;
      const c = document.createElement('canvas');
      const scale = Math.min(2, 2400 / Math.max(img.naturalWidth, img.naturalHeight));
      c.width = Math.max(1, Math.round(img.naturalWidth * scale)); c.height = Math.max(1, Math.round(img.naturalHeight * scale));
      const context = c.getContext('2d'); if (!context) throw Error('Image recognition is unavailable.');
      context.fillStyle = '#ffffff'; context.fillRect(0, 0, c.width, c.height); context.drawImage(img, 0, 0, c.width, c.height); canvas.current = c;
      let saved: PdfOcrTextSpan[] | undefined;
      try { saved = (await readBrowserOcr(identity))[0]?.spans; } catch { /* optional cache */ }
      const recognized = saved || await (await import('./lib/ocr-runner')).ocrIllustration(c);
      if (cancelled) return;
      if (!saved) try { await saveBrowserOcr(identity, { page_number: 1, spans: recognized, layout_version: OCR_LAYOUT_VERSION, layout_mode: 'auto', split: null }); } catch { /* optional cache */ }
      if (cancelled) return;
      setSpans(recognized); setStatus(recognized.length ? 'Select recognized words below to quote and crop them. Check OCR against the image.' : 'No text detected. Ask can still send the illustration.');
    })().catch(e => { if (!cancelled) setStatus(e instanceof Error ? e.message : 'Image recognition failed.'); });
    return () => { cancelled = true; canvas.current = null; };
  }, [image, retry]);
  const capture = async () => {
    const selected = window.getSelection(), root = content.current, source = canvas.current;
    if (!root || !source || !selected?.rangeCount || selected.isCollapsed) return;
    const range = selected.getRangeAt(0); if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return;
    const chosen = Array.from(root.querySelectorAll<HTMLElement>('[data-span]')).filter(node => range.intersectsNode(node)).map(node => spans[Number(node.dataset.span)]);
    if (!chosen.length) return;
    const quote = selected.toString().trim(); if (!quote || quote.length > 700) { setStatus('Select up to 700 characters.'); return; }
    const rects = chosen.map(span => ({ left: span.left, top: span.top, width: span.width, height: span.height }));
    const left = Math.min(...rects.map(r => r.left)), top = Math.min(...rects.map(r => r.top));
    const right = Math.max(...rects.map(r => r.left + r.width)), bottom = Math.max(...rects.map(r => r.top + r.height));
    const crop = document.createElement('canvas'); crop.width = Math.max(1, Math.ceil((right - left) * source.width)); crop.height = Math.max(1, Math.ceil((bottom - top) * source.height));
    crop.getContext('2d')!.drawImage(source, left * source.width, top * source.height, crop.width, crop.height, 0, 0, crop.width, crop.height);
    const blob = await new Promise<Blob | null>(resolve => crop.toBlob(resolve, 'image/png'));
    if (blob && canvas.current === source) onSelection(quote, rects, blob);
  };
  return <section className="wb-epub-image-tools" aria-label="Illustration text"><p role="status">{status} <button onClick={() => setRetry(value => value + 1)}>Retry OCR</button></p><div ref={content} onMouseUp={() => void capture()} onKeyUp={() => void capture()} tabIndex={0}>{spans.map((span, index) => <span key={index} data-span={index}>{span.text} </span>)}</div></section>;
}
