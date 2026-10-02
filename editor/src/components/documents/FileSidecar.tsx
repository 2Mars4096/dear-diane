import { useEffect, useState } from 'react';
import { resolveFileRequest, type FileOpenRequest } from '../../lib/openFile';
import { useDocumentTabs } from './useDocumentTabs';
import DocumentView from './DocumentView';
import { ReaderView } from '../reader/ReaderView';
import { ReaderNotes } from '../reader/ReaderNotes';
import './documents.css';

export default function FileSidecar({ request, active, onAsk }: { request: FileOpenRequest | null; active: boolean; onAsk: (text: string) => void }) {
  const { documents, mainTabs, activeMainTab, setActiveMainTab, openDocument, closeTab, documentDrafts, setDirtyDocuments } = useDocumentTabs(false);
  const [error, setError] = useState('');
  const [opening, setOpening] = useState(false);
  const [notes, setNotes] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!request) return;
    let cancelled = false; setOpening(true); setError(''); setNotes(false);
    void resolveFileRequest(request).then(file => { if (!cancelled) openDocument(file); }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not open this file.'); }).finally(() => { if (!cancelled) setOpening(false); });
    return () => { cancelled = true; };
  }, [request, attempt, openDocument]);
  const file = documents[activeMainTab];
  const menu = async () => {
    if (!file || file.source !== 'local') return;
    try {
      const result = await window.electronAPI?.shell.fileLink?.({ href: file.path, root: file.root || '', menu: true });
      if (result && !result.ok) setError(result.error || 'Could not open file actions.');
    } catch (e) { setError(String(e)); }
  };
  return <section className="dan-file-sidecar" aria-label="File preview">
    <div className="dan-document-toolbar" onContextMenu={event => { if (file?.source === 'local' && window.electronAPI?.shell.fileLink) { event.preventDefault(); void menu(); } }}>
      <select aria-label="Open file" value={file ? activeMainTab : ''} onChange={event => { setActiveMainTab(event.target.value); setError(''); setNotes(false); }}>
        {!file && <option value="">Select a file</option>}
        {mainTabs.filter(tab => documents[tab.id]).map(tab => <option key={tab.id} value={tab.id}>{tab.label}</option>)}
      </select>
      {file && <>
        {/\.pdf$/i.test(file.name) && <button onClick={() => setNotes(!notes)} aria-pressed={notes}>Notes</button>}
        {file.source === 'local' && window.electronAPI?.shell.fileLink && <button aria-label="File actions" onClick={() => void menu()}>…</button>}
        <button aria-label={`Close ${file.name}`} onClick={() => closeTab(activeMainTab)}>×</button>
      </>}
    </div>
    {opening && <p role="status" className="dan-document-message">Opening file…</p>}
    {error && <div role="alert" className="dan-document-message"><strong>Could not open file</strong><p>{request?.href || request?.file?.path}</p><p>{error}</p><button onClick={() => setAttempt(value => value + 1)}>Retry</button></div>}
    <div className="dan-file-sidecar-content" hidden={opening || !!error}>
      {Object.entries(documents).map(([id, entry]) => <div className="dan-side-document" key={id} hidden={id !== activeMainTab}>
        {/\.pdf$/i.test(entry.name) ? <><ReaderView file={entry} onNotes={() => setNotes(true)} onAsk={ask => onAsk(`File: ${entry.path}\nPage ${ask.pageNumber}\n\n> ${ask.quote}\n\nContext: ${ask.pageText}`)} />{notes && id === activeMainTab && <ReaderNotes file={entry} onAsk={onAsk} />}</> :
          <DocumentView file={entry} active={active && id === activeMainTab} drafts={documentDrafts.current} onDirty={(path, dirty) => setDirtyDocuments(current => ({ ...current, [path]: dirty }))} annotate onAsk={onAsk} />}
      </div>)}
      {!file && !opening && !error && <p className="dan-document-message">Open a file from the conversation or Files panel.</p>}
    </div>
  </section>;
}
