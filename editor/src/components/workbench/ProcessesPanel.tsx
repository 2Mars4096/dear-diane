import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, Play, Square, Trash2 } from "lucide-react";

export type ManagedProcess = { id: string; name: string; command: string; cwd: string; status: string; origin: string; pid: number; started_at: number; ended_at: number | null };
const ORIGIN: Record<string, string> = { codex: "Codex", claude: "Claude Code", cursor: "Cursor", antigravity: "Antigravity", dan: "DAN", user: "You" };

export function uptime(from: number, to = Date.now() / 1000): string {
  const seconds = Math.max(0, to - from);
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d`;
}

/** Side panel: processes that keep running after an agent run ends. */
export function ProcessesPanel({ cwd, workspaceId, processes, error, onChanged, header }: { cwd: string; workspaceId: string; processes: ManagedProcess[]; error: string; onChanged: () => void; header?: ReactNode }) {
  const [command, setCommand] = useState("");
  const [busy, setBusy] = useState("");
  const [problem, setProblem] = useState("");
  async function call(url: string, init: RequestInit, key: string) {
    setBusy(key); setProblem("");
    try {
      const response = await fetch(url, init);
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "That did not work.");
      onChanged();
    } catch (caught) { setProblem(caught instanceof Error ? caught.message : String(caught)); }
    finally { setBusy(""); }
  }
  return <aside className="wb-activity-panel wb-processes" aria-label="Processes">
    {header}
    <form className="wb-process-start" onSubmit={(event) => { event.preventDefault(); if (!command.trim()) return;
      void call("/api/processes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command, cwd, workspace_id: workspaceId }) }, "start").then(() => setCommand("")); }}>
      <input value={command} onChange={(event) => setCommand(event.target.value)} placeholder="Run and keep alive, e.g. npm run dev" aria-label="Command" disabled={!cwd} />
      <button type="submit" disabled={!command.trim() || busy === "start" || !cwd} aria-label="Start process"><Play size={12} /></button>
    </form>
    {(error || problem) && <p className="wb-team-error" role="alert">{problem || error}</p>}
    {!processes.length && <p className="wb-activity-empty wb-side-empty">Nothing running. Processes started here, or by an agent through DAN, keep running after the chat ends.</p>}
    <div className="wb-team-lanes">{processes.map((item) => <ProcessLane key={item.id} item={item} busy={busy === item.id}
      onStop={() => void call(`/api/processes/${item.id}/stop`, { method: "POST" }, item.id)}
      onRemove={() => void call(`/api/processes/${item.id}`, { method: "DELETE" }, item.id)} />)}</div>
  </aside>;
}

function ProcessLane({ item, busy, onStop, onRemove }: { item: ManagedProcess; busy: boolean; onStop: () => void; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const [logs, setLogs] = useState("");
  const box = useRef<HTMLPreElement>(null);
  const running = item.status === "running";
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    const load = async () => {
      try {
        const response = await fetch(`/api/processes/${item.id}/logs?tail=24000`);
        if (response.ok && !disposed) setLogs((await response.json()).logs ?? "");
      } catch { /* keep the last output */ }
    };
    void load();
    const timer = running ? window.setInterval(() => void load(), 2000) : 0;
    return () => { disposed = true; window.clearInterval(timer); };
  }, [open, running, item.id]);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [logs]);
  return <article className="wb-lane" data-phase={running ? "active" : "settled"}>
    <button type="button" className="wb-lane-title" aria-expanded={open} onClick={() => setOpen(!open)} title={item.command}>
      <span className="wb-lane-mark" aria-hidden="true">{running ? <span className="wb-live-dot" /> : <span className="wb-lane-idle" />}</span>
      <strong>{item.name}</strong><ChevronDown size={12} />
    </button>
    <div className="wb-lane-meta">
      <span>{ORIGIN[item.origin] ?? item.origin} · {running ? `running ${uptime(item.started_at)}` : item.status}</span>
      {running ? <button type="button" disabled={busy} onClick={onStop} aria-label={`Stop ${item.name}`}><Square size={9} />Stop</button>
        : <button type="button" disabled={busy} onClick={onRemove} aria-label={`Remove ${item.name}`}><Trash2 size={10} />Remove</button>}
    </div>
    {open && <div className="wb-lane-details"><code className="wb-process-command">{item.command}</code><pre ref={box}>{logs || "No output yet."}</pre></div>}
  </article>;
}
