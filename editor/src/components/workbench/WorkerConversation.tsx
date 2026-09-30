import { useEffect, useRef, useState } from "react";
import { requestJson } from "../../lib/http";
import type { TeamWorker, WorkerRequest } from "./teamPresentation";

type Entry = { cursor: number; event: Record<string, unknown> };
function eventText(row: Record<string, unknown>): string {
  const item = row.item as Record<string, unknown> | undefined;
  const message = row.message as { content?: { text?: string }[] } | undefined;
  return String(row.text || item?.text || item?.command || row.summary ||
    (typeof row.result === "string" ? row.result : "") || message?.content?.map(block => block.text || "").filter(Boolean).join("\n") || "");
}
export function workerUrl(worker: TeamWorker) {
  return `/api/native-workers/${encodeURIComponent(worker.parent_run_id)}/${encodeURIComponent(worker.worker_id)}`;
}
export default function WorkerConversation({ worker, onChange }: { worker: TeamWorker; onChange?: (worker: TeamWorker) => void }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const cursor = useRef<number | null>(null);
  const disposed = useRef(false);
  const [before, setBefore] = useState<number | null>(null);
  const [worktree, setWorktree] = useState<{ tree: string; diff: string; truncated: boolean } | null>(null);
  const [applied, setApplied] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const url = workerUrl(worker);
  async function load(older = false) {
    const page = await requestJson<{ entries: Entry[]; before: number | null; has_more?: boolean }>(`${url}/events${older && before !== null ? `?before=${before}` : cursor.current !== null ? `?after=${cursor.current}` : ""}`);
    if (disposed.current) return;
    const initial = cursor.current === null;
    if (page.entries.length) cursor.current = Math.max(cursor.current ?? -1, page.entries[page.entries.length - 1].cursor);
    setEntries(current => [...new Map([...current, ...page.entries].map(row => [row.cursor, row])).values()].sort((a, b) => a.cursor - b.cursor));
    if (older || initial) setBefore(page.before);
    if (!older && page.has_more && page.entries.length && !disposed.current) await load();
  }
  useEffect(() => {
    disposed.current = false;
    // The component is keyed by worker ID. Poll only this expanded conversation.
    const refresh = async () => { if (!disposed.current) await load().catch(e => !disposed.current && setError(String(e.message))); };
    void refresh();
    const timer = ["running", "needs_input"].includes(worker.status) ? window.setInterval(() => void refresh(), 2000) : undefined;
    return () => { disposed.current = true; window.clearInterval(timer); };
  }, [url, worker.status]); // eslint-disable-line react-hooks/exhaustive-deps
  async function send(action: string, body: unknown) {
    setBusy(true); setError("");
    try {
      const result = await requestJson<TeamWorker>(`${url}/${action}`, { method: "POST", body: JSON.stringify(body), timeoutMs: 60000 });
      onChange?.(result); if (action === "reply") setDraft("");
      if (action === "apply") { setWorktree(null); setApplied(true); }
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function reviewWorktree() {
    setBusy(true); setError(""); setApplied(false);
    try {
      const body = await requestJson<{ tree: string; diff: string; truncated: boolean }>(`${url}/changes`);
      setWorktree(body);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <section className="wb-worker-conversation" aria-label="Worker conversation">
    {worker.requests?.map(request => <WorkerQuestion key={String(request.id)} request={request} busy={busy} onAnswer={body => void send("answer", body)} />)}
    {before !== null && <button disabled={busy} onClick={() => void load(true).catch(e => setError(e.message))}>Load earlier</button>}
    {entries.map(({ cursor, event }) => <details key={cursor} className="wb-worker-event">
      <summary>{eventText(event) || String(event.type || "Event")}</summary>
      <pre>{JSON.stringify(event, null, 2)}</pre>
    </details>)}
    {worker.source_workspace && <p>Working copy: <code>{worker.workspace_root}</code></p>}
    {worker.can_apply && <button disabled={busy} onClick={() => void reviewWorktree()}>Review changes</button>}
    {worktree && <><pre>{worktree.diff || "No changes to apply."}</pre>{worktree.truncated && <p>Large diff shortened. Review the working copy for the complete changes.</p>}
      {worker.can_apply && worktree.diff && <button disabled={busy} onClick={() => void send("apply", { tree: worktree.tree })}>Apply changes to project</button>}</>}
    {applied && <p role="status">Changes applied to project.</p>}
    {worker.can_reply && !worker.native_session_id && worker.backend !== "dan" && <p>No native session was saved. Retry will use the saved task and output.</p>}
    {worker.can_reply && !worker.requests?.length && <form onSubmit={event => { event.preventDefault(); if (draft.trim() && !busy) void send("reply", { prompt: draft }); }}>
      <label>Reply to worker<textarea value={draft} onChange={event => setDraft(event.target.value)} rows={3} /></label>
      <button disabled={busy || !draft.trim()} type="submit">{worker.status === "running" ? "Send" : "Continue"}</button>
      {["failed", "interrupted", "stopped"].includes(worker.status) && <button type="button" disabled={busy} onClick={() => void send("reply", { prompt: "Continue your assigned task from the saved state. Inspect what already completed before acting; do not repeat completed actions. Report any uncertainty before retrying side effects." })}>Resume task</button>}
    </form>}
    {error && <p role="alert" className="wb-team-error">{error}</p>}
  </section>;
}
function WorkerQuestion({ request, busy, onAnswer }: { request: WorkerRequest; busy: boolean; onAnswer: (body: unknown) => void }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const questions = request.params.questions;
  return <form onSubmit={event => { event.preventDefault(); onAnswer({ request_id: String(request.id), answers }); }}>
    {questions ? <>{questions.map(q => <div key={q.id}><p>{q.question}</p>
      {q.options?.map(option => <button type="button" key={option.label} title={option.description} disabled={busy} aria-pressed={answers[q.id] === option.label} onClick={() => { const next = { ...answers, [q.id]: option.label }; setAnswers(next); if (questions.length === 1) onAnswer({ request_id: String(request.id), answers: next }); }}>{option.label}</button>)}
      <textarea aria-label={q.question} required value={answers[q.id] || ""} onChange={event => setAnswers(a => ({ ...a, [q.id]: event.target.value }))} />
    </div>)}<button disabled={busy || questions.some(q => !answers[q.id]?.trim())}>Answer</button></> : <>
      <strong>Permission needed</strong>
      <p>{String(request.params.reason || (request.method.includes("commandExecution") ? "Run this command?" : "Allow these file changes?"))}</p>
      {typeof request.params.command === "string" && <pre>{request.params.command}</pre>}
      <details><summary>Request details</summary><pre>{JSON.stringify(request.params, null, 2)}</pre></details>
      <button type="button" disabled={busy} onClick={() => onAnswer({ request_id: String(request.id), decision: "accept" })}>Allow once</button>
      <button type="button" disabled={busy} onClick={() => onAnswer({ request_id: String(request.id), decision: "decline" })}>Deny</button>
    </>}
  </form>;
}
