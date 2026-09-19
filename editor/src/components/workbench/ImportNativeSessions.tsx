import { useEffect, useId, useRef, useState } from "react";
import type { ChatV2ThreadSummary } from "../../lib/chatV2Api";
type Session = { id: string; backend: string; account: string; title: string; can_import: boolean; reason: string; continuation?: "native" | "history" };
export function ImportNativeSessions({ workspace, workspaceId, onClose, onImport }: {
  workspace: string; workspaceId: string; onClose: () => void; onImport: (thread: ChatV2ThreadSummary) => Promise<void>;
}) {
  const tabId = useId();
  const [activeSource, setActiveSource] = useState("codex");
  const sourceNames: Record<string, string> = { codex: "Codex", claude: "Claude Code", antigravity: "Antigravity", cursor: "Cursor" };
  const dialog = useRef<HTMLDialogElement>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    if (!workspace) { setLoading(false); setError("Set this project’s folder in Edit project before importing sessions."); return () => previous?.focus(); }
    const abort = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`/api/native-sessions?workspace=${encodeURIComponent(workspace)}`, { signal: abort.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not find native sessions");
        setSessions(data.sessions || []);
      } catch (error) { if (!abort.signal.aborted) setError(String(error)); }
      finally { if (!abort.signal.aborted) setLoading(false); }
    })();
    return () => { abort.abort(); previous?.focus(); };
  }, [workspace]);
  const sourceOrder = ["codex", "claude", "antigravity", "cursor"];
  const sources = [...new Set(sessions.map((session) => session.backend))].sort((a, b) => (sourceOrder.indexOf(a) < 0 ? 99 : sourceOrder.indexOf(a)) - (sourceOrder.indexOf(b) < 0 ? 99 : sourceOrder.indexOf(b)));
  const currentSource = sources.includes(activeSource) ? activeSource : sources[0];
  const visibleSessions = sessions.filter((session) => session.backend === currentSource);
  const importableIds = visibleSessions.filter((session) => session.can_import).map((session) => session.id);
  const allSelected = importableIds.length > 0 && importableIds.every((id) => selected.includes(id));
  const partiallySelected = !allSelected && importableIds.some((id) => selected.includes(id));
  return <dialog ref={dialog} className="wb-project-settings wb-native-import" onCancel={(event) => { if (busy) event.preventDefault(); else onClose(); }} aria-labelledby="native-import-title">
    <header><h2 id="native-import-title">Import native sessions</h2><button onClick={onClose} disabled={busy} aria-label="Close import">×</button></header>
    <p>Select conversations to import as forks. Originals stay untouched.</p>
    {loading && <p>Looking for matching sessions…</p>}
    {!loading && !sessions.length && !error && <p>No matching native sessions found.</p>}
    {sources.length > 1 && <div className="wb-import-tabs" role="tablist" aria-label="Session source">{sources.map((source, index) => <button
      key={source} id={`${tabId}-tab-${source}`} role="tab" aria-selected={source === currentSource} aria-controls={`${tabId}-sessions`} tabIndex={source === currentSource ? 0 : -1}
      onClick={() => setActiveSource(source)} onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? sources.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + sources.length) % sources.length;
        setActiveSource(sources[next]);
        document.getElementById(`${tabId}-tab-${sources[next]}`)?.focus();
      }}><span>{sourceNames[source] || source}</span><small>{sessions.filter((session) => session.backend === source).length}</small></button>)}</div>}
    {visibleSessions.some((session) => session.continuation === "history") && <p className="wb-import-note">{sourceNames[currentSource] || currentSource} chats are copied as history. Continuing starts a new {sourceNames[currentSource] || currentSource} session with that history as context.</p>}
    {!!importableIds.length && <div className="wb-import-selection"><label><input type="checkbox" aria-label="Select all" aria-checked={partiallySelected ? "mixed" : allSelected} ref={(input) => { if (input) input.indeterminate = partiallySelected; }} checked={allSelected} disabled={busy} onChange={(event) => { const checked = event.target.checked; setSelected((previous) => checked ? [...new Set([...previous, ...importableIds])] : previous.filter((id) => !importableIds.includes(id))); }} /><span>Select all</span></label><span>{selected.length} selected{sources.length > 1 ? " across sources" : ""}</span></div>}
    <div id={`${tabId}-sessions`} className="wb-native-import-list" role={sources.length > 1 ? "tabpanel" : undefined} aria-labelledby={sources.length > 1 ? `${tabId}-tab-${currentSource}` : undefined}>{visibleSessions.map((session) => <label key={session.id}>
      <input type="checkbox" name="native-session" value={session.id} checked={selected.includes(session.id)} disabled={!session.can_import || busy} onChange={() => setSelected((previous) => previous.includes(session.id) ? previous.filter((id) => id !== session.id) : [...previous, session.id])} />
      <span><strong>{session.title.replace(/\s+/g, " ").slice(0, 180)}{session.title.length > 180 ? "…" : ""}</strong><small>{session.backend} · {session.account}</small>{session.reason && <small>{session.reason}</small>}</span>
    </label>)}</div>
    {error && <p role="alert">{error}</p>}
    <footer><button onClick={onClose} disabled={busy}>Cancel</button><button disabled={!selected.length || busy} onClick={async () => {
      setBusy(true); setError("");
      try {
        const failures: string[] = [];
        for (const id of selected) {
          try {
            const response = await fetch("/api/native-sessions/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_id: id, workspace, workspace_id: workspaceId, fork: true }) });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || "Import failed");
            setSelected((previous) => previous.filter((value) => value !== id));
            setSessions((previous) => previous.filter((session) => session.id !== id));
            await onImport(data);
          } catch (error) { failures.push(String(error)); }
        }
        if (failures.length) setError(`${failures.length} import(s) could not finish. ${failures[0]}`);
        else onClose();
      } catch (error) { setError(String(error)); }
      finally { setBusy(false); }
    }}>{busy ? "Importing…" : `Import ${selected.length || ""} as fork${selected.length === 1 ? "" : "s"}`}</button></footer>
  </dialog>;
}
