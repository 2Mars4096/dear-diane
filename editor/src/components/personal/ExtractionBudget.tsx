import { useEffect, useRef, useState } from 'react';
import { personalApi, type SpendingSettings, type SpendingStatus } from '../../lib/personalApi';

export function ExtractionBudget() {
  const [budget, setBudget] = useState<SpendingStatus | null>(null);
  const [draft, setDraft] = useState<SpendingSettings | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const dirty = useRef(false);
  const pending = useRef<{key: string; operation: string} | null>(null);
  useEffect(() => {
    let alive = true;
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const result = await personalApi.budget();
        if (alive) {
          setBudget(previous => previous && previous.settings.revision > result.settings.revision ? previous : result);
          if (!dirty.current) { setDraft(previous => previous && previous.revision > result.settings.revision ? previous : result.settings); setError(''); }
        }
      } catch { if (alive && !dirty.current) setError('Could not load spending limits.'); }
      finally { loading = false; }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  const edit = (changes: Partial<SpendingSettings>) => {
    dirty.current = true; setSaved(false);
    setDraft(previous => previous ? {...previous, ...changes} : previous);
  };
  const save = async () => {
    if (!draft || busy) return;
    const body = {expected_revision: draft.revision, task_limit: draft.task_limit, daily_limit: draft.daily_limit, paused: draft.paused};
    const key = JSON.stringify(body);
    if (pending.current?.key !== key) pending.current = {key, operation: crypto.randomUUID()};
    setBusy(true); setError(''); setSaved(false);
    try {
      await personalApi.updateBudget({...body, operation_id: pending.current.operation});
      const next = await personalApi.budget();
      setBudget(next); setDraft(next.settings); dirty.current = false; setSaved(true); pending.current = null;
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  const reload = async () => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const next = await personalApi.budget();
      setBudget(next); setDraft(next.settings); dirty.current = false; setSaved(false); pending.current = null;
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  return <details className="personal-source-library"><summary>AI spending limits</summary>
    {error && <p role="alert">{error}</p>}
    {budget ? <>
      <p>{budget.paused ? 'AI paused' : budget.available ? '' : 'AI limit reached'}</p>
      <dl className="personal-summary"><dt>Confirmed today (UTC)</dt><dd>${budget.actual_usd ?? '0'} / ${budget.daily_limit} USD</dd>
        {Number(budget.held_usd || 0) > 0 && <><dt>In progress</dt><dd>${budget.held_usd} held</dd></>}
        {(Number(budget.unverified_usd || 0) > 0 || budget.used_reservations_usd === null) && <><dt>Unverified</dt><dd>{Number(budget.unverified_usd || 0) > 0 ? `$${budget.unverified_usd} held` : 'Earlier costs unknown'}</dd></>}
      </dl>
      {draft && <form onSubmit={event => { event.preventDefault(); void save(); }}><fieldset disabled={busy} className="personal-budget-fields">
        <div className="personal-date-fields"><label>Per-message limit (USD)<input type="number" required min="0" max={budget.host_task_limit} step="any" value={draft.task_limit} onChange={event => edit({task_limit: event.target.value})} /></label><label>Daily limit (USD)<input type="number" required min="0" max={budget.host_daily_limit} step="any" value={draft.daily_limit} onChange={event => edit({daily_limit: event.target.value})} /></label></div>
        <p>Host maximum: ${budget.host_task_limit} per message and ${budget.host_daily_limit} per day.</p>
        <label className="personal-check"><input type="checkbox" checked={draft.paused} onChange={event => edit({paused: event.target.checked})} /> Pause AI replies</label>
        <div className="personal-actions">{(dirty.current || error) && <button type="button" onClick={() => void reload()}>Reload saved limits</button>}<button className="personal-primary" disabled={!dirty.current} type="submit">{busy ? 'Saving limits…' : 'Save limits'}</button></div>
      </fieldset></form>}
      {saved && <p role="status">Spending limits saved.</p>}
      <details><summary>How limits work</summary><p>Confirmed spend comes from provider billing receipts. Temporary holds protect the limit while work is running; unused amounts are released once cost is known. Unverified requests keep a separate hold. Older requests without billing IDs cannot be reconstructed automatically. Limits reset at midnight UTC and exclude search and account fees.</p></details>
    </> : !error && <p role="status">Loading limits…</p>}
  </details>;
}
