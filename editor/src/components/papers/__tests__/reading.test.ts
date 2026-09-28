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
