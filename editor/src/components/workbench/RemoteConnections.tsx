import { useEffect, useRef, useState } from "react";
import { Plus, Server, X } from "lucide-react";
import { nativeShell } from "../../lib/electronBridge";

interface Connection {
  id: string; name: string; ssh_alias: string; relay_ssh_alias: string;
  ssh_port?: number | null; identity_file?: string; relay_enabled?: boolean;
  address: string; port: number; relay_address: string; relay_port: number;
  workspace: string; url?: string; installed?: boolean; ssh_via_relay?: boolean;
  package_source?: "pypi" | "tsinghua"; connection_error?: string;
  execution?: { boot_persistent: boolean }; relay?: { boot_persistent: boolean };
}
interface Job { id: string; status: string; message: string }
const blank: Connection = { id: "", name: "", ssh_alias: "", ssh_port: null, identity_file: "", relay_enabled: false, ssh_via_relay: false, relay_ssh_alias: "ny", address: "", port: 8765, relay_address: "10.77.77.1", relay_port: 8765, workspace: "~/Downloads/local_projects" };
async function api(path: string, init?: RequestInit) {
  const response = await fetch(`/api/remote${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : Array.isArray(body.detail) ? body.detail.map((item: { msg: string }) => item.msg).join(". ") : "Check the connection fields and try again.");
  return body;
}
function newId(host: string, connections: Connection[]) {
  const base = (host.split("@").pop() || "host").toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
  const prefix = (/^[a-z]/.test(base) ? base : `host-${base}`).slice(0, 26) || "host";
  let id = prefix, suffix = 2;
  while (connections.some(item => item.id === id)) id = `${prefix}-${suffix++}`;
  return id;
}

function ConnectionEditor({ initial, editing, phone, hosts, busy, error, onSave, onClose }: {
  initial: Connection; editing: boolean; phone: boolean; hosts: string[]; busy: boolean; error: string;
  onSave: (value: Connection) => void; onClose: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [identity, setIdentity] = useState(Boolean(initial.identity_file));
  const dialog = useRef<HTMLDialogElement>(null);
  const field = (key: keyof Connection, value: string | number | boolean | null) => setDraft(current => ({ ...current, [key]: value }));
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const node = dialog.current!;
    node.showModal();
    return () => { node.close(); previous?.focus(); };
  }, []);
  return <dialog ref={dialog} className="wb-ssh-dialog" aria-labelledby="ssh-editor-title" onCancel={event => { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); }}>
    <form onSubmit={event => { event.preventDefault(); onSave({ ...draft, name: draft.name.trim(), ssh_alias: draft.ssh_alias.trim(), identity_file: identity ? (draft.identity_file || "").trim() : "" }); }}>
      <header><h3 id="ssh-editor-title">{phone ? "Set up phone access" : editing ? "Edit SSH connection" : "Add SSH connection"}</h3><button type="button" aria-label="Close connection editor" disabled={busy} onClick={onClose}><X size={17} /></button></header>
      <fieldset disabled={busy}>
        {phone ? <>
          <p>Reach {draft.name} from a phone through your private network, even when this Mac is offline.</p>
          <label className="wb-ssh-check"><input type="checkbox" checked={draft.relay_enabled !== false} onChange={e => field("relay_enabled", e.target.checked)} />Enable private relay access</label>
          {draft.relay_enabled !== false && <>
            <label>Remote private address<input autoFocus required value={draft.address} placeholder="10.77.77.3" onChange={e => field("address", e.target.value)} /></label>
            <label>Relay SSH host<input required value={draft.relay_ssh_alias} onChange={e => field("relay_ssh_alias", e.target.value)} /></label>
            <label>Relay private address<input required value={draft.relay_address} onChange={e => field("relay_address", e.target.value)} /></label>
            <div className="wb-ssh-pair"><label>DAN service port<input required type="number" min="1024" max="65535" value={draft.port} onChange={e => field("port", Number(e.target.value))} /></label><label>Relay port<input required type="number" min="1024" max="65535" value={draft.relay_port} onChange={e => field("relay_port", Number(e.target.value))} /></label></div>
            <p>Use an unused relay port for each machine. Your phone needs access to this private network. Saving does not install or restart services.</p>
          </>}
        </> : <>
          <label>Display name<input autoFocus required maxLength={80} value={draft.name} placeholder="My server" onChange={e => field("name", e.target.value)} /></label>
          <label>Hostname<input required value={draft.ssh_alias} list="dan-ssh-hosts" placeholder="host.com or user@host.com" onChange={e => field("ssh_alias", e.target.value)} autoCapitalize="none" autoCorrect="off" spellCheck={false} /></label>
          <datalist id="dan-ssh-hosts">{hosts.map(host => <option key={host} value={host} />)}</datalist>
          <p className="wb-ssh-hint">An SSH alias such as mini or s600 also works. No need to edit ~/.ssh/config.</p>
          <label>SSH port <span>(optional)</span><input type="number" min="1" max="65535" value={draft.ssh_port ?? ""} placeholder="From SSH config, otherwise 22" onChange={e => field("ssh_port", e.target.value ? Number(e.target.value) : null)} /></label>
          <fieldset className="wb-ssh-auth"><legend>Authentication</legend><label><input type="radio" name="ssh-auth" checked={!identity} onChange={() => setIdentity(false)} />SSH config / agent</label><label><input type="radio" name="ssh-auth" checked={identity} onChange={() => setIdentity(true)} />Identity file</label></fieldset>
          {identity && <label>Identity file path<input required value={draft.identity_file || ""} placeholder="~/.ssh/id_ed25519" onChange={e => field("identity_file", e.target.value)} spellCheck={false} /></label>}
          <details className="wb-ssh-advanced"><summary>Advanced options</summary>
            <label>Remote workspace<input required value={draft.workspace} onChange={e => field("workspace", e.target.value)} /></label>
            <label className="wb-ssh-check"><input type="checkbox" checked={draft.ssh_via_relay === true} onChange={e => field("ssh_via_relay", e.target.checked)} />Use the relay as an SSH jump host</label>
            {draft.ssh_via_relay && <label>Jump / relay SSH host<input required value={draft.relay_ssh_alias} onChange={e => field("relay_ssh_alias", e.target.value)} /></label>}
            <label>Python packages<select value={draft.package_source || "pypi"} onChange={e => field("package_source", e.target.value)}><option value="pypi">PyPI</option><option value="tsinghua">Tsinghua mirror</option></select></label>
          </details>
          <p className="wb-ssh-hint">Uses SSH keys on this Mac. For a new host, connect once in Terminal to trust its host key. Password prompts are not supported here.</p>
        </>}
      </fieldset>
      {error && <p role="alert" className="wb-ssh-error">{error}</p>}
      <footer><button type="button" disabled={busy} onClick={onClose}>Cancel</button><button type="submit" className="wb-ssh-primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button></footer>
    </form>
  </dialog>;
}

export function RemoteConnections() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [hosts, setHosts] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [local, setLocal] = useState(true);
  const [machine, setMachine] = useState("");
  const [editor, setEditor] = useState<{ draft: Connection; editing: boolean; phone: boolean } | null>(null);
  const [pending, setPending] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [shareKey, setShareKey] = useState(false);
  const [accessKey, setAccessKey] = useState<{ id: string; key: string } | null>(null);
  async function refresh() {
    const result = await api("/connections");
    setConnections(result.connections); setLocal(result.local); setMachine(result.machine || ""); setReady(true);
    return result.local;
  }
  useEffect(() => { void refresh().then(isLocal => { if (isLocal) void api("/ssh-hosts").then(value => setHosts(value.hosts || [])).catch(() => {}); }).catch(error => setError(String(error))); }, []);
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
  const edit = (connection: Connection, phone = false) => {
    setError(""); setAccessKey(null);
    setEditor({ draft: { ...blank, ...connection, relay_enabled: connection.relay_enabled !== false, ssh_via_relay: connection.ssh_via_relay !== false, ...(phone && connection.relay_enabled === false ? { relay_enabled: true } : {}) }, editing: true, phone });
  };
  if (!local) return <section className="wb-desktop-updates" aria-label="Remote connection"><h3>Connected to {machine}</h3><p>Work runs on this machine and continues when you close the browser. A server restart can interrupt active work. Set up other machines from your local DAN app.</p><form method="post" action="/remote/logout"><button>Disconnect this browser</button></form></section>;
  return <section className="wb-desktop-updates wb-remote-connections" aria-label="Remote connections">
    <header className="wb-ssh-heading"><div><h3>SSH connections</h3><p>Add a remote machine by hostname or SSH alias.</p></div><button className="wb-ssh-add" disabled={busy || !ready} onClick={() => {
      setError(""); setAccessKey(null);
      let relayPort = 8765;
      while (connections.some(item => item.relay_enabled !== false && item.relay_address === blank.relay_address && item.relay_port === relayPort)) relayPort++;
      setEditor({ draft: { ...blank, relay_port: relayPort }, editing: false, phone: false });
    }}><Plus size={14} />Add SSH connection</button></header>
    {!ready && !error && <p role="status">Loading connections…</p>}
    {ready && !connections.length && <div className="wb-ssh-empty"><Server size={22} /><div><strong>No SSH connections yet</strong><p>Choose Add SSH connection, enter a hostname, then save. Configure phone access later.</p></div></div>}
    {connections.map(connection => <div className="wb-remote-row" key={connection.id}>
      <strong>{connection.name}</strong><span>{connection.ssh_alias}{connection.ssh_port ? `:${connection.ssh_port}` : ""}{connection.ssh_via_relay !== false ? ` via ${connection.relay_ssh_alias}` : ""}</span>
      <p>{connection.installed ? (connection.execution?.boot_persistent && connection.relay?.boot_persistent ? "Installed · starts at boot" : "Installed · boot persistence needs attention") : connection.relay_enabled === false ? "SSH saved · phone access not configured" : "Saved · installation needed"}</p>
      {connection.connection_error && <p role="status">{connection.connection_error}</p>}
      <div className="wb-update-actions">
        <button disabled={busy} onClick={() => void action(async () => {
          const result = await api(`/connections/${connection.id}/inspect`, { method: "POST" });
          const tools = Object.entries(result.tools).filter(([, path]) => path).map(([name]) => name).join(", ");
          setMessage(`${connection.name}: SSH connected to ${result.hostname} · ${result.platform}. Available: ${tools || "no supported tools found"}.`);
        })}>Check SSH</button>
        <button disabled={busy} onClick={() => edit(connection)}>Edit</button>
        <button disabled={busy} onClick={() => edit(connection, true)}>{connection.relay_enabled === false ? "Set up phone access" : "Phone access"}</button>
        {connection.relay_enabled !== false && <button disabled={busy} onClick={() => void action(async () => {
          setJob(await api(`/connections/${connection.id}/install`, { method: "POST", body: JSON.stringify({ share_openrouter: shareKey }) }));
        })}>{connection.installed ? "Update installation" : "Install DAN"}</button>}
        {connection.installed && <>
          <button disabled={busy} onClick={() => void action(async () => {
            const result = await api(`/connections/${connection.id}/access-key`, { method: "POST" }); setAccessKey({ id: connection.id, key: result.access_key });
          })}>Show access key</button>
          <button onClick={() => void nativeShell.openExternal(connection.url!)}>Open {connection.name}</button>
        </>}
      </div>
      {connection.installed && <p><a href={connection.url} target="_blank" rel="noreferrer" onClick={event => { if (window.electronAPI) { event.preventDefault(); void nativeShell.openExternal(connection.url!); } }}>{connection.url}</a> · Open on your phone with access to the private network.</p>}
      {accessKey?.id === connection.id && <label>Access key<input readOnly type="text" value={accessKey.key} onFocus={event => event.target.select()} /><button onClick={() => setAccessKey(null)}>Hide key</button></label>}
    </div>)}
    {connections.some(item => item.relay_enabled !== false) && <div className="wb-ssh-install-options"><label><input type="checkbox" checked={shareKey} disabled={busy} onChange={event => setShareKey(event.target.checked)} /> Provision this computer’s OpenRouter key when installing</label><p>Native agent logins stay on the remote machine. Install/update restarts its DAN service; finish active work first.</p></div>}
    {editor && <ConnectionEditor initial={editor.draft} editing={editor.editing} phone={editor.phone} hosts={hosts} busy={Boolean(busy)} error={error} onClose={() => { setEditor(null); setError(""); }} onSave={value => void action(async () => {
      const connection = { ...value, id: editor.editing ? value.id : newId(value.ssh_alias, connections) };
      await api(`/connections/${connection.id}`, { method: "PUT", headers: editor.editing ? {} : { "If-None-Match": "*" }, body: JSON.stringify(connection) });
      await refresh(); setEditor(null); setMessage(`${connection.name} saved. ${editor.phone ? connection.relay_enabled === false ? "Phone access is disabled in this profile; existing remote services are unchanged." : "Choose Install DAN to apply the phone-access setup." : "Choose Check SSH to test the connection."}`);
    })} />}
    {job && <p role={job.status === "failed" ? "alert" : "status"}>{job.message}</p>}
    {message && <p role="status">{message}</p>}
    {error && !editor && <p role="alert">{error}</p>}
    {!ready && error && <button onClick={() => void action(async () => { await refresh(); })}>Retry</button>}
  </section>;
}
