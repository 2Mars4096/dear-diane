import { useEffect, useRef, useState } from "react";
import { FolderOpen, Globe, MoreHorizontal, Pencil, Plus, Server, Smartphone, X } from "lucide-react";
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
  const [detailId, setDetailId] = useState<string | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const detailDialog = useRef<HTMLDialogElement>(null);
  const detail = connections.find(connection => connection.id === detailId);
  async function refresh() {
    const result = await api("/connections");
    setConnections(result.connections); setLocal(result.local); setMachine(result.machine || ""); setReady(true);
    return result.local;
  }
  useEffect(() => { void refresh().then(isLocal => { if (isLocal) void api("/ssh-hosts").then(value => setHosts(value.hosts || [])).catch(() => {}); }).catch(error => setError(String(error))); }, []);
  useEffect(() => {
    if (!detailId || editor) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = detailDialog.current;
    node?.showModal();
    return () => { node?.close(); previous?.focus(); };
  }, [detailId, editor]);
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
    setError(""); setMessage(""); setAccessKey(null); setDetailId(null);
    setEditor({ draft: { ...blank, ...connection, relay_enabled: connection.relay_enabled !== false, ssh_via_relay: connection.ssh_via_relay !== false, ...(phone && connection.relay_enabled === false ? { relay_enabled: true } : {}) }, editing: true, phone });
  };
  const openDetails = (connection: Connection) => { setDetailId(connection.id); setAccessKey(null); setError(""); setMessage(""); setShareKey(false); };
  const closeDetails = () => { setDetailId(null); setAccessKey(null); setError(""); setMessage(""); setShareKey(false); };
  const status = (connection: Connection) => checked[connection.id] === false || connection.connection_error ? "Needs attention" : checked[connection.id] ? "SSH connected" : connection.installed ? "Installed" : "Not connected";
  if (!local) return <section className="wb-desktop-updates" aria-label="Remote connection"><h3>{machine}</h3><p>Connected</p><form method="post" action="/remote/logout"><button>Disconnect</button></form></section>;
  return <section className="wb-desktop-updates wb-remote-connections" aria-label="Remote connections">
    <header className="wb-ssh-heading"><h3>SSH connections</h3><button className="wb-ssh-add" aria-label="Add SSH connection" disabled={busy || !ready} onClick={() => {
      setError(""); setMessage(""); setAccessKey(null); setDetailId(null);
      let relayPort = 8765;
      while (connections.some(item => item.relay_enabled !== false && item.relay_address === blank.relay_address && item.relay_port === relayPort)) relayPort++;
      setEditor({ draft: { ...blank, relay_port: relayPort }, editing: false, phone: false });
    }}><Plus size={14} />Add</button></header>
    {!ready && !error && <p role="status">Loading connections…</p>}
    {ready && !connections.length && <div className="wb-ssh-empty"><Server size={22} /><strong>No SSH connections</strong></div>}
    {connections.length > 0 && <ul className="wb-ssh-list" aria-label="SSH hosts">{connections.map(connection => <li className="wb-ssh-row" key={connection.id}>
      <Globe size={19} className="wb-ssh-host-icon" aria-hidden="true" />
      <button className="wb-ssh-host" onClick={() => openDetails(connection)}>
        <strong>{connection.name}</strong><span className="wb-ssh-status" data-connected={checked[connection.id] === true && !connection.connection_error}><i aria-hidden="true" />{status(connection)}</span>
      </button>
      <div className="wb-ssh-row-actions">
        <button title="Connection details" aria-label={`Connection details for ${connection.name}`} onClick={() => openDetails(connection)}><MoreHorizontal size={19} /></button>
        <button title={`Open projects on ${connection.name}`} aria-label={`Open projects on ${connection.name}`} disabled={!connection.installed || !connection.url} onClick={() => void action(async () => { await nativeShell.openExternal(connection.url!); })}><FolderOpen size={19} /></button>
        <button title="Phone access" aria-label={`Phone access for ${connection.name}`} disabled={busy} onClick={() => { openDetails(connection); }}><Smartphone size={19} /></button>
      </div>
    </li>)}</ul>}
    {detail && !editor && <dialog ref={detailDialog} className="wb-ssh-dialog wb-ssh-details" aria-labelledby="ssh-details-title" onCancel={event => { event.preventDefault(); event.stopPropagation(); closeDetails(); }}>
      <header><h3 id="ssh-details-title"><Globe size={20} />{detail.name}</h3><div><button title="Edit" aria-label="Edit SSH connection" disabled={busy} onClick={() => edit(detail)}><Pencil size={17} /></button><button aria-label="Close connection details" onClick={closeDetails}><X size={18} /></button></div></header>
      <span className="wb-ssh-status" data-connected={checked[detail.id] === true && !detail.connection_error}><i aria-hidden="true" />{status(detail)}</span>
      <dl className="wb-ssh-properties">
        <div><dt>SSH host</dt><dd>{detail.ssh_alias}</dd></div>
        <div><dt>Port</dt><dd>{detail.ssh_port || "SSH config"}</dd></div>
        <div><dt>Identity file</dt><dd>{detail.identity_file || "SSH config / agent"}</dd></div>
        {detail.ssh_via_relay !== false && <div><dt>Jump host</dt><dd>{detail.relay_ssh_alias}</dd></div>}
        {detail.installed && <div><dt>Start at boot</dt><dd>{detail.execution?.boot_persistent && detail.relay?.boot_persistent ? "On" : "Needs attention"}</dd></div>}
      </dl>
      <div className="wb-ssh-detail-actions"><button disabled={busy} onClick={() => void action(async () => {
        try { await api(`/connections/${detail.id}/inspect`, { method: "POST" }); setChecked(value => ({ ...value, [detail.id]: true })); }
        catch (error) { setChecked(value => ({ ...value, [detail.id]: false })); throw error; }
      })}>Check SSH</button></div>
      <h4>Phone access</h4>
      <div className="wb-ssh-phone-actions"><button disabled={busy} onClick={() => edit(detail, true)}>{detail.relay_enabled === false ? "Set up" : "Configure"}</button>
        {detail.installed && <button disabled={busy} onClick={() => void action(async () => { const result = await api(`/connections/${detail.id}/access-key`, { method: "POST" }); setAccessKey({ id: detail.id, key: result.access_key }); })}>Show access key</button>}
      </div>
      {detail.installed && detail.url && <a className="wb-ssh-address" href={detail.url} target="_blank" rel="noreferrer" onClick={event => { if (window.electronAPI) { event.preventDefault(); void action(async () => { await nativeShell.openExternal(detail.url!); }); } }}>{detail.url}</a>}
      {accessKey?.id === detail.id && <label>Access key<input readOnly value={accessKey.key} onFocus={event => event.target.select()} /><button onClick={() => setAccessKey(null)}>Hide key</button></label>}
      {detail.relay_enabled !== false && <details className="wb-ssh-advanced"><summary>Installation</summary>
        <label className="wb-ssh-check"><input type="checkbox" checked={shareKey} disabled={busy} onChange={event => setShareKey(event.target.checked)} />Copy this Mac’s OpenRouter key</label>
        <p>Restarts remote DAN. Finish active work first.</p>
        <button className="wb-ssh-install" disabled={busy} onClick={() => void action(async () => { setJob(await api(`/connections/${detail.id}/install`, { method: "POST", body: JSON.stringify({ share_openrouter: shareKey }) })); })}>{detail.installed ? "Update installation" : "Install DAN"}</button>
      </details>}
      {detail.connection_error && <p role="alert" className="wb-ssh-error">{detail.connection_error}</p>}
      {job && <p role={job.status === "failed" ? "alert" : "status"}>{job.message}</p>}
      {error && <p role="alert" className="wb-ssh-error">{error}</p>}
    </dialog>}
    {editor && <ConnectionEditor initial={editor.draft} editing={editor.editing} phone={editor.phone} hosts={hosts} busy={Boolean(busy)} error={error} onClose={() => { setEditor(null); setError(""); }} onSave={value => void action(async () => {
      const connection = { ...value, id: editor.editing ? value.id : newId(value.ssh_alias, connections) };
      await api(`/connections/${connection.id}`, { method: "PUT", headers: editor.editing ? {} : { "If-None-Match": "*" }, body: JSON.stringify(connection) });
      await refresh(); setEditor(null); setChecked(value => { const next = { ...value }; delete next[connection.id]; return next; }); setMessage(`${connection.name} saved.`);
    })} />}
    {job && !detail && <p role={job.status === "failed" ? "alert" : "status"}>{job.message}</p>}
    {message && <p role="status">{message}</p>}
    {error && !editor && !detail && <p role="alert">{error}</p>}
    {!ready && error && <button onClick={() => void action(async () => { await refresh(); })}>Retry</button>}
  </section>;
}
