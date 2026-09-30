import { useRef, useState } from 'react';
import { personalApi, type Commitment } from '../../lib/personalApi';

export function TaskLifecycle({record, onSaved, onBusy, onEditing, disabled = false}: {disabled?: boolean; record: Commitment; onSaved: (record: Commitment) => Promise<void>; onBusy: (busy: boolean) => void; onEditing: () => void}) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<{key: string; operation: string} | null>(null);
  const change = async (action: 'complete' | 'wait' | 'pause' | 'resume' | 'cancel') => {
    if (busy || disabled) return;
    const key = JSON.stringify({action, note, revision: record.revision});
    if (pending.current?.key !== key) pending.current = {key, operation: crypto.randomUUID()};
    setBusy(true); onBusy(true); setError('');
    try { await onSaved(await personalApi.decision(record.id, {operation_id: pending.current.operation, expected_revision: record.revision, action, note})); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); onBusy(false); }
  };
  if (record.outcome) return <section className="personal-export"><h3>Outcome</h3><p>{record.outcome.note || 'Cancelled by you.'}</p><p>Reported by you · {new Date(record.outcome.at).toLocaleString()}</p></section>;
  if (record.lifecycle !== 'active') return null;
  return <section className="personal-export"><h3>Update status</h3>
    {record.attention === 'paused' && <p>Commitment and reminders paused.</p>}
    {record.wait_reason && <p>Waiting: {record.wait_reason}</p>}
    <label>Outcome or waiting for<textarea rows={2} value={note} maxLength={2000} disabled={busy || disabled} onChange={event => { onEditing(); setNote(event.target.value); }} placeholder="Add a note to complete or wait" /></label>
    <div className="personal-actions">
      <button className="personal-quiet personal-destructive" disabled={busy || disabled} onClick={() => void change('cancel')}>Cancel commitment</button>
      {['paused', 'waiting-external'].includes(record.attention || '') ? <button disabled={busy || disabled} onClick={() => void change('resume')}>Resume commitment</button> : <><button disabled={busy || disabled} onClick={() => void change('pause')}>Pause commitment</button><button disabled={busy || disabled || !note.trim()} onClick={() => void change('wait')}>Wait for a response</button></>}
      <button className="personal-primary" disabled={busy || disabled || !note.trim()} onClick={() => void change('complete')}>Mark completed</button>
    </div>{error && <p role="alert">{error}</p>}
  </section>;
}
