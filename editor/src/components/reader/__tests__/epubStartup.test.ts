// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import EpubReader from '../EpubReader';
const mock = vi.hoisted(() => ({ events: {} as Record<string, (value: unknown) => void>, resize: vi.fn(), spread: vi.fn(), open: vi.fn(async () => {}), destroy: vi.fn(), resources: vi.fn(() => Promise.resolve()) }));
vi.mock('epubjs', () => ({ default: () => ({
  isOpen: true, open: () => mock.open(), opened: mock.resources(), destroy: mock.destroy,
  spine: { hooks: { content: { register() {} }, serialize: { register() {} } } },
  loaded: { navigation: Promise.resolve({ toc: [{ href: 'chapter.xhtml', label: 'Chapter 1', subitems: [] }] }) },
  renderTo: () => ({
    on: (event: string, callback: (value: unknown) => void) => { mock.events[event] = callback; },
    themes: { default() {}, fontSize() {}, register() {}, registerCss() {}, select() {} }, hooks: { content: { register() {} } },
    q: { enqueue: (fn: () => void) => Promise.resolve(fn()) }, display: async () => {}, resize: mock.resize, spread: mock.spread,
  }),
}) }));
vi.mock('../lib/epub', () => ({ EPUB_MAX_BYTES: 150000000, checkEpub: async () => {}, protectEpubDocument() {} }));
vi.mock('../useEpubPagination', () => ({ useEpubPagination: () => ({ loading: false, pages: Array.from({ length: 12 }, (_, index) => ({ section: 0, page: index + 1, cfi: 'epubcfi(/6/2!/4/2)' })) }) }));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
it('preserves the first page until relocation, before enabling controls and resizing', async () => {
  localStorage.setItem('diane.reader.epub.v1:/startup.epub', JSON.stringify({ flow: 'paginated' }));
  const observers: (() => void)[] = [];
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { observers.push(callback); } observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, headers: new Headers(), arrayBuffer: async () => new ArrayBuffer(1) }));
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 871.25, height: 824 } as DOMRect);
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(EpubReader, { file: { path: '/startup.epub', name: 'startup.epub', url: '/startup.epub' }, onAsk() {}, onNotes() {} })));
    await act(async () => observers.at(-1)!());
    expect(mock.resize).not.toHaveBeenCalled(); expect(mock.spread).not.toHaveBeenCalled();
    expect(host.textContent).toContain('Opening EPUB');
    await act(async () => mock.events.relocated({ start: { cfi: 'epubcfi(/6/2!/4/2)', href: 'chapter.xhtml', index: 0, displayed: { page: 2, total: 12 } }, atStart: false, atEnd: false }));
    await act(async () => observers.at(-1)!());
    expect(mock.resize).toHaveBeenCalledWith(871.25, 824, 'epubcfi(/6/2!/4/2)');
    expect(host.textContent).not.toContain('Opening EPUB');
    expect(host.querySelector<HTMLInputElement>('[aria-label="Page number"]')?.value).toBe('2');
    expect(host.querySelector<HTMLInputElement>('[aria-label="Page number"]')?.max).toBe('12');
    expect(host.querySelector('[aria-label="Smaller text"]')?.textContent).toBe('−');
    expect(host.querySelector('[aria-label="Larger text"]')?.textContent).toBe('+');
    vi.useFakeTimers();
    const referenceButton = [...host.querySelectorAll('button')].find(button => button.textContent === 'References')!;
    await act(async () => { referenceButton.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); vi.advanceTimersByTime(140); });
    expect(host.querySelector('[aria-label="Saved references"]')).not.toBeNull();
    await act(async () => { referenceButton.dispatchEvent(new MouseEvent('mouseout', { bubbles: true })); vi.advanceTimersByTime(240); });
    expect(host.querySelector('[aria-label="Saved references"]')).toBeNull();
    await act(async () => referenceButton.click());
    await act(async () => { referenceButton.dispatchEvent(new MouseEvent('mouseout', { bubbles: true })); vi.advanceTimersByTime(240); });
    expect(referenceButton.getAttribute('aria-pressed')).toBe('true');
    expect(host.querySelector('[aria-label="Saved references"]')).not.toBeNull();
    await act(async () => referenceButton.click());
    expect(host.querySelector('[aria-label="Saved references"]')).toBeNull();
  } finally { await act(async () => root.unmount()); host.remove(); }
});

it('waits for resource initialization before destroying an already-open book', async () => {
  let finish!: () => void;
  mock.destroy.mockClear();
  mock.open.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok:true, headers:new Headers(), arrayBuffer:async()=>new ArrayBuffer(1) }));
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({width:820,height:800} as DOMRect);
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  await act(async()=>root.render(createElement(EpubReader,{file:{path:'/pending.epub',name:'pending.epub',url:'/pending.epub'},onAsk(){},onNotes(){}})));
  await act(async()=>root.unmount());
  expect(mock.destroy).not.toHaveBeenCalled();
  await act(async()=>finish());
  expect(mock.destroy).toHaveBeenCalledOnce();
  host.remove();
});

it('does not destroy resources between open() and the later opened promise', async () => {
  let finishResources!: () => void;
  mock.destroy.mockClear();
  mock.resources.mockImplementationOnce(() => new Promise<void>(resolve => { finishResources = resolve; }));
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:true,headers:new Headers(),arrayBuffer:async()=>new ArrayBuffer(1)}));
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockReturnValue({width:820,height:800} as DOMRect);
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  await act(async()=>root.render(createElement(EpubReader,{file:{path:'/resources.epub',name:'resources.epub',url:'/resources.epub'},onAsk(){},onNotes(){}})));
  await act(async()=>root.unmount());
  expect(mock.destroy).not.toHaveBeenCalled();
  await act(async()=>finishResources());
  expect(mock.destroy).toHaveBeenCalledOnce();host.remove();
});
