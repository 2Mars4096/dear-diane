import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { Check, ChevronDown, CircleAlert, Square, X } from "lucide-react";
import { agentLabel, elapsed, leadActivity, resultLine, startedAt, stripWorkers, taskTitle, workerPhase, workerState, workerReview, workerIntegration, orderedTeamRuns, type TeamRunDetail, type TeamWorker } from "./teamPresentation";

const WorkerConversation = lazy(() => import("./WorkerConversation"));

/** Polls the lead's team; faster while anything is running. */
export function useTeamWorkers(parentIds: string[], leadRunning: boolean) {
  const [workers, setWorkers] = useState<TeamWorker[]>([]);
  const [error, setError] = useState("");
  const ids = JSON.stringify([...parentIds].sort());
  const running = leadRunning || workers.some((worker) => worker.status === "running");
  useEffect(() => { setWorkers([]); setError(""); }, [ids]);
  useEffect(() => {
    const parents = JSON.parse(ids) as string[];
    if (!parents.length) return;
    let disposed = false;
    const refresh = async () => {
      try {
        const groups = await Promise.all(parents.map(async (id) => {
          const response = await fetch(`/api/native-workers/${encodeURIComponent(id)}`);
          if (!response.ok) throw new Error("Team status is unavailable.");
          return ((await response.json()).workers || []) as TeamWorker[];
        }));
        if (!disposed) { setWorkers(groups.flat()); setError(""); }
      } catch (error) { if (!disposed) setError(error instanceof Error ? error.message : String(error)); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), running ? 1500 : 5000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [ids, running]);
  async function stop(worker: TeamWorker) {
    try {
      const response = await fetch(`/api/native-workers/${encodeURIComponent(worker.parent_run_id)}/${encodeURIComponent(worker.worker_id)}/stop`, { method: "POST" });
      if (!response.ok) throw new Error(`Could not stop ${agentLabel(worker.backend)}.`);
      const updated = await response.json() as TeamWorker;
      setWorkers((rows) => rows.map((row) => row.worker_id === updated.worker_id ? { ...row, ...updated } : row));
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
  }
  return { workers, error, stop, update: (updated: TeamWorker) => setWorkers(rows => rows.map(row => row.worker_id === updated.worker_id ? updated : row)) };
}

/** One stable line above the transcript: who is doing what right now. */
export function TeamStrip({ workers, lead, leadRunning, open, onToggle }: { workers: TeamWorker[]; lead: string; leadRunning: boolean; open: boolean; onToggle: () => void }) {
  if (!workers.length) return null;
  const { shown, more, settled } = stripWorkers(workers);
  const leadNow = leadActivity(workers, leadRunning);
  const idle = !shown.length && !leadNow;
  return <button type="button" className="wb-team-strip" aria-expanded={open} aria-label="Team progress" onClick={onToggle} data-idle={idle || undefined}>
    {idle ? <span className="wb-team-item"><Check size={12} />{settled} {settled === 1 ? "task" : "tasks"} done</span> : <>
      <span className="wb-live-dot" aria-hidden="true" />
      {leadNow && <span className="wb-team-item"><b>Lead: {lead}</b><span key={leadNow} className="wb-team-now">{leadNow}</span></span>}
      {shown.map((worker) => <span key={worker.worker_id} className="wb-team-item" data-phase={workerPhase(worker)}>
        <span className="wb-team-separator" aria-hidden="true">›</span><b>{agentLabel(worker.backend)}</b><span key={workerState(worker)} className="wb-team-now">{workerState(worker)}</span>
      </span>)}
      {more > 0 && <span className="wb-team-more">+{more}</span>}
    </>}
  </button>;
}

/** Side panel: one lane per task, live work first, finished work settled below. */
export function TeamPanel({ workers, error, onStop, onClose, header, onChange, selectedWorker, activeRunId, onOpenLead, runDetails = {} }: { runDetails?: Record<string, TeamRunDetail>; activeRunId?: string; onOpenLead?: (runId: string) => void; selectedWorker?: string; onChange?: (worker: TeamWorker) => void; workers: TeamWorker[]; error: string; onStop: (worker: TeamWorker) => void; onClose: () => void; header?: ReactNode }) {
  const branches = (rows: TeamWorker[], parent = "", seen = new Set<string>()): ReactNode => rows.filter(worker => (worker.parent_worker_id || "") === parent && !seen.has(worker.worker_id)).map(worker => {
    const visited = new Set(seen).add(worker.worker_id);
    return <li key={worker.worker_id}><TeamLane worker={worker} onStop={onStop} onChange={onChange} selected={selectedWorker === worker.worker_id} />{rows.some(child => child.parent_worker_id === worker.worker_id) && <ul className="wb-team-children" aria-label="Child sessions">{branches(rows, worker.worker_id, visited)}</ul>}</li>;
  });
  const runs = orderedTeamRuns(workers, activeRunId, runDetails);
  return <aside className="wb-activity-panel wb-team-panel" aria-label="Team">
    {header ?? <div className="wb-panel-heading"><span>Team</span><button onClick={onClose} aria-label="Close team"><X size={17} /></button></div>}
    {error && <p className="wb-team-error" role="alert">{error}</p>}
    <div className="wb-team-lanes">
      <p className="wb-team-guide">The lead assigns work and reviews results. Open a worker to inspect its own session.</p>
      {runs.map(run => <TeamRunBranch key={run.id} title={run.title} startedAt={run.startedAt} current={run.id === activeRunId} count={run.workers.length} selected={run.workers.some(worker => worker.worker_id === selectedWorker)} onOpenLead={onOpenLead ? () => onOpenLead(run.id) : undefined}>
        <ul className="wb-team-children" aria-label="Workers">{branches(run.workers)}</ul>
        {!run.workers.length && <p className="wb-activity-empty">No delegated workers in this run.</p>}
      </TeamRunBranch>)}
      {!runs.length && <p className="wb-activity-empty">No team tasks in this conversation.</p>}
    </div>
  </aside>;
}

function TeamRunBranch({ title, startedAt, current, count, selected, onOpenLead, children }: { title: string; startedAt: number; current: boolean; count: number; selected: boolean; onOpenLead?: () => void; children: ReactNode }) {
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const open = selected || (expanded ?? current);
  const date = startedAt ? new Date(startedAt) : null;
  return <section className="wb-team-branch" aria-label={title}>
    <button className="wb-team-run-toggle" aria-expanded={open} onClick={() => setExpanded(!open)}>
      <ChevronDown size={14} /><strong>{title}</strong>
    </button>
    <div className="wb-team-run-meta"><span>{current ? "Current run" : "Previous run"} · {count} {count === 1 ? "worker" : "workers"}</span>{date && <time dateTime={date.toISOString()}>{date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>}</div>
    {open && <><button className="wb-team-lead" disabled={!onOpenLead} onClick={onOpenLead}>Open lead conversation</button>{children}</>}
  </section>;
}

function TeamLane({ worker, onStop, onChange, selected }: { selected?: boolean; onChange?: (worker: TeamWorker) => void; worker: TeamWorker; onStop: (worker: TeamWorker) => void }) {
  const [open, setOpen] = useState(false);
  useEffect(() => { if (selected) setOpen(true); }, [selected]);
  const phase = workerPhase(worker);
  const running = worker.status === "running" || worker.status === "needs_input";
  const since = startedAt(worker);
  const outcome = running ? "" : resultLine(worker);
  const actions = (worker.actions ?? []).slice(-8);
  const agent = agentLabel(worker.backend);
  return <article className="wb-lane" data-phase={phase} data-status={worker.status}>
    <button type="button" className="wb-lane-title" aria-expanded={open} onClick={() => setOpen(!open)} title={worker.prompt}>
      <span className="wb-lane-mark" aria-hidden="true">{running ? <span className="wb-live-dot" /> : worker.status === "completed" ? <Check size={12} /> : phase === "attention" ? <CircleAlert size={12} /> : <span className="wb-lane-idle" />}</span>
      <strong>{taskTitle(worker.prompt)}</strong>
      <ChevronDown size={12} />
    </button>
    <div className="wb-lane-meta">
      <span>{worker.origin === "codex_subagent" ? "Codex subagent" : worker.worker_name || "Worker"} · {agent} · {running ? <span key={worker.activity} className="wb-team-now">{workerState(worker)}</span> : workerState(worker)}</span>
      {running && since > 0 && <span className="wb-lane-time">{elapsed(Date.now() / 1000 - since)}</span>}
      {running && worker.can_stop !== false && <button type="button" onClick={() => onStop(worker)} aria-label={`Stop ${agent}: ${taskTitle(worker.prompt)}`}><Square size={9} />Stop</button>}
    </div>
    {worker.status === "completed" && <p className="wb-lane-review">{workerReview(worker)}</p>}
    {outcome && <p className="wb-lane-result">{outcome}</p>}
    {open && <div className="wb-lane-details">
      <dl className="wb-worker-handoff">
        <dt>{worker.parent_worker_id ? "Assignment from parent worker" : "Assignment from lead"}</dt><dd>{worker.task_contract?.scope || worker.prompt}</dd>
        {!!worker.task_contract?.acceptance?.length && <><dt>Acceptance criteria</dt><dd><ul>{worker.task_contract.acceptance.map((criterion, index) => <li key={index}>{criterion}</li>)}</ul></dd></>}
        <dt>Lead review</dt><dd>{workerReview(worker)}{worker.lead_review?.attempt === worker.attempt && worker.lead_review?.evidence?.map((evidence, index) => <p key={index}>{evidence}</p>)}</dd>
        <dt>Integration</dt><dd>{workerIntegration(worker)}</dd>
        <dt>Result</dt><dd>{worker.response || worker.error || (running ? "Work in progress" : "No result recorded")}</dd>
      </dl>
      <p className="wb-eyebrow">Worker session</p>
      <Suspense fallback={<p>Loading conversation…</p>}><WorkerConversation worker={worker} onChange={onChange} /></Suspense>
      {actions.length > 0 && <ol>{actions.map((action, index) => <li key={`${action.at}-${index}`}>{action.text}</li>)}</ol>}
    </div>}
  </article>;
}
