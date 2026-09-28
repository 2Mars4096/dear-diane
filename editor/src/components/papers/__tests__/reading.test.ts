// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest';
import { openLibraryPaper, saveReadingLink } from '../reading';
import { readPaperPdfPosition, writePaperPdfPosition } from '../../reader/lib/paper-reading-position';
import { readPaperComments } from '../../reader/lib/paper-comments';
import { normalizePaperReferenceTray } from '../../reader/lib/paper-reference-tags';
import type { Paper, ReadingSession } from '../library';

it('hydrates durable progress before rendering, coalesces saves, and retains conversation identity across projects', async () => {
  vi.useFakeTimers();
  const paper = { id: 'session-test', path: '/fixture/library.pdf', title: 'A paper' } as Paper;
  const comment = { commentId: 'note1', text: 'Important mechanism', quote: 'production', pageNumber: 3, rotation: 0 as const, rects: [{ top: .2, left: .2, width: .3, height: .05 }], materialId: paper.path, createdAt: '2026-01-01T00:00:00.000Z', updatedAt: '2026-01-01T00:00:00.000Z' };
  let session = { id: paper.id, paper_id: paper.id, title: paper.title, revision: 2, position: { page: 3, zoom: 1.2, top: .1, left: 0 }, comments: [comment], references: normalizePaperReferenceTray(null, paper.path), link: { threadId: 'reading-thread' }, workflow_id: '_dan_reading', last_opened: '2026-01-01', pinned: false } as ReadingSession;
  const writes: Record<string, unknown>[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/open')) return { ok: true, json: async () => ({ paper, session }) };
    const body = JSON.parse(init?.body as string);
    if (url.endsWith('/progress')) { writes.push(body); expect(body.revision).toBe(session.revision); session = { ...session, ...body, revision: session.revision + 1 }; return { ok: true, json: async () => session }; }
    if (url.endsWith('/link')) { session = { ...session, link: body }; return { ok: true, json: async () => body }; }
    throw new Error(url);
  }));
  const opened = await openLibraryPaper(paper.id);
  expect(readPaperPdfPosition(paper.path)?.page).toBe(3);
  expect(readPaperComments({ material_id: paper.path })[0].text).toBe('Important mechanism');
  expect(opened.session.link.threadId).toBe('reading-thread');
  writePaperPdfPosition(paper.path, { page: 4, zoom: 1.2, top: 0, left: 0 });
  writePaperPdfPosition(paper.path, { page: 5, zoom: 1.2, top: 0, left: 0 });
  await vi.advanceTimersByTimeAsync(600);
  expect(writes).toHaveLength(1);
  expect(session.position?.page).toBe(5);
  await saveReadingLink(paper.id, { threadId: 'reading-thread', runId: 'run-1', assistantId: 'answer-1' });
  const resumed = await openLibraryPaper(paper.id);
  expect(resumed.session.link.runId).toBe('run-1');
  expect(resumed.session.position?.page).toBe(5);
  expect(localStorage.getItem(`dan.papers.pending.v1:${paper.id}`)).toBeNull();
  vi.unstubAllGlobals(); vi.useRealTimers();
});

async function readingFixture(id: string) {
  const paper = { id, path: `/fixture/${id}.pdf`, title: id } as Paper;
  let session = { id, paper_id: id, title: id, revision: 1, position: { page: 3, zoom: 1, top: 0, left: 0 }, comments: [], references: normalizePaperReferenceTray(null, paper.path), link: { threadId: id }, workflow_id: '_dan_reading', last_opened: '2026-01-01', pinned: false } as ReadingSession;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/open')) return { ok: true, json: async () => ({ paper, session: structuredClone(session) }) };
    const body = JSON.parse(init?.body as string);
    if (body.revision !== session.revision) return { ok: false, status: 409, json: async () => ({ detail: 'Reading session changed elsewhere' }), text: async () => JSON.stringify({ detail: 'Reading session changed elsewhere' }) };
    session = { ...session, ...body, revision: session.revision + 1 };
    return { ok: true, json: async () => structuredClone(session) };
  });
  vi.stubGlobal('fetch', fetcher);
  await openLibraryPaper(id);
  return { paper, fetcher, advance: () => { session = { ...session, revision: session.revision + 1, position: { ...session.position!, page: 9 } }; }, saved: () => session };
}

it('reloads server progress after closing, while preserving an active reader', async () => {
  const { closeLibraryPaper } = await import('../reading');
  const fixture = await readingFixture('closed-reopen');
  fixture.advance();
  expect((await openLibraryPaper(fixture.paper.id)).session.position?.page).toBe(3);
  await closeLibraryPaper(fixture.paper.id);
  const reopened = await openLibraryPaper(fixture.paper.id);
  expect(reopened.session.revision).toBe(2);
  expect(readPaperPdfPosition(fixture.paper.path)?.page).toBe(9);
  await closeLibraryPaper(fixture.paper.id);
  vi.unstubAllGlobals();
});

it('waits for an in-flight save before reopening and coalesces simultaneous opens', async () => {
  vi.useFakeTimers();
  const { closeLibraryPaper } = await import('../reading');
  const fixture = await readingFixture('pending-reopen');
  const implementation = fixture.fetcher.getMockImplementation()!;
  let finish!: () => void;
  const gate = new Promise<void>(resolve => { finish = resolve; });
  fixture.fetcher.mockImplementation(async (url, init) => {
    if (url.endsWith('/progress')) await gate;
    return implementation(url, init);
  });
  writePaperPdfPosition(fixture.paper.path, { page: 5, zoom: 1, top: 0, left: 0 });
  await vi.advanceTimersByTimeAsync(500);
  const closing = closeLibraryPaper(fixture.paper.id);
  const first = openLibraryPaper(fixture.paper.id), second = openLibraryPaper(fixture.paper.id);
  expect(first).toBe(second);
  finish(); await closing;
  expect((await first).session.position?.page).toBe(5);
  expect(fixture.saved().revision).toBe(2);
  await closeLibraryPaper(fixture.paper.id);
  vi.unstubAllGlobals(); vi.useRealTimers();
});

it('preserves unsynced progress on conflict and reloads the saved revision after close', async () => {
  vi.useFakeTimers();
  const { closeLibraryPaper } = await import('../reading');
  const fixture = await readingFixture('conflict-reopen');
  fixture.advance();
  writePaperPdfPosition(fixture.paper.path, { page: 5, zoom: 1, top: 0, left: 0 });
  await closeLibraryPaper(fixture.paper.id);
  const reopened = await openLibraryPaper(fixture.paper.id);
  expect(reopened.session.position?.page).toBe(9);
  const recovery = JSON.parse(localStorage.getItem(`dan.papers.pending.v1:${fixture.paper.id}`)!);
  expect(recovery.revision).toBe(1);
  expect(recovery.position.page).toBe(5);
  await vi.advanceTimersByTimeAsync(600);
  expect(fixture.saved().position?.page).toBe(9);
  await closeLibraryPaper(fixture.paper.id);
  vi.unstubAllGlobals(); vi.useRealTimers();
});
