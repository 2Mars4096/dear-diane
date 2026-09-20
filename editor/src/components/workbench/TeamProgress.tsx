import { useEffect, useState, type ReactNode } from "react";
import { Check, ChevronDown, CircleAlert, Square, X } from "lucide-react";
import { agentLabel, elapsed, leadActivity, resultLine, startedAt, stripWorkers, taskTitle, workerPhase, workerState, type TeamWorker } from "./teamPresentation";

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
  return { workers, error, stop };
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
      {leadNow && <span className="wb-team-item"><b>{lead}</b><span key={leadNow} className="wb-team-now">{leadNow}</span></span>}
      {shown.map((worker) => <span key={worker.worker_id} className="wb-team-item" data-phase={workerPhase(worker)}>
        <b>{agentLabel(worker.backend)}</b><span key={workerState(worker)} className="wb-team-now">{workerState(worker)}</span>
      </span>)}
      {more > 0 && <span className="wb-team-more">+{more}</span>}
    </>}
  </button>;
}

/** Side panel: one lane per task, live work first, finished work settled below. */
export function TeamPanel({ workers, error, onStop, onClose, header }: { workers: TeamWorker[]; error: string; onStop: (worker: TeamWorker) => void; onClose: () => void; header?: ReactNode }) {
  const live = workers.filter((worker) => workerPhase(worker) !== "settled");
  const settled = workers.filter((worker) => workerPhase(worker) === "settled");
  return <aside className="wb-activity-panel wb-team-panel" aria-label="Team">
    {header ?? <div className="wb-panel-heading"><span>Team</span><button onClick={onClose} aria-label="Close team"><X size={17} /></button></div>}
    {error && <p className="wb-team-error" role="alert">{error}</p>}
    <div className="wb-team-lanes">
      {live.map((worker) => <TeamLane key={worker.worker_id} worker={worker} onStop={onStop} />)}
      {settled.length > 0 && live.length > 0 && <div className="wb-team-divider">Done</div>}
      {settled.map((worker) => <TeamLane key={worker.worker_id} worker={worker} onStop={onStop} />)}
      {!workers.length && <p className="wb-activity-empty">No team tasks in this conversation.</p>}
    </div>
  </aside>;
}

function TeamLane({ worker, onStop }: { worker: TeamWorker; onStop: (worker: TeamWorker) => void }) {
  const [open, setOpen] = useState(false);
  const phase = workerPhase(worker);
  const running = worker.status === "running";
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
      <span>{agent} · {running ? <span key={worker.activity} className="wb-team-now">{workerState(worker)}</span> : workerState(worker)}</span>
      {running && since > 0 && <span className="wb-lane-time">{elapsed(Date.now() / 1000 - since)}</span>}
      {running && worker.can_stop !== false && <button type="button" onClick={() => onStop(worker)} aria-label={`Stop ${agent}: ${taskTitle(worker.prompt)}`}><Square size={9} />Stop</button>}
    </div>
    {outcome && <p className="wb-lane-result">{outcome}</p>}
    {open && <div className="wb-lane-details">
      {actions.length > 0 && <ol>{actions.map((action, index) => <li key={`${action.at}-${index}`}>{action.text}</li>)}</ol>}
      {(worker.response || worker.error) && <pre>{worker.response || worker.error}</pre>}
    </div>}
  </article>;
}
