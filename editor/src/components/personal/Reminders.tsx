import { useEffect, useRef, useState } from 'react';
import { personalApi, type Commitment, type ReminderChange, type ReminderNotice } from '../../lib/personalApi';

export function ReminderInbox({ onOpen }: { onOpen: (id: string) => void }) {
  const [items, setItems] = useState<ReminderNotice[]>([]);
  const [error, setError] = useState('');
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const result = await personalApi.inbox();
        if (alive) { setItems(result.notifications); setMore(result.has_more); setError(''); }
      } catch { if (alive) setError('The reminder inbox is offline. Reconnecting…'); }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  const read = async (item: ReminderNotice) => {
    setBusy(true);
    try {
      const result = await personalApi.read(item, `read-${item.id}-${item.revision}`);
      setItems(previous => previous.map(value => value.id === item.id ? result : value));
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  if (!error && !more && !items.some(item => !item.read)) return null;
  return <section className="personal-reminder-inbox" aria-label="Reminder inbox"><h2>Reminders <span>{items.filter(item => !item.read).length}</span></h2>
    {error && <p role="status">{error}</p>}
    {items.filter(item => !item.read).map(item => <article key={item.id}><strong>{item.title}</strong><p>{new Date(item.due_at).toLocaleString()}{item.late ? ' · Added after a delay' : ''}</p><div className="personal-actions"><button onClick={() => onOpen(item.commitment_id)}>Open commitment</button><button disabled={busy} onClick={() => void read(item)}>Mark read</button></div></article>)}
    {!items.some(item => !item.read) && <p className="personal-empty">No unread reminders.</p>}
    {more && <p>Showing the latest 500 reminders.</p>}
  </section>;
}

export function ReminderControls({ record, onSaved, onBusy, onEditing, disabled = false }: { onEditing: () => void; disabled?: boolean; record: Commitment; onSaved: (record: Commitment) => Promise<void>; onBusy: (busy: boolean) => void }) {
  const reminder = record.reminder;
  const [rescheduling, setRescheduling] = useState(false);
  const [initial] = useState(() => {
    if (!reminder) return {day: record.date || '', clock: record.time?.slice(0, 5) || '', timezone: record.timezone || '', offset: record.offset || ''};
    const instant = new Date(reminder.due_at);
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone: reminder.timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).formatToParts(instant);
    const part = (name: string) => parts.find(value => value.type === name)!.value;
    return {day: `${part('year')}-${part('month')}-${part('day')}`, clock: `${part('hour')}:${part('minute')}`, timezone: reminder.timezone, offset: ''};
  });
  const [day, setDay] = useState(initial.day);
  const [clock, setClock] = useState(initial.clock);
  const [offset, setOffset] = useState(initial.offset);
  const [timezone, setTimezone] = useState(initial.timezone);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<{ key: string; operation: string } | null>(null);
  const change = async (action: ReminderChange['action']) => {
    if (busy || disabled) return;
    const body = { expected_revision: record.revision, action, ...(action === 'schedule' ? { date: day, time: clock, timezone, offset: offset || null } : {}) };
    const key = JSON.stringify(body);
    if (pending.current?.key !== key) pending.current = { key, operation: crypto.randomUUID() };
    setBusy(true); onBusy(true); setError('');
    try { await onSaved(await personalApi.reminder(record.id, { ...body, operation_id: pending.current.operation })); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); onBusy(false); }
  };
  return <section className="personal-reminder-controls"><h3>Inbox reminder</h3><p className="personal-footnote">In Diane while your Mac is running.</p>
    {reminder && <p role="status">{reminder.state} · {new Date(reminder.due_at).toLocaleString(undefined, { timeZone: reminder.timezone })} ({reminder.timezone})</p>}
    {!rescheduling && (reminder?.state === 'scheduled' || reminder?.state === 'paused') ? <div className="personal-actions"><button disabled={busy || disabled} onClick={() => { onEditing(); setRescheduling(true); }}>Change reminder</button><button disabled={busy || disabled} onClick={() => void change(reminder.state === 'paused' ? 'resume' : 'pause')}>{reminder.state === 'paused' ? 'Resume reminder' : 'Pause reminder'}</button><button className="personal-quiet" disabled={busy || disabled} onClick={() => void change('stop')}>Stop reminder</button></div> : <fieldset disabled={busy || disabled}>
      <div className="personal-date-fields"><label>Reminder date<input type="date" value={day} onChange={event => { onEditing(); setDay(event.target.value); }} /></label><label>Reminder time<input type="time" value={clock} onChange={event => { onEditing(); setClock(event.target.value); }} /></label></div>
      <label>Reminder timezone<input value={timezone} placeholder="Asia/Hong_Kong" autoCapitalize="none" spellCheck={false} onChange={event => { onEditing(); setTimezone(event.target.value); }} /></label><details><summary>Daylight saving adjustment</summary><label>UTC offset (if needed)<input value={offset} placeholder="-05:00" onChange={event => { onEditing(); setOffset(event.target.value); }} /></label></details>
      <div className="personal-actions"><button className="personal-primary" disabled={!day || !clock || !timezone.trim()} onClick={() => void change('schedule')}>{rescheduling ? 'Save reminder' : 'Schedule reminder'}</button></div>
    </fieldset>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
