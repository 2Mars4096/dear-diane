import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ArrowUp, Paperclip, Square } from 'lucide-react';
import { personalApi, type SourceAttachment } from '../../lib/personalApi';
import { requestJson } from '../../lib/http';
import { isElectron } from '../../lib/electronBridge';
import './personal.css';
import VoiceControl from './VoiceControl';
const Records = lazy(() => import('./PersonalApp'));
const DRAFT = 'dan.personal.conversation.draft.v1';
type Turn = {id: string; created_at?: string; text: string; reply?: string; state: string; record_id?: string; calendar_url?: string; sources?: {id: string; name: string}[]};
type Draft = {text: string; operation: string; sources: SourceAttachment[]};
const blank = (): Draft => ({text: '', operation: crypto.randomUUID(), sources: []});
function load(): Draft {
  try { const value = JSON.parse(localStorage.getItem(DRAFT) || 'null'); if (value && typeof value.text === 'string' && typeof value.operation === 'string' && Array.isArray(value.sources)) return value; } catch { /* Online sending remains available. */ }
  return blank();
}
const active = (turn: Turn) => ['queued', 'running', 'applying'].includes(turn.state);
function WorkingElapsed({startedAt}: {startedAt?: string}) {
  const [fallback] = useState(Date.now);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const parsed = Date.parse(startedAt || '');
  const seconds = Math.max(0, Math.floor((now - (Number.isFinite(parsed) ? parsed : fallback)) / 1000));
  const pad = (value: number) => String(value).padStart(2, '0');
  return <span>Working… <time className="personal-elapsed" aria-live="off" dateTime={`PT${seconds}S`}>{pad(Math.floor(seconds / 3600))}h{pad(Math.floor(seconds / 60) % 60)}m{pad(seconds % 60)}s</time></span>;
}

export default function PersonalConversation() {
  const [draft, setDraft] = useState(load);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [unread, setUnread] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [model, setModel] = useState('');
  const [error, setError] = useState('');
  const [warning, setWarning] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState('');
  const [activity, setActivity] = useState(false);
  const [visited, setVisited] = useState(false);
  const [voiceActive, setVoiceActive] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const uploadOperations = useRef(new Map<string, string>());
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const lastSnapshot = useRef(0);
  const refresh = async () => {
    const request = ++lastSnapshot.current;
    const result = await requestJson<{turns: Turn[]; model: string; unread?: number}>('/api/personal/conversation');
    if (mounted.current && request === lastSnapshot.current) { setTurns(result.turns); setModel(result.model); setUnread(result.unread || 0); setLoaded(true); }
  };
  useEffect(() => {
    mounted.current = true;
    const update = () => void refresh().catch(() => { if (mounted.current) setError('Unable to connect. Your unsent message is kept here.'); });
    update(); const timer = setInterval(update, 2000); window.addEventListener('online', update);
    return () => { mounted.current = false; clearInterval(timer); window.removeEventListener('online', update); controller.current?.abort(); };
  }, []);
  useEffect(() => { try { localStorage.setItem(DRAFT, JSON.stringify(draft)); setWarning(''); } catch { setWarning('This browser cannot save your unsent message.'); } }, [draft]);
  const last = turns.at(-1);
  useEffect(() => { if (!activity) bottom.current?.scrollIntoView({block: 'nearest'}); }, [last?.id, last?.reply, activity]);
  const working = turns.find(active);
  const send = async () => {
    if (voiceActive || busy || working || !loaded || !model || (!draft.text.trim() && !draft.sources.length)) return;
    setBusy(true); setError('');
    try {
      await requestJson('/api/personal/conversation', {method: 'POST', body: JSON.stringify({operation_id: draft.operation, text: draft.text.trim() || 'Please help me with this file.', timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', source_ids: draft.sources.map(source => source.id)})});
      setDraft(blank()); await refresh(); input.current?.focus();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  const attach = async (files: File[]) => {
    if (voiceActive || busy || working) return;
    if (draft.sources.length + files.length > 5 || files.some(file => file.size > 10 * 1024 * 1024)) { setError('Attach up to five files, 10 MB each.'); return; }
    setBusy(true); setError(''); const abort = new AbortController(); controller.current = abort;
    try {
      for (const file of files) {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (!uploadOperations.current.has(key)) uploadOperations.current.set(key, crypto.randomUUID());
        const operation = uploadOperations.current.get(key)!;
        setUploading(`Reading ${file.name}…`);
        let text = '';
        if (/\.(png|jpe?g)$/i.test(file.name)) { const {recognizePersonalImage} = await import('../../lib/personalOcr'); text = await recognizePersonalImage(file, abort.signal); }
        if (abort.signal.aborted) throw new Error('Attachment stopped.');
        let source = await personalApi.upload(file, operation);
        if (source.needs_transcription) source = await personalApi.transcribe(source.id, operation+'-ocr', text);
        setDraft(previous => ({...previous, operation: crypto.randomUUID(), sources: [...previous.sources.filter(item => item.id !== source.id), source]}));
      }
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); setUploading(''); controller.current = null; }
  };
  const stop = async () => {
    if (!working || busy) return;
    setBusy(true); setError('');
    try { await requestJson(`/api/personal/conversation/${encodeURIComponent(working.id)}/stop`, {method:'POST'}); await refresh(); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  const openActivity = () => { setVisited(true); setActivity(true); };
  return <main className={`personal-app personal-conversation${isElectron() ? ' personal-native' : ''}`}>
    <header className="personal-header app-drag-region"><a className="app-no-drag" href="#workspace" title="Open workspace">Diane</a><button className="app-no-drag personal-quiet" onClick={() => activity ? setActivity(false) : openActivity()}>{activity ? 'Back to chat' : `Activity${unread ? ` (${unread})` : ''}`}</button></header>
    <div className="personal-dialogue" hidden={activity}>
      <div className="personal-messages" role="log" aria-label="Conversation with Diane" aria-live="polite">
        {loaded && turns.length === 0 && <div className="personal-welcome"><h1>What can I help you with?</h1></div>}
        {!loaded && <p role="status">Connecting…</p>}
        {turns.map(turn => <div key={turn.id} className="personal-turn">
          <article className="personal-user-message" aria-label="You"><p>{turn.text}</p>{turn.sources?.map(source => <small key={source.id}>{source.name}</small>)}</article>
          <article className="personal-assistant-message" aria-label="Diane"><p>{turn.reply || (active(turn) ? <WorkingElapsed startedAt={turn.created_at} /> : 'This reply was interrupted.')}</p>
            {turn.calendar_url && <a href={turn.calendar_url}>Download calendar file</a>}
          </article>
        </div>)}
        <div ref={bottom} />
      </div>
      <form className={`personal-chat-composer${voiceActive ? ' personal-voice-active' : ''}`} onSubmit={event => { event.preventDefault(); void send(); }}>
        {loaded && !model && <p className="personal-footnote">Chat needs an AI connection on this Mac. Your reminders are in Activity.</p>}
        {error && <p role="alert">{error}</p>}{warning && <p role="status">{warning}</p>}
        {uploading && <p role="status">{uploading} <button type="button" onClick={() => controller.current?.abort()}>Stop import</button></p>}
        {draft.sources.length > 0 && <ul className="personal-chat-attachments">{draft.sources.map(source => <li key={source.id}>{source.name}<button type="button" aria-label={`Remove ${source.name}`} disabled={busy} onClick={() => setDraft(previous => ({...previous, operation: crypto.randomUUID(), sources: previous.sources.filter(item => item.id !== source.id)}))}>×</button></li>)}</ul>}
        <textarea ref={input} aria-label="Message Diane" placeholder="Tell Diane what you need…" rows={2} maxLength={16000} value={draft.text} disabled={busy || voiceActive} onChange={event => setDraft(previous => ({...previous, text:event.target.value, operation:crypto.randomUUID()}))} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
        <div className="personal-chat-actions"><label className="personal-attach" title="Attach a file"><Paperclip size={18} /><span className="personal-sr-only">Attach files</span><input type="file" aria-label="Attach files" accept=".pdf,.eml,.txt,.png,.jpg,.jpeg" multiple disabled={busy || voiceActive || Boolean(working)} onChange={event => { const files=Array.from(event.target.files || []); event.target.value=''; void attach(files); }} /></label>
          {!activity && <VoiceControl disabled={busy || Boolean(working) || !loaded || !model || Boolean(draft.text.trim() || draft.sources.length)} refresh={() => void refresh().catch(() => {})} onError={setError} onActive={setVoiceActive} />}
          {working ? <button type="button" aria-label="Stop Diane" disabled={busy || working.state === 'applying'} onClick={() => void stop()}><Square size={16} /></button> : <button type="submit" className="personal-primary" aria-label="Send message" disabled={busy || voiceActive || !model || !loaded || (!draft.text.trim() && !draft.sources.length)}><ArrowUp size={18} /></button>}
        </div>
      </form>
    </div>
    {visited && <div className="personal-activity" hidden={!activity}><Suspense fallback={<p>Opening activity…</p>}><Records /></Suspense></div>}
  </main>;
}
