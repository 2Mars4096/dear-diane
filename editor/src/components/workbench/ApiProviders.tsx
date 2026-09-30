import { useEffect, useState } from "react";
import { requestJson } from "../../lib/http";

type Provider = { id: string; label: string; configured: boolean; saved: boolean; key_env: string };
type Report = { providers: Provider[] };
export function ApiProviders() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [selected, setSelected] = useState("openrouter");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  useEffect(() => {
    let disposed = false;
    requestJson<Report>("/api/model-providers").then(result => { if (!disposed) setProviders(result.providers); })
      .catch(() => { if (!disposed) setError("API keys are managed from the local Dear Diane app on this host."); });
    return () => { disposed = true; };
  }, []);
  const provider = providers.find(item => item.id === selected);
  async function save(value: string) {
    setBusy(true); setError(""); setStatus("");
    try {
      const result = await requestJson<Report>(`/api/model-providers/${selected}/key`, { method: "PUT", body: JSON.stringify({ api_key: value }) });
      setProviders(result.providers); setKey("");
      setStatus(value ? "API key saved. Reopen the model selector to refresh provider status." : "Saved key removed. A server environment key may still be configured.");
    } catch { setError("Could not save the API key. Check that you are using the local app and try again."); }
    finally { setBusy(false); }
  }
  return <section className="wb-api-providers" aria-label="API providers">
    <h3>API providers</h3>
    <p>Keys stay on this host. Choose Native or API in the agent’s model selector.</p>
    {providers.length > 0 && <form onSubmit={event => { event.preventDefault(); if (key.trim() && !busy) void save(key.trim()); }}>
      <label>Provider<select aria-label="API key provider" disabled={busy} value={selected} onChange={event => { setSelected(event.target.value); setKey(""); setError(""); setStatus(""); }}>{providers.map(item => <option key={item.id} value={item.id}>{item.label}{item.configured ? " · configured" : ""}</option>)}</select></label>
      <p>{provider?.configured ? "Key configured" : "No key configured"}{provider?.saved ? " in Settings." : provider?.configured ? " on the server." : "."}</p>
      <label>API key<input aria-label="API key" type="password" autoComplete="new-password" spellCheck={false} disabled={busy} value={key} placeholder={provider?.configured ? "Enter a replacement key" : "Enter API key"} onChange={event => setKey(event.target.value)} /></label>
      <div className="wb-update-actions"><button type="submit" disabled={busy || !key.trim()}>{busy ? "Saving…" : "Save key"}</button>{provider?.saved && <button type="button" disabled={busy} onClick={() => void save("")}>Remove saved key</button>}</div>
    </form>}
    {status && <p role="status">{status}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
