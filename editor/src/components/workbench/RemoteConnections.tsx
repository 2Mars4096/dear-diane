import { useEffect, useState } from "react";
import { nativeShell } from "../../lib/electronBridge";

interface Connection {
  id: string; name: string; ssh_alias: string; relay_ssh_alias: string;
  address: string; port: number; relay_address: string; relay_port: number;
  workspace: string; url?: string; installed?: boolean;
  ssh_via_relay?: boolean;
  package_source?: "pypi" | "tsinghua";
  connection_error?: string;
  execution?: { boot_persistent: boolean }; relay?: { boot_persistent: boolean };
}
interface Job { id: string; status: string; message: string }
const blank: Connection = { id: "", name: "", ssh_alias: "", relay_ssh_alias: "ny", address: "", port: 8765, relay_address: "10.77.77.1", relay_port: 8765, workspace: "~/Downloads/local_projects" };
async function api(path: string, init?: RequestInit) {
  const response = await fetch(`/api/remote${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Check the connection fields and try again.");
  return body;
}

export function RemoteConnections() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [local, setLocal] = useState(true);
  const [machine, setMachine] = useState("");
  const [draft, setDraft] = useState<Connection | null>(null);
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [shareKey, setShareKey] = useState(false);
  const [accessKey, setAccessKey] = useState<{ id: string; key: string } | null>(null);
  async function refresh() {
    const result = await api("/connections");
    setConnections(result.connections); setLocal(result.local); setMachine(result.machine || "");
  }
  useEffect(() => { void refresh().catch(error => setError(String(error))); }, []);
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const timer = window.setInterval(() => {
      void api(`/jobs/${job.id}`).then(value => {
        setJob(value);
        if (value.status !== "running") void refresh().catch(error => setError(String(error)));
      }).catch(error => { setError(String(error)); setJob(null); });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [job]);
  async function action(work: () => Promise<void>) {
    setPending(true); setError(""); setMessage(""); setAccessKey(null);
    try { await work(); } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setPending(false); }
  }
  const busy = pending || job?.status === "running";
  if (!local) return <section className="wb-desktop-updates" aria-label="Remote connection"><h3>Connected to {machine}</h3><p>Work runs on this machine and continues when you close the browser. A server restart can interrupt active work. Set up other machines from your local DAN app.</p><form method="post" action="/remote/logout"><button>Disconnect this browser</button></form></section>;
  return <section className="wb-desktop-updates wb-remote-connections" aria-label="Remote connections">
    <h3>Remote connections</h3>
    <p>Connect through an existing SSH alias. Phone access uses your private VPN and relay, even while this computer is offline.</p>
    {connections.map(connection => <div className="wb-remote-row" key={connection.id}>
      <strong>{connection.name}</strong><span>{connection.ssh_alias} via {connection.relay_ssh_alias}</span>
      <p>{connection.installed ? (connection.execution?.boot_persistent && connection.relay?.boot_persistent ? "Installed · starts at boot" : "Installed · boot persistence needs attention") : "Saved · installation needed"}</p>
      {connection.connection_error && <p role="status">{connection.connection_error}</p>}
      <div className="wb-update-actions">
        <button disabled={busy} onClick={() => { setDraft(connection); setEditing(true); setAccessKey(null); }}>Edit</button>
        <button disabled={busy} onClick={() => void action(async () => {
          const result = await api(`/connections/${connection.id}/inspect`, { method: "POST" });
          const tools = Object.entries(result.tools).filter(([, path]) => path).map(([name]) => name).join(", ");
          setMessage(`${result.hostname} · ${result.platform} · ${result.linger}. Available: ${tools || "no supported tools found"}.`);
        })}>Check SSH</button>
        <button disabled={busy} onClick={() => void action(async () => {
          setJob(await api(`/connections/${connection.id}/install`, { method: "POST", body: JSON.stringify({ share_openrouter: shareKey }) }));
        })}>{connection.installed ? "Update installation" : "Install DAN"}</button>
        {connection.installed && <>
          <button disabled={busy} onClick={() => void action(async () => {
            const result = await api(`/connections/${connection.id}/access-key`, { method: "POST" });
            setAccessKey({ id: connection.id, key: result.access_key });
          })}>Show access key</button>
          <button onClick={() => void nativeShell.openExternal(connection.url!)}>Open {connection.name}</button>
        </>}
      </div>
      {connection.installed && <p><a href={connection.url} target="_blank" rel="noreferrer" onClick={event => { if (window.electronAPI) { event.preventDefault(); void nativeShell.openExternal(connection.url!); } }}>{connection.url}</a> · Use this address on your phone with the VPN connected.</p>}
      {accessKey?.id === connection.id && <label>Access key<input readOnly type="text" value={accessKey.key} onFocus={event => event.target.select()} /><button onClick={() => setAccessKey(null)}>Hide key</button></label>}
    </div>)}
    <label><input type="checkbox" checked={shareKey} disabled={busy} onChange={event => setShareKey(event.target.checked)} /> Provision this computer’s OpenRouter key on the machine when installing</label>
    <p>Native agent logins stay on the remote machine. Install/update restarts its DAN service; finish active work first.</p>
    {!draft && <div className="wb-update-actions"><button disabled={busy} onClick={() => { setDraft({ ...blank }); setEditing(false); }}>Add remote connection</button></div>}
    {draft && <form onSubmit={event => { event.preventDefault(); void action(async () => { await api(`/connections/${draft.id}`, { method: "PUT", body: JSON.stringify(draft) }); await refresh(); setDraft(null); setMessage("Connection saved."); }); }}>
      <h4>{editing ? "Edit connection" : "New connection"}</h4>
      <div className="wb-remote-fields">
        <label>Name<input required value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} placeholder="Mini" /></label>
        <label>Machine ID<input required disabled={editing} pattern="[a-z][a-z0-9-]{0,31}" value={draft.id} onChange={event => setDraft({ ...draft, id: event.target.value })} placeholder="mini" /></label>
        <label>SSH alias<input required value={draft.ssh_alias} onChange={event => setDraft({ ...draft, ssh_alias: event.target.value })} placeholder="mini" /></label>
        <label>Remote VPN address<input required value={draft.address} onChange={event => setDraft({ ...draft, address: event.target.value })} placeholder="10.77.77.3" /></label>
        <label>Remote workspace<input required value={draft.workspace} onChange={event => setDraft({ ...draft, workspace: event.target.value })} /></label>
        <label>Remote port<input required type="number" min="1024" max="65535" value={draft.port} onChange={event => setDraft({ ...draft, port: Number(event.target.value) })} /></label>
        <label>Python packages<select value={draft.package_source || "pypi"} onChange={event => setDraft({ ...draft, package_source: event.target.value as "pypi" | "tsinghua" })}><option value="pypi">PyPI</option><option value="tsinghua">Tsinghua mirror</option></select></label>
        <label>Relay SSH alias<input required value={draft.relay_ssh_alias} onChange={event => setDraft({ ...draft, relay_ssh_alias: event.target.value })} /></label>
        <label>Relay VPN address<input required value={draft.relay_address} onChange={event => setDraft({ ...draft, relay_address: event.target.value })} /></label>
        <label>Relay port<input required type="number" min="1024" max="65535" value={draft.relay_port} onChange={event => setDraft({ ...draft, relay_port: Number(event.target.value) })} /></label>
        <label><input type="checkbox" checked={draft.ssh_via_relay !== false} onChange={event => setDraft({ ...draft, ssh_via_relay: event.target.checked })} /> Bootstrap SSH through the relay</label>
      </div>
      <p>Use a different relay port for each machine. SSH keys remain on this computer; existing host keys must already be trusted.</p>
      <div className="wb-update-actions"><button disabled={busy} type="submit">Save connection</button><button disabled={busy} type="button" onClick={() => setDraft(null)}>Cancel</button></div>
    </form>}
    {job && <p role={job.status === "failed" ? "alert" : "status"}>{job.message}</p>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
