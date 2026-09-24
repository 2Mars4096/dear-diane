import { expect, it } from 'vitest';
import { pdfAssetBase } from '../lib/pdf-assets';
it('anchors decoder and font URLs to the application instead of the worker directory', () => {
 const base=pdfAssetBase('./','http://localhost:45173/#workspace');
 expect(new URL(base+'wasm/jbig2.wasm','http://localhost:45173/assets/pdf.worker.mjs').href).toBe('http://localhost:45173/pdfjs/wasm/jbig2.wasm');
 expect(pdfAssetBase('./','https://example.test/dan/#workspace')).toBe('https://example.test/dan/pdfjs/');
 expect(pdfAssetBase('/dan/','https://example.test/another/page')).toBe('https://example.test/dan/pdfjs/');
});
