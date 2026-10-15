import { DEFAULT_READER_TEXT, type ReaderTextStyle, type EpubTypography } from "./lib/epub-typography";
import { useEffect, useState, type RefObject } from 'react';
import type Book from 'epubjs/types/book';
import { paginateEpub, type EpubPage } from './lib/epub-pagination';

export function useEpubPagination(book: Book | null, host: RefObject<HTMLDivElement | null>, ready: boolean, font: number, spread: string, typography: EpubTypography = 'original', text: ReaderTextStyle = DEFAULT_READER_TEXT) {
  const [pages, setPages] = useState<EpubPage[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setPages([]); setLoading(true);
    if (!book || !ready || !host.current) return;
    let controller: AbortController | undefined, timer: number | undefined, lastSize = '';
    let cancelled = false;
    const measure = () => {
      const box = host.current?.getBoundingClientRect();
      if (!box || box.width <= 0 || box.height <= 0) return;
      const width = Math.floor(box.width), height = Math.floor(box.height), key = `${width}:${height}`;
      if (key === lastSize) return;
      lastSize = key; controller?.abort(); window.clearTimeout(timer); setLoading(true);
      timer = window.setTimeout(() => {
        const run = new AbortController(); controller = run;
        void paginateEpub(book, { width, height, font, spread, typography, text }, run.signal).then(result => {
          if (!cancelled && !run.signal.aborted) { setPages(result); setLoading(false); }
        }).catch((error) => { if (cancelled || run.signal.aborted) return; console.warn('EPUB pagination failed', error); setLoading(false); });
      }, 250);
    };
    const observer = new ResizeObserver(measure); observer.observe(host.current); measure();
    return () => { cancelled = true; window.clearTimeout(timer); controller?.abort(); observer.disconnect(); };
  }, [book, host, ready, font, spread, typography, text]);
  return { pages, loading };
}
