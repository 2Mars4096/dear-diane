import { useState } from 'react';
import { personalApi, type SourceAttachment } from '../../lib/personalApi';

export function SourceLibrary({onRemoved}: {onRemoved: (source: SourceAttachment) => void}) {
  const [sources, setSources] = useState<SourceAttachment[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const load = async () => {
    setLoading(true);
    try { setSources((await personalApi.sources()).sources); setError(''); }
    catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  };
  const remove = async (source: SourceAttachment) => {
    if (busy) return;
    setBusy(true);
    try {
      const deleted = await personalApi.deleteSource(source, `delete-${source.id}-${source.revision || 1}`);
      onRemoved(deleted); setConfirming(null); await load();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  return <details className="personal-source-library" onToggle={event => { if (event.currentTarget.open) void load(); }}><summary>Uploaded originals</summary>
    {error && <p role="alert">{error}</p>}
    {loading && <p role="status">Loading originals…</p>}
    {!loading && sources.length === 0 ? <p>No uploaded originals.</p> : <ul>{sources.map(source => <li key={source.id}>
      <div className="personal-source-row"><span>{source.name}{source.original_deleted ? ' · original removed' : ''}</span><button disabled={busy || source.original_deleted} onClick={() => setConfirming(source.id)}>Delete original</button></div>
      {confirming === source.id && <div role="group" aria-label={`Delete ${source.name}`}><p>Delete this original? Captured text and older database backups are retained. Unused source text will also be removed.</p><div className="personal-actions"><button disabled={busy} onClick={() => setConfirming(null)}>Keep original</button><button disabled={busy} onClick={() => void remove(source)}>{busy ? 'Deleting…' : 'Confirm deletion'}</button></div></div>}
    </li>)}</ul>}
  </details>;
}
