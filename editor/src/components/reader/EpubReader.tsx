import { epubNavigationKey, epubWheelNavigation, prioritizeEpubInput } from './lib/epub-input';
import { useEpubPagination } from './useEpubPagination';
import { ReaderPageNavigation } from './ReaderPageNavigation';
import { waitForReaderSize } from './lib/reader-size';
import { useEffect, useRef, useState } from 'react';
import ePub, { EpubCFI } from 'epubjs';
import type Book from 'epubjs/types/book';
import type Rendition from 'epubjs/types/rendition';
import type { Location } from 'epubjs/types/rendition';
import type Contents from 'epubjs/types/contents';
import type Section from 'epubjs/types/section';
import type { NavItem } from 'epubjs/types/navigation';
import type { ReaderAsk, ReaderFile } from './ReaderView';
import { readerActions, useReaderState, type ReaderDraft } from './readerStore';
import { readPaperReferenceTray, writePaperReferenceTray, MAX_PAPER_REFERENCE_TAGS, type PaperReferenceTray } from './lib/paper-reference-tags';
import { checkEpub, downloadEpubBlob, exportAnnotatedEpub, protectEpubDocument, EPUB_MAX_BYTES } from './lib/epub';
import { prepareAttachments } from '../../lib/attachFiles';
import { textSelection, anchorRange } from '../documents/TextAnnotations';
import { normalizePassageText } from './lib/paper-anchors';
import { EpubReferences } from './EpubReferences';
import { bridgeReaderFileDrops } from './lib/reader-file-drop';
import { EpubImageTools } from './EpubImageTools';
import './epub-reader.css';
import { ReaderToolbar } from './ReaderToolbar';
import { Download, MessageSquareQuote } from 'lucide-react';

type Selection = ReaderDraft & { context: string; image?: string; crop?: Blob };
type Position = { cfi?: string; font?: number; spread?: string; flow?: string };
const key = (path: string) => `diane.reader.epub.v1:${path}`;
function savedPosition(path: string): Position {
  try {
    const saved = JSON.parse(localStorage.getItem(key(path)) || '{}');
    if (!saved || typeof saved !== 'object') return {};
    return { cfi: typeof saved.cfi === 'string' && /^epubcfi\(.+\)$/.test(saved.cfi) ? saved.cfi : undefined,
      font: typeof saved.font === 'number' && Number.isFinite(saved.font) ? saved.font : 100, spread: saved.spread === 'auto' ? 'auto' : 'none', flow: saved.flow === 'paginated' ? 'paginated' : 'scrolled-continuous' };
  } catch { return {}; }
}
function flatten(items: NavItem[], depth = 0): { href: string; label: string }[] {
  return items.flatMap(item => [{ href: item.href, label: `${'　'.repeat(depth)}${item.label.trim()}` }, ...flatten(item.subitems || [], depth + 1)]);
}

export default function EpubReader({ file, onAsk, onNotes }: { file: ReaderFile; onAsk: (ask: ReaderAsk) => void; onNotes: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLElement>(null);
  const bookRef = useRef<Book | null>(null);
  const renditionRef = useRef<Rendition | null>(null);
  const turnRef = useRef<(direction: number) => void>(() => {});
  const bytesRef = useRef<ArrayBuffer | null>(null);
  const callbacks = useRef({ onAsk, onNotes }); callbacks.current = { onAsk, onNotes };
  const state = useReaderState(file.path);
  const [initial] = useState(() => savedPosition(file.path));
  const [font, setFont] = useState(() => Math.max(70, Math.min(200, initial.font || 100)));
  const [spread, setSpread] = useState(initial.spread === 'auto' ? 'auto' : 'none');
  const [flow, setFlow] = useState(initial.flow || 'scrolled-continuous');
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [toc, setToc] = useState<{ href: string; label: string }[]>([]);
  const [chapter, setChapter] = useState('');
  const [location, setLocation] = useState<Location | null>(null);
  const pagination = useEpubPagination(bookRef.current, host, ready, font, spread);
  const bookPage = location ? (flow === 'paginated'
    ? pagination.pages.findIndex(page => page.section === location.start.index && page.page === location.start.displayed.page) + 1
    : (() => {
      const compare = new EpubCFI();
      let low = 0, high = pagination.pages.length;
      while (low < high) { const mid = (low + high) >>> 1; if (compare.compare(pagination.pages[mid].cfi, location.start.cfi) <= 0) low = mid + 1; else high = mid; }
      return Math.max(1, low);
    })()) : 0;
  const [selection, setSelection] = useState<Selection | null>(null);
  const [tray, setTray] = useState(() => readPaperReferenceTray(file.path));
  const [references, setReferences] = useState(false);
  const [referencesPinned, setReferencesPinned] = useState(false);
  const referenceTimers = useRef<{ open?: number; close?: number }>({});
  const cancelReferenceTimers = () => {
    window.clearTimeout(referenceTimers.current.open);
    window.clearTimeout(referenceTimers.current.close);
  };
  useEffect(() => () => {
    window.clearTimeout(referenceTimers.current.open);
    window.clearTimeout(referenceTimers.current.close);
  }, []);
  const openReferences = (hover = false) => {
    cancelReferenceTimers();
    if (hover) referenceTimers.current.open = window.setTimeout(() => setReferences(true), 140);
    else setReferences(true);
  };
  const closeReferences = () => {
    cancelReferenceTimers();
    if (!referencesPinned) referenceTimers.current.close = window.setTimeout(() => { setReferences(false); setPreview(null); }, 240);
  };
  const pinReferences = () => {
    cancelReferenceTimers();
    setReferencesPinned(!referencesPinned); setReferences(!referencesPinned);
    if (referencesPinned) setPreview(null);
  };
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const options = useRef({ font, spread, flow }); options.current = { font, spread, flow };
  const position = useRef(initial.cfi);
  const tocRef = useRef(toc); tocRef.current = toc;
  const persist = () => { try { localStorage.setItem(key(file.path), JSON.stringify({ ...options.current, cfi: position.current })); } catch { setNotice('Reading position could not be saved on this device.'); } };
  const labelFor = (index: number, href: string) => tocRef.current.find(item => item.href.split('#')[0] === href.split('#')[0])?.label.trim() || `Chapter ${index + 1}`;

  useEffect(() => {
    if (!host.current) return;
    let cancelled = false;
    const controller = new AbortController();
    let timeout: number | undefined;
    let book: Book | undefined, rendition: Rendition | undefined;
    let resize: ResizeObserver | undefined, theme: MutationObserver | undefined;
    setReady(false); setError(''); setSelection(null); setLocation(null); setToc([]); setChapter('');
    const failure = (e: unknown) => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not open this EPUB.'); };
    void (async () => {
      await waitForReaderSize(host.current!, controller.signal);
      if (cancelled) return;
      timeout = window.setTimeout(() => { controller.abort(); if (!cancelled) setError('Opening timed out. Try again.'); }, 30000);
      const response = await fetch(file.url, { signal: controller.signal });
      if (!response.ok) throw Error(`Could not load EPUB (${response.status}).`);
      if (Number(response.headers.get('content-length')) > EPUB_MAX_BYTES) throw Error('This EPUB is too large (150 MB maximum).');
      const bytes = await response.arrayBuffer();
      await checkEpub(bytes);
      if (cancelled) return;
      bytesRef.current = bytes;
      book = ePub(); bookRef.current = book;
      book.spine.hooks.content.register((doc: Document) => protectEpubDocument(doc, false));
      await book.open(bytes, 'binary');
      if (controller.signal.aborted) throw Error('Opening timed out. Try again.');
      if (cancelled) return;
      // The serialize hook runs after EPUB.js resource replacement, before iframe insertion.
      book.spine.hooks.serialize.register((_html: string, section: Section) => {
        const doc = new DOMParser().parseFromString(section.output, 'application/xhtml+xml');
        if (doc.querySelector('parsererror')) throw Error('This EPUB contains an invalid chapter.');
        protectEpubDocument(doc);
        const base = doc.createElementNS('http://www.w3.org/1999/xhtml', 'base');
        base.setAttribute('href', new URL(section.url, window.location.href).href);
        doc.querySelector('head')?.append(base);
        section.output = new XMLSerializer().serializeToString(doc);
      });
      const navigation = await book.loaded.navigation;
      if (cancelled || controller.signal.aborted) return;
      setToc(flatten(navigation.toc)); tocRef.current = flatten(navigation.toc);
      if (!tocRef.current.length) {
        const chapters: { href: string; label: string }[] = [];
        book.spine.each((section: Section) => chapters.push({ href: section.href, label: `Chapter ${section.index + 1}` }));
        setToc(chapters); tocRef.current = chapters;
      }
      await waitForReaderSize(host.current!, controller.signal);
      if (cancelled) return;
      rendition = book.renderTo(host.current!, { width: '100%', height: '100%', manager: flow === 'paginated' ? 'default' : 'continuous', flow, spread: options.current.spread, allowScriptedContent: false });
      renditionRef.current = rendition;
      rendition.on('displayError', failure);
      const applyTheme = () => {
        if (!root.current || !rendition) return;
        const style = getComputedStyle(root.current);
        const ink = style.getPropertyValue('--wb-ink').trim() || style.color;
        const paper = style.getPropertyValue('--wb-soft').trim() || style.backgroundColor;
        rendition.themes.default({ body: { color: `${ink} !important`, background: `${paper} !important` }, 'img,svg': { 'max-width': '100%' } });
      };
      applyTheme(); rendition.themes.fontSize(`${options.current.font}%`);
      theme = new MutationObserver(applyTheme); theme.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
      let lastSection = '';
      let located = false;
      let turning = false;
      const turn = (direction: number) => {
        prioritizeEpubInput();
        if (cancelled || !located || turning) return;
        turning = true;
        void (direction > 0 ? rendition!.next() : rendition!.prev()).catch(failure).finally(() => { turning = false; });
      };
      turnRef.current = turn;
      rendition.on('relocated', (next: Location) => {
        if (cancelled) return;
        located = true;
        window.clearTimeout(timeout);
        setReady(true); setLocation(next); setChapter(next.start.href);
        if (lastSection && lastSection !== next.start.href) setSelection(null);
        lastSection = next.start.href;
        position.current = next.start.cfi; persist();
      });
      rendition.on('selected', (cfi: string, contents: Contents) => {
        try {
          const range = contents.range(cfi); const quote = range.toString().trim();
          if (!quote) return;
          const section = book!.spine.get(contents.sectionIndex);
          const before = range.cloneRange(); before.selectNodeContents(contents.document.body); before.setEnd(range.startContainer, range.startOffset);
          const start = before.toString().length;
          const anchored = textSelection(contents.document.body.textContent || '', start, start + range.toString().length);
          const context = range.startContainer.parentElement?.closest('p,section,article,div')?.textContent || contents.document.body.textContent || '';
          setNotice('');
          setSelection({ draftId: crypto.randomUUID(), quote, pageNumber: contents.sectionIndex + 1, rotation: 0, rects: [], anchor: anchored?.anchor || null, epub: { cfi, href: section.href, label: labelFor(contents.sectionIndex, section.href) }, context: context.slice(0, 6000) });
        } catch (e) { failure(e); }
      });
      rendition.hooks.content.register((contents: Contents) => {
        if (root.current) bridgeReaderFileDrops(contents.document, root.current);
        contents.document.addEventListener('click', event => {
          const image = (event.target as Element)?.closest('img');
          if (!image || !/^(blob:|data:image\/)/.test(image.src)) return;
          const range = contents.document.createRange(); range.selectNode(image);
          const section = book!.spine.get(contents.sectionIndex);
          setSelection({ draftId: crypto.randomUUID(), quote: image.alt || 'Selected illustration', pageNumber: contents.sectionIndex + 1, rotation: 0, rects: [], anchor: null, kind: 'area', epub: { cfi: contents.cfiFromRange(range), href: section.href, label: labelFor(contents.sectionIndex, section.href) }, context: image.parentElement?.textContent?.slice(0, 6000) || '', image: image.src });
        });
        contents.document.addEventListener('keydown', event => epubNavigationKey(event, turn));
        if (flow === 'paginated') contents.document.addEventListener('wheel', epubWheelNavigation(turn), { passive: false });
      });
      try { await rendition.display(position.current); }
      catch {
        if (cancelled || controller.signal.aborted) return;
        position.current = undefined; await rendition.display();
        if (!cancelled) setNotice('The saved location is unavailable; opened the beginning of this book.');
      }
      if (cancelled || controller.signal.aborted) return;
      resize = new ResizeObserver(() => {
        const box = host.current?.getBoundingClientRect();
        // Resizing clears EPUB.js views. Before its first relocated event there
        // is no location to redisplay, so an eager observer can erase the book.
        if (located && box && box.width > 0 && box.height > 0) rendition?.resize(box.width, box.height);
      });
      resize.observe(host.current!);
    })().catch(error => { window.clearTimeout(timeout); failure(error); });
    return () => {
      cancelled = true; controller.abort(); window.clearTimeout(timeout); resize?.disconnect(); theme?.disconnect();
      turnRef.current = () => {};
      renditionRef.current = null; bookRef.current = null; bytesRef.current = null;
      // Destroy the book once outstanding open/display promises settle to avoid stale hooks.
      if (book?.isOpen) book.destroy();
      else if (book) void book.opened.then(() => book?.destroy(), () => {});
    };
    // File identity owns the entire engine; controls update the current rendition below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file.path, file.url, attempt, flow]);

  useEffect(() => {
    const element = host.current;
    if (!element || flow !== 'paginated') return;
    const wheel = epubWheelNavigation(direction => turnRef.current(direction));
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, [flow]);
  useEffect(() => { const r = renditionRef.current; if (r && ready) { r.themes.fontSize(`${font}%`); r.spread(spread); persist(); } }, [font, spread, ready]);
  useEffect(() => {
    const r = renditionRef.current; if (!r || !ready) return;
    const applied: { cfi: string; type: string }[] = [];
    for (const note of state.comments) if (note.epub) {
      try {
        const type = note.kind === 'area' ? 'mark' : 'highlight';
        r.annotations.add(type, note.epub.cfi, {}, () => { readerActions.focusNote(file.path, note.commentId); callbacks.current.onNotes(); }, 'diane-epub-highlight', { fill: '#e2b44f', 'fill-opacity': '.3', 'mix-blend-mode': 'multiply' });
        applied.push({ cfi: note.epub.cfi, type });
      } catch { readerActions.setStale(file.path, note.commentId, 'missing'); }
    }
    for (const tag of tray.tags) if (tag.selection?.epub) {
      try { r.annotations.underline(tag.selection.epub.cfi, {}, () => { setReferences(true); setPreview(tag.tagId); }); applied.push({ cfi: tag.selection.epub.cfi, type: 'underline' }); } catch { /* A reference remains inspectable in its tray. */ }
    }
    return () => { if (renditionRef.current !== r) return; for (const entry of applied) r.annotations.remove(entry.cfi, entry.type); };
  }, [ready, state.comments, tray.tags, file.path]);
  useEffect(() => {
    const book = bookRef.current, r = renditionRef.current;
    if (!ready || !state.focus || !book || !r) return;
    const note = state.comments.find(note => note.commentId === state.focus?.commentId);
    if (!note?.epub) return;
    let cancelled = false;
    void (async () => {
      const section = book.spine.get(note.epub!.href) || book.spine.get(note.pageNumber - 1);
      await section.load(book.load.bind(book));
      if (cancelled || bookRef.current !== book) return;
      let cfi = note.epub!.cfi;
      if (note.kind !== 'area' && note.anchor) {
        const range = anchorRange(section.document.querySelector('body') as HTMLElement, note.anchor);
        if (!range || normalizePassageText(range.toString()) !== normalizePassageText(note.quote)) throw Error('Passage not found in this version.');
        cfi = section.cfiFromRange(range);
        if (cfi !== note.epub!.cfi) readerActions.setComments(file.path, state.comments.map(item => item.commentId === note.commentId ? { ...item, epub: { ...note.epub!, cfi } } : item));
      }
      await r.display(cfi);
      if (!cancelled) readerActions.setStale(file.path, note.commentId, null);
    })().catch(() => { if (!cancelled) readerActions.setStale(file.path, note.commentId, 'missing'); });
    return () => { cancelled = true; };
  }, [ready, state.focus]);

  const updateTray = (next: PaperReferenceTray) => {
    setTray(next); try { writePaperReferenceTray(file.path, next); } catch { setNotice('References could not be saved on this device.'); }
  };
  const reference = (locationOnly = false) => {
    const saved = (!locationOnly && selection) || (location ? { quote: labelFor(location.start.index, location.start.href), pageNumber: location.start.index + 1, rotation: 0 as const, rects: [], epub: { cfi: location.start.cfi, href: location.start.href, label: labelFor(location.start.index, location.start.href) } } : null);
    if (!saved) return;
    if (tray.tags.length >= MAX_PAPER_REFERENCE_TAGS) { setNotice('Remove a reference before adding another (8 maximum).'); setReferences(true); return; }
    const { quote, pageNumber, rotation, rects, epub, kind } = saved as ReaderDraft;
    updateTray({ ...tray, tags: [...tray.tags, { selection: { quote, pageNumber, rotation, rects, epub, kind }, createdAt: new Date().toISOString(), label: quote.slice(0, 60), lineId: tray.lines[0].lineId, materialId: file.path, pageNumber, tagId: crypto.randomUUID() }] }); setReferences(true); setReferencesPinned(true);
  };
  const ask = async () => {
    if (!selection || busy) return;
    const selected = selection; setBusy(true); setNotice('');
    const currentBook = bookRef.current;
    try {
      let attachment;
      if (selected.image) {
        const blob = selected.crop || await (await fetch(selected.image)).blob();
        const extension = blob.type === 'image/svg+xml' ? 'svg' : blob.type === 'image/jpeg' ? 'jpg' : 'png';
        const prepared = await prepareAttachments([new File([blob], `epub-illustration.${extension}`, { type: blob.type })], 'EPUB illustration');
        if (prepared.errors.length || !prepared.attachments[0]) throw Error(prepared.errors.join('; ') || 'Could not prepare illustration.');
        attachment = prepared.attachments[0];
      }
      if (bookRef.current !== currentBook) return;
      callbacks.current.onAsk({ file, quote: selected.quote, pageNumber: selected.pageNumber, locationLabel: selected.epub!.label, pageText: selected.context, anchor: null, attachment });
    } catch (e) { setNotice(e instanceof Error ? e.message : 'Could not prepare selection.'); }
    finally { setBusy(false); }
  };
  const exportBook = async () => {
    const book = bookRef.current, bytes = bytesRef.current; if (!book || !bytes) return;
    setBusy(true); setNotice('');
    try { const blob = await exportAnnotatedEpub(bytes, book, state.comments); downloadEpubBlob(file.name.replace(/\.epub$/i, '-annotated.epub'), blob); }
    catch (e) { setNotice(e instanceof Error ? e.message : 'Could not export EPUB.'); }
    finally { setBusy(false); }
  };
  const go = (target: string) => { prioritizeEpubInput(); void renditionRef.current?.display(target).catch(e => setNotice(e instanceof Error ? e.message : 'This location could not be opened.')); };
  return <section className="wb-epub" ref={root} tabIndex={-1} onKeyDown={event => epubNavigationKey(event.nativeEvent, direction => turnRef.current(direction))} aria-label={`Reading ${file.name}`}>
    <div className="wb-epub-toolbar">
    <ReaderToolbar
      navigation={<ReaderPageNavigation page={bookPage} total={pagination.pages.length} loading={pagination.loading} onGo={page => { const target = pagination.pages[page - 1]; if (target) go(target.cfi); }} previous={() => turnRef.current(-1)} next={() => turnRef.current(1)} canPrevious={ready && !location?.atStart} canNext={ready && !location?.atEnd} />}
      chapters={<select aria-label="Chapter" disabled={!ready} value={toc.find(item => item.href.split('#')[0] === chapter.split('#')[0])?.href || ''} onChange={e => go(e.target.value)}><option value="" disabled>Chapters</option>{toc.map((item, index) => <option key={`${item.href}:${index}`} value={item.href}>{item.label}</option>)}</select>}
      sizing={<>
        <button disabled={!ready || font <= 70} aria-label="Smaller text" onClick={() => setFont(value => Math.max(70, value - 10))}>−</button><button disabled={!ready || font === 100} aria-label="Reset text size" onClick={() => setFont(100)}>{font}%</button><button disabled={!ready || font >= 200} aria-label="Larger text" onClick={() => setFont(value => Math.min(200, value + 10))}>+</button>
      </>}
      layout={<select aria-label="Page layout" value={flow === 'paginated' ? spread : 'scroll'} onChange={e => { const value = e.target.value; setFlow(value === 'scroll' ? 'scrolled-continuous' : 'paginated'); setSpread(value === 'auto' ? 'auto' : 'none'); }}><option value="scroll">Continuous scroll</option><option value="none">Single page</option><option value="auto">Two pages</option></select>}
      references={<button disabled={!ready} aria-expanded={references} aria-pressed={referencesPinned} onMouseEnter={() => openReferences(true)} onMouseLeave={closeReferences} onFocus={() => openReferences()} onBlur={closeReferences} onClick={pinReferences}>References{tray.tags.length ? ` ${tray.tags.length}` : ''}</button>}
      actions={<>
        <button onClick={onNotes}><MessageSquareQuote size={13} />Notes{state.comments.length ? ` ${state.comments.length}` : ''}</button>
        <button disabled={!ready || busy || !state.comments.length} title="Download an EPUB copy with highlights and notes" onClick={() => void exportBook()}><Download size={13} />{busy ? 'Preparing…' : 'Export'}</button>
        <a href={file.url} download={file.name}>Download</a>
      </>}
    />
    {references && <section className="wb-epub-references" aria-label="Saved references" onMouseEnter={cancelReferenceTimers} onMouseLeave={closeReferences} onFocus={cancelReferenceTimers} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) closeReferences(); }} onKeyDown={event => { if (event.key === "Escape") { cancelReferenceTimers(); setReferencesPinned(false); setReferences(false); setPreview(null); } }}>
      <EpubReferences book={bookRef.current} tray={tray} onChange={updateTray} onKeep={() => reference(true)} onGo={go} onPin={() => setReferencesPinned(true)} preview={preview} onPreview={setPreview} page={bookPage || 1} />
    </section>}
    </div>
    {!ready && !error && <p className="wb-epub-status" role="status">Opening EPUB…</p>}
    {error && <div className="wb-epub-status" role="alert"><strong>Could not open EPUB</strong><p>{error}</p><button onClick={() => setAttempt(value => value + 1)}>Retry</button></div>}
    {notice && <p className="wb-epub-status" role="status">{notice}</p>}
    <div className="wb-epub-stage" ref={host} onPointerDown={() => root.current?.focus({ preventScroll: true })} />
    <div className="wb-epub-selection-overlay">
    {selection?.image && <EpubImageTools key={selection.epub?.cfi} image={selection.image} onSelection={(quote, rects, crop) => setSelection(current => current ? { ...current, quote, rects, crop } : null)} />}
    {selection && <div className="wb-epub-controls wb-epub-selection" aria-label="Selection actions"><q>{selection.quote}</q>
      <button disabled={busy} onClick={() => { const { context: _context, image: _image, crop: _crop, ...draft } = selection; readerActions.setDraft(file.path, draft); onNotes(); }}>Comment</button>
      <button className="primary" disabled={busy} onClick={() => void ask()}>Ask</button><button disabled={busy} onClick={() => reference()}>Ref</button><button aria-label="Dismiss selection" onClick={() => setSelection(null)}>×</button>
    </div>}
    </div>
  </section>;
}
