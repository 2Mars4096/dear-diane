import { expect, it } from 'vitest';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/pdf';
import { detectPdfChapters, pdfOutline } from '../lib/pdf-chapters';

it('resolves named and referenced destinations and retains children of unlinked headings', async () => {
  const doc = {
    numPages: 20,
    getOutline: async () => [
      { title: 'Part', dest: null, items: [{ title: 'Opening', dest: 'opening', items: [] }] },
      { title: 'Broken', dest: 'missing', items: [] },
      { title: 'Second', dest: [9], items: [] },
      { title: 'Outside book', dest: [30], items: [] },
    ],
    getDestination: async (name: string) => name === 'opening' ? [{ num: 4, gen: 0 }] : null,
    getPageIndex: async () => 2,
  } as unknown as PDFDocumentProxy;
  expect(await pdfOutline(doc)).toEqual([
    { id: 'outline-0', label: 'Opening', page: 3, depth: 1 },
    { id: 'outline-1', label: 'Second', page: 10, depth: 0 },
  ]);
});
it('detects explicit headings, suppresses contents and repeated headers, and preserves page numbers', () => {
  const texts = new Map([
    [1, 'Contents\nChapter 1\nChapter 2'],
    [3, 'Chapter 1\nThe first topic\nBody text'],
    [4, 'Chapter 1\nMore body text'],
    [8, 'Chapter 2: A second topic\nBody text'],
    [10, '第二章 新的开始\n正文'],
    [11, 'This paragraph discusses chapter three.'],
    [12, 'Chapter 3 .......... 14'],
  ]);
  expect(detectPdfChapters(texts).map(c => [c.page, c.label, c.detected])).toEqual([
    [3, 'Chapter 1', true], [8, 'Chapter 2: A second topic', true], [10, '第二章 新的开始', true],
  ]);
});
it('uses existing OCR for empty or number-only pages without replacing native text', () => {
  const spans = [{ text: 'Chapter', top: .1, left: .1, width: .2, height: .03, confidence: .9 }, { text: 'IV', top: .1, left: .3, width: .1, height: .03, confidence: .9 }];
  const chapters = detectPdfChapters(new Map([[20, '20'], [21, 'Native paragraph with sufficient selectable text to avoid replacing a good text layer. '.repeat(10)]]), [{ page_number: 20, spans }, { page_number: 21, spans }]);
  expect(chapters).toMatchObject([{ label: 'Chapter IV', page: 20, detected: true }]);
});
