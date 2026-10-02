import { TextAnnotations } from './TextAnnotations';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import MarkdownRenderer from '../shared/MarkdownRenderer';
import { decodeDocument, documentKind, documentResponse, downloadDocument, type DocumentFile, type DocumentDraft } from './documents';
import './documents.css';

export default function DocumentView({ file, active, drafts, onDirty, annotate = false, onAsk, keyboard = true }: { keyboard?: boolean; annotate?: boolean; onAsk?: (text: string) => void; file: DocumentFile; active: boolean; drafts: Record<string, DocumentDraft>; onDirty: (path: string, dirty: boolean) => void }) {
  const kind = documentKind(file.name);
  const [text, setText] = useState(drafts[file.path]?.text ?? '');
  const [saved, setSaved] = useState<string | null>(drafts[file.path]?.saved ?? null);
  const [revision, setRevision] = useState(drafts[file.path]?.revision ?? '');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState(annotate);
  const [retry, setRetry] = useState(0);
  const [notice, setNotice] = useState('');
  const dirty = saved !== null && text !== saved;
  const callback = useRef(onDirty); callback.current = onDirty;
  useEffect(() => { callback.current(file.path, dirty); }, [file.path, dirty]);
  useEffect(() => {
    if (kind !== 'text' || drafts[file.path]) return;
    let cancelled = false; setError('');
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    void (async () => {
      try {
        if (file.local && file.local.size > 2_000_000) throw new Error('Text editing supports files up to 2 MB.');
        const value = file.local
          ? { content: decodeDocument(await file.local.arrayBuffer()), revision: '' }
          : await documentResponse(await fetch(`/api/workspace-files/document?${new URLSearchParams({path:file.path,root_path:file.root ?? ''})}`, { signal: controller.signal }));
        if (!cancelled) { setText(value.content); setSaved(value.content); setRevision(value.revision); }
      } catch (e) { if (!cancelled) setError(controller.signal.aborted ? 'Opening timed out. Try again.' : e instanceof Error ? e.message : 'Could not read this file.'); }
      finally { window.clearTimeout(timeout); }
    })();
    return () => { cancelled = true; controller.abort(); window.clearTimeout(timeout); };
  }, [file, kind, drafts, retry]);
  useLayoutEffect(() => { if (saved !== null) drafts[file.path] = {text, saved, revision}; }, [drafts, file.path, text, saved, revision]);
  const save = async () => {
    if (saved === null || saving) return;
    const snapshot = text;
    if (file.local) { downloadDocument(file.name, snapshot); setSaved(snapshot); setNotice('Downloaded a copy. The original file is unchanged.'); return; }
    setSaving(true); setError(''); setNotice('');
    try {
      const value = await documentResponse(await fetch('/api/workspace-files/document', { method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:file.path,root_path:file.root,revision,content:snapshot}) }));
      setSaved(snapshot); setRevision(value.revision); setNotice('Saved');
    } catch (e) { setError(e instanceof Error ? e.message : 'Save failed. Your edits are still here.'); }
    finally { setSaving(false); }
  };
  const saveRef = useRef(save); saveRef.current = save;
  useEffect(() => {
    if (!active || !keyboard || kind !== 'text') return;
    const key = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); void saveRef.current(); } };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [active, kind, keyboard]);
  return <section className="dan-document" hidden={!active} aria-label={file.name}>
    <div className="dan-document-toolbar">
      <span title={file.local ? 'Browser copy' : file.path}>{file.local ? 'Browser copy' : file.path}</span>
      {kind === 'text' && saved !== null && <>
        {(annotate || /\.(md|markdown|mdx)$/i.test(file.name)) && <button aria-pressed={preview} onClick={() => setPreview(!preview)}>{preview ? 'Edit' : 'Preview'}</button>}
        {!file.local && <button onClick={() => downloadDocument(file.name, text)}>Download copy</button>}
        <button onClick={() => void save()} disabled={saving || (!dirty && !file.local)}>{saving ? 'Saving…' : file.local ? 'Download edits' : 'Save'}</button>
      </>}
      {(kind !== 'text' || saved === null) && <a href={file.url} download={file.name}>Download</a>}
    </div>
    {error && <div className="dan-document-message" role="alert"><strong>{saved === null || kind !== 'text' ? "Could not open file" : "Could not save file"}</strong><p>{error}</p>{saved === null && <button onClick={() => { setError(''); setRetry(value => value + 1); }}>Retry</button>}</div>}
    {notice && <p className="dan-document-message" role="status">{notice}</p>}
    {kind === 'text' ? saved === null ? !error && <p className="dan-document-message">Opening file…</p> : annotate && preview ? <TextAnnotations file={file} text={text} onAsk={onAsk}>{/\.(md|markdown|mdx)$/i.test(file.name) ? <div className="dan-document-preview"><MarkdownRenderer content={text} workspaceRoot={file.root} /></div> : undefined}</TextAnnotations> : preview ? <div className="dan-document-preview"><MarkdownRenderer content={text} workspaceRoot={file.root} /></div> : <textarea aria-label={`Edit ${file.name}`} value={text} spellCheck={false} onChange={e => {setText(e.target.value);setNotice('');}} /> :
      <div className="dan-document-media" key={retry}>{kind === 'image' ? <img src={file.url} alt={file.name} onError={() => setError('This image could not be displayed.')} /> : kind === 'audio' ? <audio src={file.url} controls onError={() => setError('This audio could not be opened.')} /> : <video src={file.url} controls onError={() => setError('This video could not be opened.')} />}</div>}
  </section>;
}
