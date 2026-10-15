import { applyEpubTypography, type EpubTypography, type ReaderTextStyle } from "./epub-typography";
import { yieldEpubPagination } from './epub-input';
import { Rendition } from 'epubjs';
import type Book from 'epubjs/types/book';
import type Contents from 'epubjs/types/contents';
import type Mapping from 'epubjs/types/mapping';
import type Section from 'epubjs/types/section';

export type EpubPage = { section: number; page: number; cfi: string };
export type EpubPageSize = { width: number; height: number; font: number; spread: string; typography?: EpubTypography; text?: ReaderTextStyle };
// EPUB.js exposes these on its default manager but omits them from Rendition's types.
type MeasuredView = { section: Section; contents: Contents; width: () => number; expand: () => void };
type MeasuringRendition = Rendition & { manager: {
  views: { all: () => MeasuredView[] };
  layout: { pageWidth: number; count: (width: number) => { pages: number } };
  mapping: Mapping; settings: { direction: string };
} };

/** Measure the whole book at the real viewport/font without moving the visible reader. */
export async function paginateEpub(book: Book, size: EpubPageSize, signal: AbortSignal): Promise<EpubPage[]> {
  const host = document.createElement('div');
  host.setAttribute('aria-hidden', 'true');
  host.style.cssText = `position:fixed;left:-100000px;top:0;width:${size.width}px;height:${size.height}px;visibility:hidden;pointer-events:none;overflow:hidden;`;
  document.body.append(host);
  const existingHooks = new Set(book.spine.hooks.content.list());
  const rendition = new Rendition(book, { width: size.width, height: size.height, flow: 'paginated', spread: size.spread, allowScriptedContent: false }) as MeasuringRendition;
  const addedHooks = book.spine.hooks.content.list().filter(hook => !existingHooks.has(hook));
  const pages: EpubPage[] = [];
  try {
    await rendition.attachTo(host);
    applyEpubTypography(rendition, size.typography || 'original', false, size.text);
    rendition.themes.fontSize(`${size.font}%`);
    rendition.themes.default({ 'img,svg': { 'max-width': '100%' } });
    const sections: Section[] = []; book.spine.each((section: Section) => { if (section.linear) sections.push(section); });
    for (const section of sections) {
      await yieldEpubPagination(signal);
      await rendition.display(section.href);
      signal.throwIfAborted();
      const view = rendition.manager.views.all().find(item => item.section.index === section.index);
      if (!view) throw Error('Could not calculate book pages.');
      await view.contents.document.fonts?.ready;
      view.expand();
      const { layout, mapping, settings } = rendition.manager;
      const count = Math.max(1, layout.count(view.width()).pages);
      if (!Number.isFinite(count) || layout.pageWidth <= 0) throw Error('Could not measure this chapter.');
      for (let index = 0; index < count; index++) {
        await yieldEpubPagination(signal);
        const offset = settings.direction === 'rtl' ? count - index - 1 : index;
        const start = offset * layout.pageWidth;
        const cfi = mapping.page(view.contents, section.cfiBase, start, start + layout.pageWidth).start;
        pages.push({ section: section.index, page: index + 1, cfi });
      }
      // Yield between chapters so pagination never monopolizes reading/selection input.
      await new Promise(resolve => window.setTimeout(resolve, 0));
    }
    return pages;
  } finally {
    rendition.destroy(); host.remove();
    for (const hook of addedHooks) book.spine.hooks.content.deregister(hook);
  }
}
