import { useEffect, useState, type ReactNode } from 'react';
import { requestJson } from '../../lib/http';
import { API_DEFAULTS, EMPTY_PROFILE, OPENROUTER_URL } from './modelSelection';
import './onboarding.css';
const READY_KEY = 'dan.setup.ready.v1';
function previouslyReady() {
  try { return localStorage.getItem(READY_KEY) === 'true'; } catch { return false; }
}
type Setup = { ready: boolean; local: boolean };
function initializeLead() {
  try {
    const profiles = JSON.parse(localStorage.getItem('dan.leadProfiles.v1') || '{}');
    if (!profiles.dan) {
      profiles.dan = { ...EMPTY_PROFILE, provider:'openrouter', base_url:OPENROUTER_URL, model:API_DEFAULTS.openrouter };
      localStorage.setItem('dan.leadProfiles.v1', JSON.stringify(profiles));
    }
    if (!localStorage.getItem('dan.chunkWorkspace.agentSelection.v1')) localStorage.setItem('dan.chunkWorkspace.agentSelection.v1','native');
  } catch { /* Existing preferences remain untouched if storage is unavailable. */ }
}
export default function OpenRouterSetup({ children }: { children: ReactNode }) {
  const [workspaceReady, setWorkspaceReady] = useState(previouslyReady);
  const [configure, setConfigure] = useState(false);
  const [status, setStatus] = useState<Setup | null>(null);
  const [key, setKey] = useState('');
  const [titles, setTitles] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const accept = (next: Setup) => {
    if (next.ready) {
      initializeLead(); setWorkspaceReady(true); setConfigure(false);
      try { localStorage.setItem(READY_KEY, 'true'); } catch { /* Readiness can still persist for this mount. */ }
    }
    setStatus(next);
  };
  async function check() {
    setBusy(true); setError('');
    try { accept(await requestJson<Setup>('/api/setup')); }
    catch { setError('Could not reach Dear Diane. Check the connection and retry.'); }
    finally { setBusy(false); }
  }
  useEffect(() => { void check(); }, []);
  async function connect() {
    if (busy || !key.trim()) return;
    setBusy(true); setError('');
    try { const result = await requestJson<Setup>('/api/setup/openrouter', { method:'POST', body:JSON.stringify({api_key:key.trim()}), timeoutMs:20000 }); setKey('');
      if (titles) { try { localStorage.setItem('dan.sessionTitles.enabledAt.v1', String(Date.now())); } catch { /* Leave optional titles disabled. */ } }
      accept(result); }
    catch (e) { setError(e instanceof Error ? e.message.replace(/^\d+: /,'') : 'Could not connect. Try again.'); }
    finally { setBusy(false); }
  }
  if (workspaceReady && !configure) return <>
    {children}
    {(error || status?.ready === false) && <aside className="dan-setup-notice" role="alert">
      <span>{error || 'Reconnect OpenRouter to continue sending requests.'}</span>
      <button type="button" disabled={busy} onClick={() => error ? void check() : setConfigure(true)}>{error ? 'Retry' : 'Connect'}</button>
    </aside>}
  </>;
  return <main className="dan-onboarding"><section aria-labelledby="setup-title"><p className="dan-setup-brand">Dear Diane</p><h1 id="setup-title">{status ? 'Connect OpenRouter' : 'Dear Diane'}</h1>
    {!status ? <>{!error && <p role="status">Checking your setup…</p>}</> : status.local ? <>
      <p>Connect an OpenRouter API key to start working with Diane.</p>
      <form onSubmit={event => { event.preventDefault(); void connect(); }}>
        <label>OpenRouter API key<input autoFocus type="password" autoComplete="new-password" spellCheck={false} value={key} disabled={busy} onChange={event => setKey(event.target.value)} placeholder="sk-or-…" /></label>
        <p className="dan-setup-detail">Your key is stored on this computer. Requests to Diane are sent to OpenRouter. Usage is billed to your OpenRouter account; add credit before starting.</p>
        <label className="dan-setup-titles"><input type="checkbox" checked={titles} disabled={busy} onChange={event => setTitles(event.target.checked)} />Generate titles for new sessions with OpenRouter. This sends each new session’s first request.</label>
        <button type="submit" disabled={busy || !key.trim()}>{busy ? 'Connecting…' : 'Connect and continue'}</button>
      </form>
      <a onClick={event => { if (window.electronAPI) { event.preventDefault(); void window.electronAPI.shell.openExternal("https://openrouter.ai/settings/keys"); } }} href="https://openrouter.ai/settings/keys" target="_blank" rel="noreferrer">Create an OpenRouter key ↗</a>
    </> : <p>Open Dear Diane on the host computer to connect an OpenRouter key, then retry here.</p>}
    {error && <p role="alert">{error}</p>}
    {(!status || !status.local) && <button type="button" disabled={busy} onClick={() => void check()}>Retry</button>}
  </section></main>;
}
