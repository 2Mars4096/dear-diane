import { expect, it } from 'vitest';
import { defaultFilters, findPapers, searchScore } from '../search';
import type { Paper, ReadingSummary } from '../library';
const paper: Paper = { id: 'one', key: 'smith2024networks', title: 'Trade and production networks', authors: 'Smith, Anne', year: '2024', journal: 'Econometrica', tags: ['geopolitics'], abstract: 'Financial links', notes: 'Supplier substitution under tariffs.', available: true, path: '/papers/a.pdf', note_path: '/notes/a.md', source: 'KB', source_root: '/', bibtex: '', added: '2024-01-01' };
it('finds unordered words across metadata and notes, partial terms, diacritics, and small typos', () => {
  for (const query of ['networks Smith', '2024 trade', 'prod net', 'netwroks', 'geopolitcs', 'supplier tariffs', 'Économetrica']) expect(searchScore(paper, query), query).toBeGreaterThan(0);
  for (const query of ['unrelated', 'trade nonexistent', 'cat']) expect(searchScore(paper, query)).toBe(-1);
});
it('combines facets and ranks exact metadata above notes, using recent sessions for empty search', () => {
  const notesOnly = { ...paper, id: 'two', title: 'Banking', notes: 'Production networks', added: '2026-01-01' };
  expect(findPapers([notesOnly, paper], [], { ...defaultFilters, query: 'production' }).map(p => p.id)).toEqual(['one', 'two']);
  expect(findPapers([paper], [], { ...defaultFilters, tag: 'missing' })).toEqual([]);
  expect(findPapers([paper], [], { ...defaultFilters, year: '2024', journal: 'Econometrica', tag: 'geopolitics' })).toEqual([paper]);
  const session = { id: 'one', paper_id: 'one', last_opened: '2026-01-01', pinned: true } as ReadingSummary;
  expect(findPapers([notesOnly, paper], [session], defaultFilters)[0].id).toBe('one');
  expect(findPapers([notesOnly, paper], [session], { ...defaultFilters, scope: 'reading' })).toEqual([paper]);
});
