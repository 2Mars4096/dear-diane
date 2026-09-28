import { useEffect, useRef, useState } from 'react';
import { BookOpen, FileUp, Plus, RefreshCw, Search } from 'lucide-react';
import MarkdownRenderer from '../shared/MarkdownRenderer';
import type { DocumentFile } from '../documents/documents';
import type { executeChatV2AgentRun } from '../../lib/chatV2Api';
import { pathFileTarget } from '../../lib/fileTargets';
import { requestJson } from '../../lib/http';
import { jsonBody, paperRequest, refreshLibrary, usePaperLibrary, type Paper } from './library';
import { defaultFilters, findPapers } from './search';
import './literature.css';

type Execution = Parameters<typeof executeChatV2AgentRun>[1];
type Candidate = { bibtex: string; title: string; authors: string; year: string; source: string };
type Draft = { reason: string; abstract: string; tags: string[]; notes_markdown: string; read_pages: number[] };
export type IntakeItem = { id: string; name: string; staged_path: string; status: string; scope: 'paper' | 'book_overview'; error: string; warning?: string; query: string; manual_bibtex: string; bibtex?: string; key?: string; candidates: Candidate[]; draft?: Draft; citation_source?: string; pages?: number; run_id?: string; note_path?: string; paper_id?: string; existing_paper_id?: string };
export type IntakeBatch = { id: string; created: string; destination: string; items: IntakeItem[] };
type BatchSummary = Pick<IntakeBatch, 'id' | 'created' | 'destination'> & { count: number; active: number };
const active = (item: IntakeItem) => ['queued', 'matching', 'reading', 'importing', 'stopping'].includes(item.status);
const labels: Record<string, string> = { staged: 'Awaiting preparation', queued: 'Queued', matching: 'Finding citation', reading: 'Checking and reading', stopping: 'Stopping', ready: 'Ready to import', needs_review: 'Needs review', duplicate: 'Already in library', imported: 'Imported', importing: 'Saving', interrupted: 'Interrupted', failed: 'Preparation failed' };
const api = <T,>(path: string, options?: Parameters<typeof requestJson>[1]) => requestJson<T>(`/api/literature${path}`, options);

function ItemDetails({ item, batch, busy, onSave, onRetry, onRemove, onPreview, onOpen }: {
  item: IntakeItem; batch: IntakeBatch; busy: boolean; onSave: (body: unknown) => Promise<void>; onRetry: () => void; onRemove: () => void; onPreview: () => void; onOpen: (id: string) => void;
}) {
  const [bibtex, setBibtex] = useState(item.manual_bibtex || '');
  const [query, setQuery] = useState(item.query || '');
  const [scope, setScope] = useState(item.scope);
  const [editing, setEditing] = useState(false);
  const locked = busy || active(item) || item.status === 'imported';
  const changed = bibtex !== (item.manual_bibtex || '') || query !== (item.query || '') || scope !== item.scope;
  return <section className="literature-detail" aria-label={`Details for ${item.name}`}>
    <header><h3>{item.name}</h3><span>{labels[item.status]}</span></header>
    <div className="literature-actions"><button onClick={onPreview}><BookOpen size={13} />Open PDF</button>
      {(item.existing_paper_id || item.paper_id) && <button onClick={() => onOpen((item.existing_paper_id || item.paper_id)!)}>Open library copy</button>}
      {!active(item) && item.status !== 'imported' && <button disabled={busy || changed} onClick={onRetry}><RefreshCw size={13} />Retry preparation</button>}
    </div>
    {item.error && <p className="literature-notice" role="status">{item.error}</p>}
    {item.warning && <p className="literature-muted">{item.warning}</p>}
    {item.draft && <><p>{item.draft.reason}</p><p className="literature-muted">Pages read: {item.draft.read_pages.join(', ') || 'None recorded'}{item.scope === 'book_overview' ? ' · Book overview' : ''}</p></>}
    {item.bibtex && <details><summary>Citation{item.key ? ` · ${item.key}` : ''}</summary><pre>{item.bibtex}</pre><p className="literature-muted">Source: {item.citation_source}</p></details>}
    {item.draft?.notes_markdown && <details open><summary>Reading notes</summary><MarkdownRenderer content={item.draft.notes_markdown} workspaceRoot={batch.destination} /></details>}
    {item.note_path && <p className="literature-path">Saved: {item.note_path}</p>}
    {!active(item) && item.status !== 'imported' && <>
      <button className="literature-text-button" aria-expanded={editing} onClick={() => setEditing(!editing)}>Correct citation or reading scope</button>
      {editing && <form onSubmit={event => { event.preventDefault(); void onSave({ bibtex, query, scope }); }}>
        <label>Title or DOI to search<input value={query} onChange={e => setQuery(e.target.value)} maxLength={500} /></label>
        <label>Reading scope<select value={scope} onChange={e => setScope(e.target.value as typeof scope)}><option value="paper">Paper skim</option><option value="book_overview">Book overview</option></select></label>
        <label>BibTeX for this PDF<textarea value={bibtex} onChange={e => setBibtex(e.target.value)} placeholder="Paste a complete citation, or select a candidate below" rows={7} maxLength={50000} /></label>
        {item.candidates.length > 0 && <details><summary>{item.candidates.length} citation candidates</summary><ul className="literature-candidates">{item.candidates.map((c, i) => <li key={i}><strong>{c.title}</strong><small>{c.authors} · {c.year}</small><button type="button" onClick={() => setBibtex(c.bibtex)}>Use this citation</button></li>)}</ul></details>}
        <button disabled={locked || !changed}>Save corrections</button><p className="literature-muted">Saved corrections need preparation again before import.</p>
      </form>}
    </>}
    {!active(item) && <button className="literature-text-button" disabled={busy} onClick={onRemove}>Remove from this batch</button>}
  </section>;
}

export default function LiteraturePanel({ execution, leadLabel, onOpen, onDocument, onBrowse }: {
  execution: Execution; leadLabel: string; onOpen: (paper: Paper) => Promise<void>; onDocument: (file: DocumentFile) => void; onBrowse: () => void;
}) {
  const library = usePaperLibrary();
  const [view, setView] = useState<'library' | 'import'>('import');
  const [search, setSearch] = useState('');
  const [summaries, setSummaries] = useState<BatchSummary[]>([]);
  const [batch, setBatch] = useState<IntakeBatch | null>(null);
  const [batchId, setBatchId] = useState('');
  const [selected, setSelected] = useState('');
  const [destination, setDestination] = useState('');
  const [newRoot, setNewRoot] = useState('');
  const [bibtex, setBibtex] = useState('');
  const [scope, setScope] = useState<'paper' | 'book_overview'>('paper');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(true);
  const [checked, setChecked] = useState<string[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const idRef = useRef(batchId); idRef.current = batchId;
  const busyRef = useRef(false);
  const revision = useRef(0);
  const sources = library.settings.sources.filter(s => s.kind === 'hugo');
  useEffect(() => { if (!destination && sources.length) setDestination(sources[0].path); }, [destination, sources]);
  useEffect(() => {
    let disposed = false;
    api<BatchSummary[]>('/batches').then(rows => { if (!disposed) { setSummaries(rows); if (!idRef.current && rows.length) setBatchId(rows[0].id); } })
      .catch(e => { if (!disposed) setError(String(e)); }).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, []);
  useEffect(() => {
    if (!batchId) { setBatch(null); return; }
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const generation = revision.current;
        const value = await api<IntakeBatch>(`/batches/${batchId}`);
        if (!disposed && !busyRef.current && generation === revision.current) setBatch(value);
      } catch (e) { if (!disposed) setError(String(e)); }
      if (!disposed) timer = setTimeout(() => void poll(), 2000);
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [batchId]);
  async function action(label: string, work: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; revision.current++; setBusy(label); setError('');
    try { await work(); setSummaries(await api<BatchSummary[]>('/batches')); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { busyRef.current = false; setBusy(''); }
  }
  async function upload(files: File[]) {
    if (!files.length) return;
    await action('Adding PDFs…', async () => {
      if (batchId && batch?.id !== batchId) throw new Error('Wait for the selected batch to finish opening.');
      if (!batch && !destination) throw new Error('Choose a knowledge-base destination first.');
      let current = batch;
      if (!current) current = await api<IntakeBatch>('/batches', jsonBody({ destination, bibtex }, 'POST'));
      setBatchId(current.id); idRef.current = current.id; setBatch(current);
      const failures: string[] = [];
      for (const file of files) {
        setBusy(`Adding ${file.name}…`);
        try {
          if (!file.name.toLowerCase().endsWith('.pdf')) throw new Error('Choose PDF files.');
          current = await api<IntakeBatch>(`/batches/${current.id}/pdf?name=${encodeURIComponent(file.name)}&scope=${scope}`, { method: 'POST', body: file, headers: { 'Content-Type': 'application/pdf' }, timeoutMs: 120000 });
          setBatch(current);
        } catch (e) { failures.push(`${file.name}: ${e instanceof Error ? e.message : String(e)}`); }
      }
      if (failures.length) setError(failures.join('\n'));
    });
  }
  async function prepare(ids: string[]) {
    if (!batch) return;
    await action('Starting preparation…', async () => {
      setBatch(await api<IntakeBatch>(`/batches/${batch.id}/prepare`, jsonBody({ ids, execution }, 'POST')));
    });
  }
  const visibleBatch = batch?.id === batchId ? batch : null;
  const item = visibleBatch?.items.find(i => i.id === selected);
  const ready = visibleBatch?.items.filter(i => i.status === 'ready') ?? [];
  const waiting = visibleBatch?.items.filter(i => ['staged', 'failed', 'interrupted'].includes(i.status)) ?? [];
  const working = Boolean(visibleBatch?.items.some(active));
  const selectedReady = ready.filter(i => checked.includes(i.id));
  const importIds = (selectedReady.length ? selectedReady : ready).map(i => i.id);
  const papers = findPapers(library.papers, library.sessions, { ...defaultFilters, query: search });
  const openById = (id: string) => { const paper = library.papers.find(p => p.id === id); if (paper) void action('Opening…', () => onOpen(paper)); else setError('Refresh the library to open this paper.'); };
  return <section className="literature-panel" aria-label="Literature" data-literature-drop
    onDragEnter={e => { if (Array.from(e.dataTransfer.types).includes('Files')) { e.preventDefault(); e.stopPropagation(); setDragging(true); } }}
    onDragOver={e => { e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = 'copy'; }}
    onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }}
    onDrop={e => { e.preventDefault(); e.stopPropagation(); setDragging(false); setView('import'); void upload(Array.from(e.dataTransfer.files)); }}>
    <header className="literature-header"><div role="tablist" aria-label="Literature views">{(['library', 'import'] as const).map(tab => <button key={tab} role="tab" aria-selected={view === tab} onClick={() => setView(tab)}>{tab === 'library' ? 'Library' : 'Import'}</button>)}</div><button title="Refresh library" aria-label="Refresh literature library" onClick={() => void refreshLibrary()}><RefreshCw size={14} /></button></header>
    <div className="literature-scroll">
      {error && <p role="alert" className="literature-notice">{error}</p>}
      {busy && <p role="status" className="literature-muted">{busy}</p>}
      {view === 'library' ? <>
        <label className="literature-search"><Search size={14} /><input aria-label="Search literature" placeholder="Title, author, idea…" value={search} onChange={e => setSearch(e.target.value)} /></label>
        <div className="literature-actions"><small>{papers.length} {papers.length === 1 ? 'document' : 'documents'}</small><button onClick={onBrowse}>Full library</button></div>
        {library.loading ? <p className="literature-muted">Loading library…</p> : library.error ? <p role="alert">{library.error}</p> : !papers.length ? <p className="literature-muted">{search ? 'No matching documents.' : 'Import PDFs to build your reading library.'}</p> : <ul className="literature-library">{papers.map(paper => <li key={paper.id}><button disabled={!paper.available || Boolean(busy)} onClick={() => void action('Opening…', () => onOpen(paper))}><strong>{paper.title}</strong><small>{paper.authors} {paper.year && `(${paper.year})`}</small>{!paper.available && <small>PDF unavailable</small>}</button></li>)}</ul>}
      </> : <>
        <div className="literature-actions"><h2>Import literature</h2><button disabled={Boolean(busy) || loading} onClick={() => { setBatchId(''); setBatch(null); setSelected(''); setChecked([]); }}><Plus size={14} />New batch</button></div>
        {loading ? <p className="literature-muted">Opening imports…</p> : summaries.length > 0 && <label>Batch<select aria-label="Import batch" disabled={Boolean(busy)} value={batchId} onChange={e => { setBatchId(e.target.value); setSelected(''); setChecked([]); }}><option value="">New batch</option>{summaries.map(row => <option key={row.id} value={row.id}>{new Date(row.created).toLocaleString()} · {row.count} {row.count === 1 ? 'PDF' : 'PDFs'}{row.active ? ' · Preparing' : ''}</option>)}</select></label>}
        {batchId && !visibleBatch ? <p className="literature-muted">Opening batch…</p> : <>
          {visibleBatch ? <p className="literature-path">Destination: {visibleBatch.destination}</p> : <>
            <label>Knowledge base<select value={destination} onChange={e => setDestination(e.target.value)}><option value="">Choose a destination</option>{sources.map(source => <option key={source.path} value={source.path}>{source.name} · {source.path}</option>)}</select></label>
            <details><summary>Connect a knowledge-base folder</summary><form onSubmit={e => { e.preventDefault(); void action('Connecting…', async () => {
              await paperRequest('/settings', jsonBody({ sources: [...library.settings.sources.filter(s => s.path !== newRoot.trim()), { kind: 'hugo', path: newRoot.trim(), name: '' }] })); await refreshLibrary(); setDestination(newRoot.trim()); setNewRoot('');
            }); }}><label>Folder on this DAN host<input value={newRoot} onChange={e => setNewRoot(e.target.value)} placeholder="/path/to/my-knowledge-base" /></label><button disabled={Boolean(busy) || !newRoot.trim()}>Connect</button></form></details>
            <details><summary>Supply BibTeX (optional)</summary><label>BibTeX entries<textarea rows={6} value={bibtex} onChange={e => setBibtex(e.target.value)} placeholder="Paste entries, or choose a .bib file" maxLength={500000} /></label><input type="file" accept=".bib" aria-label="Load BibTeX file" onChange={e => { const file = e.target.files?.[0]; if (file) void action('Reading BibTeX…', async () => { if (file.size > 500000) throw new Error('BibTeX file must be under 500 KB'); setBibtex(await file.text()); }); e.target.value = ''; }} /></details>
          </>}
          <label>New PDFs are<select value={scope} onChange={e => setScope(e.target.value as typeof scope)}><option value="paper">Papers, generate a research skim</option><option value="book_overview">Books, generate an overview</option></select></label>
          <button className={`literature-drop ${dragging ? 'is-dragging' : ''}`} disabled={Boolean(busy) || loading || (!visibleBatch && !destination)} onClick={() => input.current?.click()}><FileUp size={23} /><strong>{dragging ? 'Drop PDFs to add them' : 'Drop PDFs here'}</strong><span>or choose files · up to 100 MB each</span></button>
          <input ref={input} type="file" multiple accept="application/pdf,.pdf" hidden onChange={e => { void upload(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
          <p className="literature-muted">Original files are kept. New preparation uses {leadLabel}. You can leave this panel while it runs.</p>
          {visibleBatch && <>
            <div className="literature-actions">{working && <button disabled={Boolean(busy)} onClick={() => void action('Stopping…', async () => { setBatch(await api<IntakeBatch>(`/batches/${visibleBatch.id}/stop`, { method: 'POST' })); })}>Stop preparation</button>}<button disabled={Boolean(busy) || working || !waiting.length} onClick={() => void prepare(waiting.map(i => i.id))}>Prepare {waiting.length || ''} PDFs</button><button className="literature-primary" disabled={Boolean(busy) || !importIds.length} onClick={() => void action('Importing…', async () => {
              setBatch(await api<IntakeBatch>(`/batches/${visibleBatch.id}/apply`, { ...jsonBody({ ids: importIds }, 'POST'), timeoutMs: 120000 })); setChecked([]); await refreshLibrary();
            })}>{selectedReady.length ? 'Import selected' : 'Import ready items'}{importIds.length ? ` (${importIds.length})` : ''}</button></div>
            <ul className="literature-queue" aria-label="Import documents">{visibleBatch.items.map(row => <li key={row.id} className={selected === row.id ? 'is-selected' : ''}>
              {row.status === 'ready' && <input type="checkbox" aria-label={`Select ${row.name} for import`} checked={checked.includes(row.id)} onChange={e => setChecked(current => e.target.checked ? [...current, row.id] : current.filter(id => id !== row.id))} />}
              <button aria-expanded={selected === row.id} onClick={() => setSelected(selected === row.id ? '' : row.id)}><strong>{row.key || row.name}</strong><span>{labels[row.status] || row.status}</span></button>
            </li>)}</ul>
            {item && <ItemDetails key={`${visibleBatch.id}:${item.id}`} item={item} batch={visibleBatch} busy={Boolean(busy) || working} onOpen={openById}
              onPreview={() => onDocument(pathFileTarget(item.staged_path, item.name, { url: `/api/literature/batches/${visibleBatch.id}/items/${item.id}/pdf` }))}
              onRetry={() => void prepare([item.id])}
              onSave={body => action('Saving corrections…', async () => { setBatch(await api<IntakeBatch>(`/batches/${visibleBatch.id}/items/${item.id}`, jsonBody(body))); })}
              onRemove={() => void action('Removing…', async () => { setBatch(await api<IntakeBatch>(`/batches/${visibleBatch.id}/items/${item.id}`, { method: 'DELETE' })); setSelected(''); })} />}
          </>}
        </>}
      </>}
    </div>
  </section>;
}
