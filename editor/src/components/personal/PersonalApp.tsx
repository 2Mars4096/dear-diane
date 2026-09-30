import { useEffect, useRef, useState } from 'react';
import { personalApi, type Commitment, type Detail, type Review, type SourceAttachment } from '../../lib/personalApi';
import './personal.css';
import { ExtractionBudget } from './ExtractionBudget';
import { SourceLibrary } from './SourceLibrary';
import { TaskLifecycle } from './TaskLifecycle';
import { ReminderControls, ReminderInbox } from './Reminders';
import { isElectron } from '../../lib/electronBridge';

const DRAFT = 'dan.personal.unsent.v1';
function loadDraft(): { text: string; operation: string; sources: SourceAttachment[] } {
  try {
    const value = JSON.parse(localStorage.getItem(DRAFT) || '{}');
    if (typeof value.text === 'string' && typeof value.operation === 'string') return { text: value.text, operation: value.operation, sources: Array.isArray(value.sources) ? value.sources.filter((source: SourceAttachment) => source && typeof source.id === 'string' && typeof source.name === 'string').slice(0, 5) : [] };
  } catch { /* A blocked/full browser store still permits online capture. */ }
  return { text: '', operation: crypto.randomUUID(), sources: [] };
}

export default function PersonalApp() {
  const [draft, setDraft] = useState(loadDraft);
  const [records, setRecords] = useState<Commitment[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [draftWarning, setDraftWarning] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [model, setModel] = useState('');
  const [reminders, setReminders] = useState(false);
  const [editing, setEditing] = useState(false);
  const selecting = useRef(0);
  const uploadOperations = useRef(new Map<string, string>());
  const imageReading = useRef<AbortController | null>(null);
  const [uploadStatus, setUploadStatus] = useState('');
  useEffect(() => () => imageReading.current?.abort(), []);
  const cursor = useRef(0);
  const refresh = async () => { const snapshot = await personalApi.list(); cursor.current = snapshot.cursor; setRecords(snapshot.commitments); setHasMore(snapshot.has_more); };
  useEffect(() => {
    let alive = true;
    void personalApi.capabilities().then(async value => {
      if (!alive) return;
      setEnabled(value.enabled);
      setModel(value.model || '');
      setReminders(value.reminders === 'inbox');
      if (value.enabled) {
        const snapshot = await personalApi.list();
        if (alive) { cursor.current = snapshot.cursor; setRecords(snapshot.commitments); setHasMore(snapshot.has_more); }
      }
    }).catch(err => { if (alive) setError(String(err.message)); });
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    try { localStorage.setItem(DRAFT, JSON.stringify(draft)); setDraftWarning(''); }
    catch { setDraftWarning('This browser cannot keep your unsent draft after closing. Copy it before leaving.'); }
  }, [draft]);
  const extractionPending = ['queued', 'running'].includes(detail?.commitment.extraction?.status || '');
  const reminderPending = detail?.commitment.reminder?.state === 'scheduled';
  const detailId = detail?.commitment.id;
  useEffect(() => {
    if (detailId && window.matchMedia?.('(max-width: 767px)').matches) {
      const heading = document.getElementById('review-heading');
      heading?.focus({preventScroll: true});
      document.querySelector('.personal-review')?.scrollIntoView({block: 'start'});
    }
  }, [detailId]);
  useEffect(() => {
    if (!detailId || editing || (!extractionPending && !reminderPending)) return;
    let alive = true;
    const poll = async () => {
      try {
        const next = await personalApi.detail(detailId);
        if (!alive) return;
        setDetail(previous => previous?.commitment.id === detailId && next.commitment.revision >= previous.commitment.revision ? next : previous);
        if (!['queued', 'running'].includes(next.commitment.extraction?.status || '') && next.commitment.reminder?.state !== 'scheduled') await refresh();
      } catch { /* Keep the last durable status; retry while this detail is open. */ }
    };
    const timer = setInterval(() => void poll(), 1500);
    return () => { alive = false; clearInterval(timer); };
  }, [detailId, extractionPending, reminderPending, editing]);

  useEffect(() => {
    if (!enabled || busy) return;
    let alive = true;
    let inFlight = false;
    const reconnect = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const changes = await personalApi.events(cursor.current);
        if (!alive || (!changes.reset && changes.events.length === 0)) return;
        const snapshot = await personalApi.list();
        if (!alive) return;
        setRecords(snapshot.commitments); setHasMore(snapshot.has_more);
        if (detailId && !editing) {
          const next = await personalApi.detail(detailId);
          if (alive) setDetail(previous => previous?.commitment.id === detailId && next.commitment.revision >= previous.commitment.revision ? next : previous);
        }
        if (alive) cursor.current = snapshot.cursor;
      } catch { /* Keep the cursor until detail refresh succeeds, so reconnect can retry. */ }
      finally { inFlight = false; }
    };
    const timer = setInterval(() => void reconnect(), 5000);
    window.addEventListener('online', reconnect);
    return () => { alive = false; clearInterval(timer); window.removeEventListener('online', reconnect); };
  }, [enabled, busy, detailId, editing]);

  const capture = async () => {
    if (busy || (!draft.text.trim() && !draft.sources.length)) return;
    setBusy(true); setError(''); ++selecting.current;
    try {
      const result = draft.sources.length ? await personalApi.capture(draft.operation, draft.text, draft.sources.map(source => source.id)) : await personalApi.capture(draft.operation, draft.text);
      setEditing(false); setDetail(result); setDraft({ text: '', operation: crypto.randomUUID(), sources: [] });
      await refresh();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  const attach = async (files: File[]) => {
    if (busy) return;
    if (files.length + draft.sources.length > 5) { setError('Use at most five files per capture.'); return; }
    if (files.some(file => file.size > 10 * 1024 * 1024)) { setError('Each file must be no larger than 10 MiB.'); return; }
    setBusy(true); setError('');
    const controller = new AbortController(); imageReading.current = controller;
    try {
      for (const file of files) {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (!uploadOperations.current.has(key)) uploadOperations.current.set(key, crypto.randomUUID());
        let recognized = '';
        if (/\.(png|jpe?g)$/i.test(file.name)) {
          setUploadStatus(`Reading ${file.name} on this device…`);
          const { recognizePersonalImage } = await import('../../lib/personalOcr');
          recognized = await recognizePersonalImage(file, controller.signal);
        }
        if (controller.signal.aborted) throw new Error('Import stopped.');
        setUploadStatus(`Saving ${file.name}…`);
        let source = await personalApi.upload(file, uploadOperations.current.get(key)!);
        if (source.needs_transcription) source = await personalApi.transcribe(source.id, uploadOperations.current.get(key)! + '-ocr', recognized);

        setDraft(previous => ({ ...previous, sources: previous.sources.some(item => item.id === source.id) ? previous.sources : [...previous.sources, source], operation: crypto.randomUUID() }));
      }
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); setUploadStatus(''); imageReading.current = null; }
  };
  const open = async (id: string) => {
    const selection = ++selecting.current;
    setError('');
    try { const value = await personalApi.detail(id); if (selection === selecting.current) { setEditing(false); setDetail(value); } }
    catch (err) { if (selection === selecting.current) setError((err as Error).message); }
  };
  const executing = (item: Commitment) => ['queued', 'running'].includes(item.extraction?.status || '');
  const waiting = (item: Commitment) => ['paused', 'waiting-external'].includes(item.attention || '') || ['scheduled', 'paused'].includes(item.reminder?.state || '');
  const pending = records.filter(item => item.lifecycle === 'draft' && !executing(item));
  const progress = records.filter(item => (item.lifecycle === 'draft' && executing(item)) || (item.lifecycle === 'active' && !waiting(item)));
  const waitingRecords = records.filter(item => item.lifecycle === 'active' && waiting(item));
  const completed = records.filter(item => item.lifecycle === 'fulfilled');
  const dismissed = records.filter(item => item.lifecycle === 'cancelled');
  return <main className={`personal-app${isElectron() ? ' personal-native' : ''}`}>
    <header className="personal-header app-drag-region"><a className="app-no-drag" href="#workspace">← Chat with Diane</a><span>Reminders &amp; commitments</span></header>
    <div className="personal-body">
      <section className="personal-intro"><h1>What do you need to remember?</h1><p>Keep track of appointments, deadlines, and things to follow up.</p></section>
      {error && <div className="personal-error" role="alert">{error} <button onClick={() => location.reload()}>Reload</button></div>}
      {enabled === null && !error && <p role="status">Connecting to Diane…</p>}
      {enabled === false && <p>Personal commitments are not enabled on this Diane host yet.</p>}
      {enabled && <>
        <section className="personal-capture" aria-labelledby="capture-heading"><h2 id="capture-heading" className="personal-sr-only">Add a reminder or commitment</h2>
          <label htmlFor="personal-source">Message or note</label>
          <textarea id="personal-source" rows={3} maxLength={65536} value={draft.text} disabled={busy} placeholder="Paste an invitation, or write something like ‘Dentist on 14 October at 3 pm’." onChange={event => setDraft({ ...draft, text: event.target.value, operation: crypto.randomUUID() })} />

          {records.length === 0 && <p className="personal-footnote">{model ? 'Add a message or file, review the details, then set a reminder.' : 'Add a message or file. On the next screen, enter its date and time, then set a reminder.'}</p>}
          <details className="personal-upload-help"><summary>Supported files and limits</summary><p className="personal-footnote">Text PDFs, saved emails (.eml), text files, or PNG/JPEG images. Up to 5 files, 10 MiB each; PDFs up to 20 pages. Image text is read locally in English and Chinese; check recognition against the original. Scanned PDFs need a transcription. Removing a file from a draft keeps its uploaded original.</p></details>
          {uploadStatus && <p role="status">{uploadStatus} <button onClick={() => imageReading.current?.abort()}>Stop import</button></p>}
          {draft.sources.length > 0 && <ul>{draft.sources.map(source => <li key={source.id}>{source.name} <button disabled={busy} onClick={() => setDraft(previous => ({ ...previous, operation: crypto.randomUUID(), sources: previous.sources.filter(item => item.id !== source.id) }))}>Remove from draft</button></li>)}</ul>}
          <div className="personal-actions personal-capture-actions"><label className="personal-attach">Attach files<input type="file" accept=".pdf,.eml,.txt,.png,.jpg,.jpeg" multiple disabled={busy} onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; void attach(files); }} /></label><span>{draft.text || draft.sources.length ? 'Draft saved on this device' : ''}</span>
            {(draft.text || draft.sources.length > 0) && <button disabled={busy} onClick={() => setDraft({ text: '', operation: crypto.randomUUID(), sources: [] })} className="personal-quiet">Discard draft</button>}
            <button className="personal-primary" disabled={busy || (!draft.text.trim() && !draft.sources.length)} onClick={() => void capture()}>Review details →</button>
          </div>{draftWarning && <p role="status">{draftWarning}</p>}
        </section>
        <div className={`personal-columns${records.length === 0 && !detail ? ' personal-columns-empty' : ''}`}><section className="personal-inbox" aria-label="Commitments" tabIndex={-1}>
          {reminders && <ReminderInbox onOpen={id => { if (!busy) void open(id); }} />}
          {hasMore && <p role="status">Showing your 500 most recent commitments. Older records remain on the host.</p>}
          {([["To review", pending], ["Upcoming", progress], ["Scheduled or waiting", waitingRecords], ["Completed", completed], ["Dismissed", dismissed]] as const).filter(([, items]) => items.length > 0).map(([label, items]) => <section key={label}><h2>{label} <span>{items.length}</span></h2>
            {items.map(item => <button className="personal-record" disabled={busy} key={item.id} aria-pressed={detail?.commitment.id === item.id} onClick={() => void open(item.id)}><strong>{item.title}</strong><span>{item.date ? `${item.date}${item.time ? ` · ${item.time.slice(0, 5)}` : ''}` : 'Date needs review'}{item.timezone ? ` · ${item.timezone}` : ''}</span>{item.attention === 'paused' ? <span className="personal-record-status">Paused</span> : item.wait_reason ? <span className="personal-record-status">Waiting: {item.wait_reason}</span> : item.reminder?.state === 'scheduled' ? <span className="personal-record-status">Reminder scheduled</span> : null}</button>)}
          </section>)}
        </section>
        {detail ? <ReviewCard onClose={() => { setDetail(null); setEditing(false); const inbox = document.querySelector<HTMLElement>('.personal-inbox'); inbox?.focus({preventScroll: true}); inbox?.scrollIntoView({block: 'start'}); }} key={`${detail.commitment.id}:${detail.commitment.revision}`} detail={detail} model={model} reminders={reminders} onEditing={() => setEditing(true)} onBusy={value => { setBusy(value); if (value) ++selecting.current; }} onSaved={async record => { setEditing(false); setDetail({ ...detail, commitment: record }); const saved = await personalApi.detail(record.id); setDetail(saved); await refresh(); }} onReload={() => open(detail.commitment.id)} /> : records.length > 0 ? <aside className="personal-placeholder"><p>Select an item to see its details</p></aside> : null}
        </div>
        <div className="personal-tools">
        {model && <ExtractionBudget />}
        <SourceLibrary onRemoved={source => {
          uploadOperations.current.clear();
          setDraft(previous => ({ ...previous, operation: crypto.randomUUID(), sources: previous.sources.filter(item => item.id !== source.id) }));
          setDetail(previous => previous ? { ...previous, capture: { ...previous.capture, attachments: previous.capture.attachments?.map(item => item.id === source.id ? source : item) } } : previous);
        }} />
        </div>
      </>}
    </div>
  </main>;
}

function ReviewCard({ detail, model, reminders, onClose, onEditing, onSaved, onReload, onBusy }: { detail: Detail; model: string; reminders: boolean; onClose: () => void; onEditing: () => void; onSaved: (value: Commitment) => Promise<void>; onReload: () => Promise<void>; onBusy: (value: boolean) => void }) {
  const record = detail.commitment;
  const [values, setValues] = useState({ title: record.title, date: record.date || '', time: record.time?.slice(0, 5) || '', timezone: record.timezone || '', all_day: record.all_day, location: record.location, offset: record.offset || '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<{ serialized: string; body: Review } | null>(null);
  const extractionOperation = useRef(crypto.randomUUID());
  const extraction = record.extraction;
  const needsNewChoice = ['update', 'cancellation', 'none', 'unknown'].includes(extraction?.draft?.intent || '');
  const [confirmAsNew, setConfirmAsNew] = useState(false);
  const [showEditor, setShowEditor] = useState(record.lifecycle === 'draft');
  const extracting = ['queued', 'running'].includes(extraction?.status || '');
  const dirty = values.title !== record.title || values.date !== (record.date || '') || values.time !== (record.time?.slice(0, 5) || '') || values.timezone !== (record.timezone || '') || values.all_day !== record.all_day || values.location !== record.location || values.offset !== (record.offset || '');
  const extract = async (stop: boolean) => {
    setBusy(true); onBusy(true); setError('');
    try { await onSaved(await personalApi.extract(record.id, extractionOperation.current + (stop ? '-stop' : ''), record.revision, stop)); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); onBusy(false); }
  };
  const update = <K extends keyof typeof values>(key: K, value: typeof values[K]) => { onEditing(); setValues(old => ({ ...old, [key]: value })); };
  const submit = async (decision: Review['decision']) => {
    if (busy) return;
    setBusy(true); onBusy(true); setError('');
    const payload = { ...values, date: values.date || null, time: values.all_day ? null : values.time || null, timezone: values.timezone || null, offset: values.all_day ? null : values.offset || null, confirm_as_new: confirmAsNew, decision, expected_revision: record.revision };
    const serialized = JSON.stringify(payload);
    if (!pending.current || pending.current.serialized !== serialized) pending.current = { serialized, body: { ...payload, operation_id: crypto.randomUUID() } };
    try { await onSaved(await personalApi.review(record.id, pending.current.body)); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); onBusy(false); }
  };
  const cancelled = record.lifecycle === 'cancelled';
  const closed = cancelled || record.lifecycle === 'fulfilled';
  return <section className="personal-review" aria-labelledby="review-heading"><button className="personal-back" onClick={onClose}>← All commitments</button><h2 id="review-heading" tabIndex={-1}>{cancelled ? 'Dismissed commitment' : record.lifecycle === 'fulfilled' ? 'Completed commitment' : record.lifecycle === 'active' ? 'Commitment details' : 'Review your commitment'}</h2>
    <details open={record.lifecycle === 'draft'}><summary>Original source</summary><blockquote>{detail.capture.text}</blockquote>{detail.capture.attachments?.map(source => <p key={source.id}>{source.original_deleted ? <span>{source.name} · original removed</span> : <><a href={personalApi.source(record.id, source.id)}>{source.name}</a>{source.media_type.startsWith('image/') && <img className="personal-source-image" src={personalApi.source(record.id, source.id) + '/preview'} alt={`Original source: ${source.name}`} />}</>}</p>)}</details>
    {model && record.lifecycle === 'draft' && <div className="personal-extraction"><button disabled={busy || (dirty && !extracting)} onClick={() => void extract(extracting)}>{extracting ? 'Stop extraction' : 'Extract details'}</button><p>{extracting ? 'Reading source…' : dirty ? 'Save your edits to extract again.' : `Uses ${model}`}</p></div>}
    {extraction?.error && <p role="status">{extraction.error}</p>}
    {extraction?.draft && <div className="personal-extraction-result">
      {extraction.draft.questions.length > 0 && <><h3>Needs clarification</h3><ul>{extraction.draft.questions.map((question, index) => <li key={index}>{question}</li>)}</ul></>}
      <details><summary>Evidence</summary><p>{extraction.draft.title} · {extraction.draft.date || 'Date unknown'} · {extraction.draft.time || 'Time unknown'} · {extraction.draft.timezone || 'Timezone unknown'}</p><ul>{extraction.draft.anchors.map((anchor, index) => <li key={index}><strong>{anchor.field}:</strong> {anchor.quote}</li>)}</ul></details>
    </div>}
    {detail.capture.duplicate_candidates.length > 0 && <p role="status">This source matches an earlier capture. Check your saved commitments before confirming another.</p>}
    {closed || !showEditor ? <dl className="personal-summary"><dt>Title</dt><dd>{record.title}</dd><dt>When</dt><dd>{record.date || 'No date'}{record.time ? ` · ${record.time.slice(0, 5)}` : ''}{record.all_day ? ' · All day' : ''}{record.timezone ? ` · ${record.timezone}` : ''}</dd>{record.location && <><dt>Place</dt><dd>{record.location}</dd></>}</dl> : <form onSubmit={event => { event.preventDefault(); void submit('confirm'); }}>
      <fieldset disabled={busy || closed || extracting || record.attention === 'paused'}>
        {needsNewChoice && <div role="note"><p>{extraction?.draft?.intent === 'cancellation' ? 'This source describes a cancellation.' : extraction?.draft?.intent === 'update' ? 'This source describes a change to an existing commitment.' : 'This source does not identify a clear new commitment.'} Updating an existing commitment from a source is not available yet.</p><label className="personal-check"><input type="checkbox" checked={confirmAsNew} onChange={event => { onEditing(); setConfirmAsNew(event.target.checked); }} /> Save this as a new, separate commitment</label></div>}
        <label>Title<input required maxLength={240} value={values.title} onChange={event => update('title', event.target.value)} /></label>
        <div className="personal-date-fields"><label>Date<input type="date" value={values.date} onChange={event => update('date', event.target.value)} /></label><label>Time<input type="time" disabled={values.all_day} value={values.time} onChange={event => update('time', event.target.value)} /></label></div>
        <label className="personal-check"><input type="checkbox" checked={values.all_day} onChange={event => update('all_day', event.target.checked)} /> All day</label>
        <label>Timezone<input placeholder="Asia/Hong_Kong" value={values.timezone} onChange={event => update('timezone', event.target.value)} autoCapitalize="none" spellCheck={false} /></label>
        <details><summary>Daylight saving adjustment</summary><label>UTC offset (only when needed)<input placeholder="-05:00" value={values.offset} onChange={event => update('offset', event.target.value)} /></label></details>
        <label>Place or meeting link<input maxLength={1000} value={values.location} onChange={event => update('location', event.target.value)} /></label>
        <div className="personal-actions">{record.lifecycle === 'active' && <button type="button" onClick={() => { setValues({title: record.title, date: record.date || '', time: record.time?.slice(0, 5) || '', timezone: record.timezone || '', all_day: record.all_day, location: record.location, offset: record.offset || ''}); setShowEditor(false); }}>Cancel edits</button>}{record.lifecycle === 'draft' && <button className="personal-quiet personal-destructive" type="button" onClick={() => void submit('dismiss')}>Dismiss</button>}{record.lifecycle === 'draft' && <button type="button" disabled={!dirty} onClick={() => void submit('save')}>Save draft</button>}<button className="personal-primary" type="submit" disabled={(needsNewChoice && !confirmAsNew) || (record.lifecycle === 'active' && !dirty)}>{busy ? 'Saving…' : record.lifecycle === 'active' ? 'Save changes' : 'Confirm details'}</button></div>
      </fieldset>
    </form>}
    {dirty && record.lifecycle === 'active' && ['scheduled', 'paused'].includes(record.reminder?.state || '') && <p role="status">Saving changes clears the current reminder.</p>}
    {error && <p role="alert">{error} <button disabled={busy} onClick={() => void onReload()}>Reload saved version</button></p>}
    {record.lifecycle === 'active' && !showEditor && <div className="personal-actions"><a className="personal-calendar" href={personalApi.calendar(record.id)}>Download .ics</a><button disabled={busy || record.attention === 'paused'} onClick={() => setShowEditor(true)}>Edit details</button></div>}
    {reminders && record.lifecycle === 'active' && record.attention !== 'paused' && !dirty && <ReminderControls disabled={busy} onEditing={onEditing} record={record} onSaved={onSaved} onBusy={value => { setBusy(value); onBusy(value); }} />}
    {!dirty && <TaskLifecycle disabled={busy} record={record} onEditing={onEditing} onSaved={onSaved} onBusy={value => { setBusy(value); onBusy(value); }} />}
    {detail.history && <details><summary>History</summary><ul>{detail.history.map(event => <li key={event.cursor}>{event.kind.replace(/_/g, ' ')} · {new Date(event.at).toLocaleString()}</li>)}</ul></details>}
  </section>;
}
