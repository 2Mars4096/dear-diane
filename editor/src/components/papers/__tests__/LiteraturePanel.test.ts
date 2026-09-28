// @vitest-environment happy-dom
import { act, createElement as h } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import LiteraturePanel from '../LiteraturePanel';
const { library, refresh } = vi.hoisted(() => ({ library: { papers: [], sessions: [], warnings: [], loading: false, error: '', settings: { sources: [{ kind: 'hugo', path: '/kb', name: 'KB' }] } }, refresh: vi.fn() }));
vi.mock('../library', async importOriginal => ({ ...await importOriginal<object>(), usePaperLibrary: () => library, refreshLibrary: refresh }));
vi.mock('../../shared/MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => h('div', {}, content) }));
let host: HTMLDivElement, root: ReturnType<typeof createRoot>;
let batch: any;
const onDocument = vi.fn(), onOpen = vi.fn(), onBrowse = vi.fn();
let calls: { url: string; init?: RequestInit }[];
const tick = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); });
function button(text: string) { const found = [...host.querySelectorAll('button')].find(b => b.textContent?.includes(text)); if (!found) throw new Error(`Missing button ${text}`); return found; }
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  batch = { id: 'b1', created: '2026-09-28T12:00:00Z', destination: '/kb', items: [
    { id: 'one', name: 'one.pdf', staged_path: '/stage/one.pdf', status: 'ready', key: 'smith2024trade', scope: 'paper', error: '', query: '', manual_bibtex: '', candidates: [], bibtex: '@article{smith2024trade}', draft: { reason: 'Title and authors match.', read_pages: [1,2], notes_markdown: '## Takeaways\nA finding (p. 2).' } },
    { id: 'two', name: 'two.pdf', staged_path: '/stage/two.pdf', status: 'needs_review', scope: 'paper', error: 'Two possible editions', query: '', manual_bibtex: '', candidates: [] },
  ] };
  calls = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    if (String(url).endsWith('/batches')) return new Response(JSON.stringify([{ ...batch, count: 2, active: 0 }]));
    if (String(url).endsWith('/apply')) { batch = { ...batch, items: batch.items.map((i: any) => i.status === 'ready' ? { ...i, status: 'imported' } : i) }; }
    return new Response(JSON.stringify(batch));
  }));
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
async function render() { await act(async () => root.render(h(LiteraturePanel, { execution: { backend: 'native_codex' }, leadLabel: 'Codex', onDocument, onOpen, onBrowse }))); await tick(); }

it('imports only ready documents while retaining unresolved items', async () => {
  await render();
  expect(host.textContent).toContain('Needs review');
  await act(async () => button('Import ready items').click());
  const call = calls.find(c => c.url.endsWith('/apply'))!;
  expect(JSON.parse(String(call.init?.body))).toEqual({ ids: ['one'] });
  expect(host.textContent).toContain('Needs review'); expect(host.textContent).toContain('Imported');
  expect(refresh).toHaveBeenCalled();
});
it('opens staged PDFs in the main reader and preserves selected details across view switches', async () => {
  await render();
  await act(async () => button('smith2024trade').click());
  await act(async () => button('Open PDF').click());
  expect(onDocument).toHaveBeenCalledWith(expect.objectContaining({ name: 'one.pdf', path: '/stage/one.pdf', url: '/api/literature/batches/b1/items/one/pdf' }));
  await act(async () => host.querySelector<HTMLButtonElement>('[role=tab]')!.click());
  await act(async () => [...host.querySelectorAll<HTMLButtonElement>('[role=tab]')][1].click());
  expect(host.querySelector('[aria-label="Details for one.pdf"]')).not.toBeNull();
});
it('uploads drops to the batch without invoking document opening', async () => {
  await render();
  const event = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: { files: [new File(['%PDF-1.4'], 'new.pdf', { type: 'application/pdf' })] } });
  await act(async () => { host.querySelector('[data-literature-drop]')!.dispatchEvent(event); });
  expect(calls.some(c => c.url.includes('/pdf?name=new.pdf'))).toBe(true);
  expect(onDocument).not.toHaveBeenCalled();
});
it('retries one unresolved document using the selected lead', async () => {
  await render();
  await act(async () => button('two.pdf').click());
  await act(async () => button('Retry preparation').click());
  const call = calls.find(c => c.url.endsWith('/prepare'))!;
  expect(JSON.parse(String(call.init?.body))).toEqual({ ids: ['two'], execution: { backend: 'native_codex' } });
});
