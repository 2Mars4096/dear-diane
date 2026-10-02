import { describe, it, expect } from 'vitest';
import { normalizePaperReferenceTags, normalizePaperReferenceTray } from '../lib/paper-reference-tags';
import { normalizePaperComments } from '../lib/paper-comments';
import { selectionBounds } from '../lib/selection-image';

const rects = [{ left: .6, top: .7, width: .2, height: .05 }];
const page = { tagId: 'page-2', pageNumber: 2, label: 'Page 2', materialId: 'paper', createdAt: '2026-10-02T00:00:00.000Z', lineId: 'saved' };
const selected = (id: string, kind = 'text') => ({ ...page, tagId: id, selection: { pageNumber: 2, kind, quote: 'Passage', rects, rotation: 0 } });

describe('selected references', () => {
  it('keeps multiple passages and the whole-page ref on the same page through persistence', () => {
    const tags = [page, selected('a'), selected('b', 'area')];
    const tray = normalizePaperReferenceTray(JSON.parse(JSON.stringify({ version: 2, tags, lines: [{ lineId: 'saved', label: 'Saved' }] })), 'paper');
    expect(tray.tags).toEqual(tags);
  });
  it('still deduplicates legacy page refs and rejects malformed geometry', () => {
    const tags = normalizePaperReferenceTags([page, { ...page, label: 'New label' }, { ...selected('bad'), selection: { rects: [{ left: NaN, top: 0, width: 0, height: .2 }] } }], 'paper');
    expect(tags).toHaveLength(1);
    expect(tags[0].selection).toBeUndefined();
  });
  it('preserves area notes without a text anchor', () => {
    const notes = normalizePaperComments([{ ...page, kind: 'area', commentId: 'area', quote: 'Selected area', rects, rotation: 0, updatedAt: page.createdAt, text: '' }], 'paper');
    expect(notes[0]).toMatchObject({ kind: 'area', rects });
  });
  it('bounds only the chosen region across multiple lines', () => {
    expect(selectionBounds([{ left: .6, top: .7, width: .2, height: .05 }, { left: .62, top: .76, width: .1, height: .03 }])).toEqual({ left: .6, top: .7, width: .8 - .6, height: .79 - .7 });
  });
});
