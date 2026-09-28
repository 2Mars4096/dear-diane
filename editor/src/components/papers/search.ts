import type { Paper, ReadingSummary } from './library';
const normalize = (text: string) => text.normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase();
const tokens = (text: string) => normalize(text).match(/[\p{L}\p{N}_-]+/gu) ?? [];
// One edit or adjacent transposition; short terms must match exactly or by prefix.
function near(a: string, b: string): boolean {
  if (a.length < 4 || Math.abs(a.length - b.length) > 1) return false;
  let i = 0, j = 0, edits = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++edits > 1) return false;
    if (a.length === b.length && a[i] === b[j+1] && a[i+1] === b[j]) { i += 2; j += 2; }
    else if (a.length > b.length) i++;
    else if (b.length > a.length) j++;
    else { i++; j++; }
  }
  return edits + (a.length - i) + (b.length - j) <= 1;
}
const cache = new WeakMap<Paper, { metadata: string; words: string[]; notes: string }>();
export function searchScore(paper: Paper, query: string): number {
  let indexed = cache.get(paper);
  if (!indexed) {
    const metadata = normalize([paper.title, paper.authors, paper.key, paper.year, paper.journal, ...paper.tags, paper.abstract].join(' '));
    indexed = { metadata, words: tokens(metadata), notes: normalize(`${paper.notes} ${paper.reading_notes ?? ''}`) }; cache.set(paper, indexed);
  }
  const terms = tokens(query).slice(0, 30);
  let score = 0;
  for (const term of terms) {
    if (normalize(paper.key) === term) score += 12;
    else if (normalize(paper.title).includes(term)) score += 8;
    else if (indexed.metadata.includes(term)) score += 5;
    else if (indexed.notes.includes(term)) score += 1;
    else if (indexed.words.some(word => near(term, word))) score += 2;
    else return -1;
  }
  return score;
}
export type Filters = { query: string; tag: string; year: string; journal: string; sort: 'relevance' | 'added' | 'opened' | 'year' | 'title'; scope: 'all' | 'reading' | 'pinned' };
export const defaultFilters: Filters = { query: '', tag: '', year: '', journal: '', sort: 'relevance', scope: 'all' };
export function findPapers(papers: Paper[], sessions: ReadingSummary[], filters: Filters) {
  const saved = new Map(sessions.map(session => [session.paper_id, session]));
  return papers.filter(paper => (!filters.tag || paper.tags.includes(filters.tag)) && (!filters.year || paper.year === filters.year) && (!filters.journal || paper.journal === filters.journal)
    && (filters.scope !== 'reading' || saved.has(paper.id)) && (filters.scope !== 'pinned' || saved.get(paper.id)?.pinned))
    .map(paper => ({ paper, score: searchScore(paper, filters.query) })).filter(item => item.score >= 0)
    .sort((a,b) => {
      if (filters.sort === 'title') return a.paper.title.localeCompare(b.paper.title);
      if (filters.sort === 'year') return b.paper.year.localeCompare(a.paper.year) || a.paper.title.localeCompare(b.paper.title);
      if (filters.sort === 'opened') return (saved.get(b.paper.id)?.last_opened ?? '').localeCompare(saved.get(a.paper.id)?.last_opened ?? '') || a.paper.title.localeCompare(b.paper.title);
      if (filters.sort === 'relevance') {
        if (filters.query.trim() && a.score !== b.score) return b.score - a.score;
        if (!filters.query.trim()) {
          const pin = Number(saved.get(b.paper.id)?.pinned ?? false) - Number(saved.get(a.paper.id)?.pinned ?? false);
          if (pin) return pin;
          const opened = (saved.get(b.paper.id)?.last_opened ?? '').localeCompare(saved.get(a.paper.id)?.last_opened ?? '');
          if (opened) return opened;
        }
      }
      return b.paper.added.localeCompare(a.paper.added) || a.paper.title.localeCompare(b.paper.title);
    }).map(item => item.paper);
}
