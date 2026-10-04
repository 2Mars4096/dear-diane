// @vitest-environment happy-dom
import { act, createElement, type ComponentProps } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { ReaderView } from '../ReaderView';
import type { InteractivePdfViewer } from '../InteractivePdfViewer';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/pdf';
import { extractPageTexts, ocrPage, sparsePages } from '../lib/ocr-runner';
import { browserOcrIdentity, readBrowserOcr, saveBrowserOcr } from '../lib/ocr-cache';
import { OCR_LAYOUT_VERSION } from '../lib/ocr-layout';
import type { MaterialPdfOcrPage } from '../lib/pdf-ocr';

let viewer: ComponentProps<typeof InteractivePdfViewer>;
vi.mock('../InteractivePdfViewer', () => ({ InteractivePdfViewer: (props: typeof viewer) => { viewer = props; return null; } }));
vi.mock('../lib/ocr-runner', async importOriginal => ({
  ...await importOriginal<typeof import('../lib/ocr-runner')>(),
  extractPageTexts: vi.fn(), ocrPage: vi.fn(),
}));
vi.mock('../lib/ocr-cache', () => ({ browserOcrIdentity: vi.fn(), readBrowserOcr: vi.fn(), saveBrowserOcr: vi.fn() }));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const texts = () => new Map(Array.from({ length: 205 }, (_, i) => [i + 1, '']));
const result = (page_number: number): MaterialPdfOcrPage => ({ page_number, layout_version: OCR_LAYOUT_VERSION, layout_mode: 'auto', spans: [{ text: '扫描文字', left: .1, top: .1, width: .3, height: .03, confidence: .9 }] });
const cleanup: (() => void)[] = [];
afterEach(() => { cleanup.splice(0).forEach(close => close()); vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
async function mount(saved: MaterialPdfOcrPage[] = [], url = '/scan.pdf') {
  vi.mocked(extractPageTexts).mockResolvedValue(texts());
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ pages: saved }) }));
  const host = document.createElement('div'); const root = createRoot(host);
  cleanup.push(() => act(() => root.unmount()));
  await act(async () => root.render(createElement(ReaderView, { file: { name: 'scan', path: '/scan.pdf', url }, onAsk: () => {}, onNotes: () => {} })));
  await act(async () => viewer.onDocument?.({ numPages: 205 } as PDFDocumentProxy));
}

it('includes scanned pages beyond 40 while preserving native text pages', () => {
  const pages = texts(); pages.set(100, 'Native text');
  expect(sparsePages(pages)).toContain(43);
  expect(sparsePages(pages)).toContain(205);
  expect(sparsePages(pages)).not.toContain(100);
});

it('recognizes only nearby pages and resumes when navigating farther', async () => {
  vi.mocked(ocrPage).mockImplementation(async (_doc, page) => result(page));
  await mount();
  expect(ocrPage).toHaveBeenCalledTimes(2);
  await act(async () => viewer.onPageChange(43));
  expect(viewer.ocrPages.some(page => page.page_number === 43)).toBe(true);
  await act(async () => viewer.onPageChange(205));
  expect(viewer.ocrPages.some(page => page.page_number === 205)).toBe(true);
  const puts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'PUT');
  const saved = JSON.parse(String(puts.at(-1)?.[1]?.body)).pages;
  expect(saved.some((page: MaterialPdfOcrPage) => page.page_number === 205)).toBe(true);
  const calls = vi.mocked(ocrPage).mock.calls.length;
  await act(async () => viewer.onPageChange(43));
  expect(ocrPage).toHaveBeenCalledTimes(calls);
});

it('prioritizes navigation after the current recognition and keeps cached pages', async () => {
  let finish!: (page: MaterialPdfOcrPage) => void;
  vi.mocked(ocrPage).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockImplementation(async (_doc, page) => result(page));
  await mount(Array.from({ length: 40 }, (_, i) => result(i + 1)));
  expect(ocrPage).not.toHaveBeenCalled();
  await act(async () => viewer.onPageChange(43));
  expect(vi.mocked(ocrPage).mock.calls.map(call => call[1])).toEqual([43]);
  await act(async () => viewer.onPageChange(205));
  expect(ocrPage).toHaveBeenCalledTimes(1);
  await act(async () => finish(result(43)));
  expect(vi.mocked(ocrPage).mock.calls[1][1]).toBe(205);
  expect(viewer.ocrPages.some(page => page.page_number === 1)).toBe(true);
  expect(viewer.ocrPages.some(page => page.page_number === 205)).toBe(true);
});

it('discards recognition completed after the reader is closed', async () => {
  let finish!: (page: MaterialPdfOcrPage) => void;
  vi.mocked(ocrPage).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await mount();
  cleanup.pop()!();
  await act(async () => finish(result(1)));
  expect(ocrPage).toHaveBeenCalledTimes(1);
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0);
});

it('reopens cached pages without launching another OCR batch', async () => {
  vi.mocked(ocrPage).mockImplementation(async (_doc, page) => result(page));
  await mount([result(1), result(2)]);
  expect(ocrPage).not.toHaveBeenCalled();
  expect(viewer.ocrPages.map(page => page.page_number)).toEqual([1, 2]);
  await act(async () => viewer.onPageChange(2));
  expect(vi.mocked(ocrPage).mock.calls.map(call => call[1])).toEqual([3]);
});

it('reuses persisted browser OCR with a new blob URL and saves only missing pages', async () => {
  vi.mocked(browserOcrIdentity).mockResolvedValue('same-content');
  vi.mocked(readBrowserOcr).mockResolvedValue([result(1), result(2)]);
  vi.mocked(saveBrowserOcr).mockResolvedValue();
  vi.mocked(ocrPage).mockImplementation(async (_doc, page) => result(page));
  await mount([], 'blob:new-after-restart');
  expect(readBrowserOcr).toHaveBeenCalledWith('same-content');
  expect(ocrPage).not.toHaveBeenCalled();
  await act(async () => viewer.onPageChange(3));
  expect(vi.mocked(ocrPage).mock.calls.map(call => call[1])).toEqual([3, 4]);
  expect(saveBrowserOcr).toHaveBeenCalledWith('same-content', result(3));
  expect(fetch).not.toHaveBeenCalled();
});
