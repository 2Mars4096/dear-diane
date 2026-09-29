import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { BookOpen, Search, Pin, X, SlidersHorizontal, ArrowUpRight, ChevronRight } from 'lucide-react';
import MarkdownRenderer from '../shared/MarkdownRenderer';
import { citation, jsonBody, paperRequest, pinPaper, refreshLibrary, usePaperLibrary, type Paper, type PaperSource } from './library';
import { defaultFilters, findPapers, type Filters } from './search';
import { downloadPendingReading, reloadSavedReading, retryReadingSave, useReadingStatus } from './reading';
import './papers.css';

type Props = { onOpen: (paper: Paper) => Promise<void>; onBrowse: () => void; onReference: (papers: Paper[]) => void };
const shortcut = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘K' : 'Ctrl+K';
const preferenceEvent = 'dan:paper-preferences';
function useSidebarVisible() {
  return useSyncExternalStore(callback => { window.addEventListener(preferenceEvent, callback); return () => window.removeEventListener(preferenceEvent, callback); }, () => localStorage.getItem('dan.papers.sidebar') !== 'hidden');
}
function showSidebar(show: boolean) { localStorage.setItem('dan.papers.sidebar', show ? 'visible' : 'hidden'); window.dispatchEvent(new Event(preferenceEvent)); }
function PaperMeta({ paper }: { paper: Paper }) { return <span className="papers-meta">{[paper.authors.replace(/ and /g, '; '), paper.year, paper.journal].filter(Boolean).join(' · ') || paper.source}</span>; }
export function PaperSidebar({ onOpen, onBrowse }: Pick<Props, 'onOpen' | 'onBrowse'>) {
  const library = usePaperLibrary();
  const visible = useSidebarVisible();
  const [error, setError] = useState('');
  if (!visible) return null;
  const recent = library.sessions.slice(0, 5);
  return <section className="papers-sidebar" aria-label="Reading sessions">
    <button className="papers-sidebar-entry" onClick={onBrowse}><BookOpen size={16} /><span>Papers</span><kbd>{shortcut}</kbd></button>
    {recent.length > 0 && <><div className="papers-sidebar-caption">Continue reading</div>{recent.map(session => {
      const paper = library.papers.find(item => item.id === session.paper_id);
      return <button className="papers-sidebar-session" key={session.id} title={session.title} disabled={!paper?.available} onClick={() => { if (paper) void onOpen(paper).catch(error => setError(String(error))); }}><BookOpen size={12} /><span>{session.title}</span>{session.position && <small>p. {session.position.page}</small>}</button>;
    })}</>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
export function ReadingSessionBar({ id, onBrowse }: { id: string; onBrowse: () => void }) {
  const status = useReadingStatus(id);
  const error = status !== 'Reading session saved' && status !== 'Saving reading session…' && status !== 'Reading session';
  return <div className="papers-reading-bar"><button onClick={onBrowse}><BookOpen size={13} />Papers</button><span role="status">{status}</span>{error && <><button onClick={() => retryReadingSave(id)}>Retry save</button><button onClick={() => downloadPendingReading(id)}>Download unsynced notes</button><button onClick={() => reloadSavedReading(id)}>Download & reload saved</button></>}</div>;
}
export function PaperSearch({ onOpen, onBrowse, onClose }: Pick<Props, 'onOpen' | 'onBrowse'> & { onClose: () => void }) {
  const library = usePaperLibrary();
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const matches = useMemo(() => findPapers(library.papers, library.sessions, { ...defaultFilters, query }), [library.papers, library.sessions, query]);
  const recentIds = new Set(library.sessions.map(session => session.paper_id));
  const results = (query.trim() ? matches : matches.filter(paper => recentIds.has(paper.id))).slice(0, 12);
  useEffect(() => { const previous = document.activeElement as HTMLElement; dialog.current?.showModal(); input.current?.focus(); return () => { dialog.current?.close(); previous?.focus(); }; }, []);
  useEffect(() => { setIndex(0); }, [query]);
  useEffect(() => { document.getElementById(`paper-result-${index}`)?.scrollIntoView({ block: 'nearest' }); }, [index]);
  const selectedIndex = Math.min(index, Math.max(0, results.length - 1));
  async function open(paper: Paper) { if (busy) return; setBusy(true); setError(''); try { await onOpen(paper); onClose(); } catch (error) { setError(String(error)); } finally { setBusy(false); } }
  return <dialog ref={dialog} className="wb-switcher papers-search-dialog" aria-label="Find a paper" onCancel={event => { event.preventDefault(); onClose(); }}>
    <header><Search size={19} /><input ref={input} role="combobox" aria-label="Find a paper" aria-controls="paper-search-results" aria-expanded="true" aria-autocomplete="list" aria-activedescendant={results.length ? `paper-result-${selectedIndex}` : undefined} placeholder="Title, author, idea, or citation key…" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => {
      if (event.nativeEvent.isComposing) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setIndex(current => Math.max(0, Math.min(results.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)))); }
      if (event.key === 'Enter' && results[selectedIndex]) { event.preventDefault(); void open(results[selectedIndex]); }
    }} /><button onClick={onClose} aria-label="Close paper search"><X size={18} /></button></header>
    <div className="papers-search-caption">{query.trim() ? `${matches.length} matching papers` : 'Pinned & recently opened'}</div>
    <div id="paper-search-results" role="listbox" aria-label="Papers" aria-busy={busy || library.loading}>
      {results.map((paper, row) => <div id={`paper-result-${row}`} key={paper.id} role="option" aria-selected={selectedIndex === row} className="papers-search-result" onMouseEnter={() => setIndex(row)} onClick={() => void open(paper)}><BookOpen size={17} /><div><strong>{paper.title}</strong><PaperMeta paper={paper} /><small>{paper.key}{!paper.available ? ' · PDF missing' : ''}</small></div><ChevronRight size={15} /></div>)}
      {!results.length && <p className="papers-empty">{library.loading ? 'Loading papers…' : query.trim() ? 'No matches. Try fewer words or browse the library.' : 'Open a paper from the library. Your reading sessions will appear here.'}</p>}
    </div>
    {(error || library.error) && <p className="papers-error" role="alert">{error || library.error}<button onClick={() => void refreshLibrary()}>Retry</button></p>}
    <footer><span>↑↓ select · Enter open · Esc close</span><button onClick={() => { onClose(); onBrowse(); }}>Browse all papers <ArrowUpRight size={14} /></button></footer>
  </dialog>;
}

function SourceSettings({ sources, onSaved }: { sources: PaperSource[]; onSaved: () => void }) {
  const [draft, setDraft] = useState(sources);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const visible = useSidebarVisible();
  return <section className="papers-source-settings" aria-label="Paper library settings">
    <h2>Paper sources</h2><p>Folders on this Dear Diane host. Hugo sources use content/papers and static/papers, just like Notes.</p>
    {draft.map((source, index) => <div className="papers-source-row" key={index}><select aria-label={`Source ${index + 1} type`} value={source.kind} onChange={event => setDraft(items => items.map((item, i) => i === index ? { ...item, kind: event.target.value as PaperSource['kind'] } : item))}><option value="hugo">Knowledge base</option><option value="pdf">PDF folder</option></select><input aria-label={`Source ${index + 1} folder`} placeholder="/path/to/my-knowledge-base" value={source.path} onChange={event => setDraft(items => items.map((item, i) => i === index ? { ...item, path: event.target.value, name: '' } : item))} /><button aria-label={`Remove source ${index + 1}`} onClick={() => setDraft(items => items.filter((_, i) => i !== index))}><X size={16} /></button></div>)}
    <div className="papers-actions"><button onClick={() => setDraft(items => [...items, { kind: 'hugo', path: '', name: '' }])}>Add source</button><button disabled={saving || draft.some(source => !source.path.trim())} onClick={async () => { setSaving(true); setError(''); try { await paperRequest('/settings', jsonBody({ sources: draft })); await refreshLibrary(); onSaved(); } catch (error) { setError(String(error)); } finally { setSaving(false); } }}>{saving ? 'Saving…' : 'Save sources'}</button></div>
    <label><input type="checkbox" checked={visible} onChange={event => showSidebar(event.target.checked)} /> Show Papers and reading sessions in sidebar</label>
    {error && <p className="papers-error" role="alert">{error}</p>}
  </section>;
}
function preferences(): Filters & { view: 'list' | 'table' } {
  try { return { ...defaultFilters, view: 'list', ...JSON.parse(localStorage.getItem('dan.papers.view.v1') || '{}') }; } catch { return { ...defaultFilters, view: 'list' }; }
}
export function PaperLibrary({ onOpen, onReference }: Pick<Props, 'onOpen' | 'onReference'>) {
  const library = usePaperLibrary();
  const [filters, setFilters] = useState(preferences);
  const [selected, setSelected] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [settings, setSettings] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => { localStorage.setItem('dan.papers.view.v1', JSON.stringify(filters)); }, [filters]);
  useEffect(() => { if (scroller.current) scroller.current.scrollTop = Number(sessionStorage.getItem('dan.papers.scroll') || 0); }, [library.loading]);
  const results = useMemo(() => findPapers(library.papers, library.sessions, filters), [library.papers, library.sessions, filters]);
  const details = library.papers.find(paper => paper.id === selected);
  const sessions = new Map(library.sessions.map(session => [session.paper_id, session]));
  const facets = (field: 'tags' | 'year' | 'journal') => [...new Set(library.papers.flatMap(paper => field === 'tags' ? paper.tags : [paper[field]]).filter(Boolean))].sort((a,b) => field === 'year' ? b.localeCompare(a) : a.localeCompare(b));
  const change = (patch: Partial<typeof filters>) => { setFilters(current => ({ ...current, ...patch })); if (scroller.current) scroller.current.scrollTop = 0; };
  async function open(paper: Paper) { if (busy) return; setBusy(paper.id); setError(''); try { await onOpen(paper); } catch (error) { setError(String(error)); } finally { setBusy(''); } }
  const selectedPapers = library.papers.filter(paper => checked.has(paper.id));
  async function copy(text: string, label: string) { try { await navigator.clipboard.writeText(text); setNotice(`${label} copied`); } catch { setError('Clipboard unavailable. Select the citation text in paper details to copy it.'); } }
  return <div className="papers-library">
    <header className="papers-heading"><div><h1>Papers <small>{library.papers.length}</small></h1><p>Find a paper. Pick up where you left off.</p></div><button aria-label="Paper library settings" aria-expanded={settings} onClick={() => setSettings(!settings)}><SlidersHorizontal size={18} /></button></header>
    {settings && <SourceSettings sources={library.settings.sources} onSaved={() => setSettings(false)} />}
    <div className="papers-controls"><label className="papers-query"><Search size={17} /><input aria-label="Search papers" placeholder="Search titles, authors, keywords, or reading notes…" value={filters.query} onChange={event => change({ query: event.target.value })} /></label>
      <div className="papers-filters"><select aria-label="Paper collection" value={filters.scope} onChange={event => change({ scope: event.target.value as Filters['scope'] })}><option value="all">All papers</option><option value="reading">Reading sessions</option><option value="pinned">Pinned</option></select>
        {(['tags','year','journal'] as const).map(field => { const key = field === 'tags' ? 'tag' : field; return <select key={field} aria-label={`Filter ${field}`} value={filters[key]} onChange={event => change({ [key]: event.target.value })}><option value="">{field === 'tags' ? 'All tags' : field === 'year' ? 'All years' : 'All journals'}</option>{facets(field).map(value => <option key={value}>{value}</option>)}</select>; })}
        <select aria-label="Sort papers" value={filters.sort} onChange={event => change({ sort: event.target.value as Filters['sort'] })}><option value="relevance">Relevance</option><option value="added">Recently added</option><option value="opened">Last opened</option><option value="year">Year, newest</option><option value="title">Title A–Z</option></select>
        <button onClick={() => change({ view: filters.view === 'list' ? 'table' : 'list' })}>{filters.view === 'list' ? 'Table view' : 'List view'}</button>
      </div>
    </div>
    <div className="papers-results-toolbar"><span>{results.length} papers{selectedPapers.length ? ` · ${selectedPapers.length} selected` : ''}</span><div>{selectedPapers.length > 0 && <><button onClick={() => { onReference(selectedPapers); setChecked(new Set()); }}>Add {selectedPapers.length} to chat</button><button onClick={() => setChecked(new Set())}>Clear selection</button></>}<button onClick={() => void refreshLibrary()}>Refresh</button></div></div>
    {(error || library.error) && <p className="papers-error" role="alert">{error || library.error}</p>}
    {notice && <p className="papers-notice" role="status">{notice}</p>}
    {library.warnings.map(warning => <p className="papers-error" key={warning}>{warning}</p>)}
    <div className="papers-body"><div className="papers-results" ref={scroller} onScroll={event => sessionStorage.setItem('dan.papers.scroll', String(event.currentTarget.scrollTop))}>
      {library.loading ? <p className="papers-empty">Loading your paper library…</p> : !results.length ? <div className="papers-empty"><h2>{library.papers.length ? 'No matching papers' : 'Connect your papers'}</h2><p>{library.papers.length ? 'Try fewer words or clear the filters.' : 'Add your knowledge-base folder or a folder of PDFs to start browsing.'}</p><button onClick={() => library.papers.length ? change(defaultFilters) : setSettings(true)}>{library.papers.length ? 'Clear filters' : 'Set up paper sources'}</button></div> : filters.view === 'table' ? <div className="papers-table-wrap"><table><thead><tr><th>Select</th><th>Title</th><th>Authors</th><th>Year</th><th>Journal</th><th>Details</th></tr></thead><tbody>{results.map(paper => <tr key={paper.id}><td><input type="checkbox" aria-label={`Select ${paper.title}`} checked={checked.has(paper.id)} onChange={event => setChecked(current => { const next = new Set(current); if (event.target.checked) next.add(paper.id); else next.delete(paper.id); return next; })} /></td><td><button disabled={!paper.available || Boolean(busy)} onClick={() => void open(paper)}>{paper.title}</button>{!paper.available && <small>PDF missing</small>}</td><td>{paper.authors}</td><td>{paper.year}</td><td>{paper.journal}</td><td><button aria-label={`Details for ${paper.title}`} onClick={() => setSelected(paper.id)}>Details</button></td></tr>)}</tbody></table></div> : <ul className="papers-list">{results.map(paper => {
        const session = sessions.get(paper.id);
        return <li key={paper.id} data-selected={selected === paper.id || undefined}><input type="checkbox" aria-label={`Select ${paper.title}`} checked={checked.has(paper.id)} onChange={event => setChecked(current => { const next = new Set(current); if (event.target.checked) next.add(paper.id); else next.delete(paper.id); return next; })} /><div className="papers-row-body"><button className="papers-title" disabled={!paper.available || Boolean(busy)} onClick={() => void open(paper)}>{paper.title}</button><PaperMeta paper={paper} /><div className="papers-row-tags">{paper.tags.slice(0, 5).map(tag => <button key={tag} onClick={() => change({ tag })}>{tag}</button>)}<small>{paper.key}</small></div><small>{!paper.available ? 'PDF missing' : busy === paper.id ? 'Opening…' : session ? `Continue reading${session.position ? ` · page ${session.position.page}` : ''}` : ''}</small></div><div className="papers-row-actions"><button aria-label={`${session?.pinned ? 'Unpin' : 'Pin'} ${paper.title}`} aria-pressed={Boolean(session?.pinned)} disabled={!paper.available && !session} onClick={() => void pinPaper(paper, !session?.pinned).catch(error => setError(String(error)))}><Pin size={15} /></button><button aria-label={`Details for ${paper.title}`} onClick={() => setSelected(selected === paper.id ? null : paper.id)}><ChevronRight size={17} /></button></div></li>;
      })}</ul>}
    </div>{details && <aside className="papers-details" aria-label="Paper details"><header><span>Paper details</span><button aria-label="Close paper details" onClick={() => setSelected(null)}><X size={16} /></button></header><h2>{details.title}</h2><PaperMeta paper={details} /><div className="papers-actions"><button disabled={!details.available || Boolean(busy)} onClick={() => void open(details)}>{sessions.has(details.id) ? 'Resume reading' : 'Read paper'}</button><button onClick={() => onReference([details])}>Add to chat</button><button onClick={() => void copy(citation(details), 'Citation')}>Copy citation</button>{details.bibtex && <button onClick={() => void copy(details.bibtex, 'BibTeX')}>Copy BibTeX</button>}</div>{details.abstract && <><h3>Abstract</h3><p>{details.abstract}</p></>}{details.notes && <><h3>Reading notes</h3><MarkdownRenderer content={details.notes} /></>}<details><summary>Citation & source</summary><p>{citation(details)}</p><pre>{details.bibtex}</pre><p className="papers-source-path">{details.path || 'No PDF link'}<br />{details.note_path}</p></details></aside>}</div>
  </div>;
}
