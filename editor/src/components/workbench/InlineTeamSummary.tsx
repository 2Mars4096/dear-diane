import { useState, useSyncExternalStore } from 'react';
import { ChevronRight } from 'lucide-react';
import { TeamWorkerStore } from './teamWorkerStore';
import { agentLabel, startedAt, workerState, workerTaskSummary, type TeamWorker } from './teamPresentation';

/** Reuses the existing team feed; expanding does not start another poller. */
export function InlineTeamSummary({ store, runId, lead, onOpenWorker }: {
  store: TeamWorkerStore; runId: string; lead: string; onOpenWorker: (worker: TeamWorker) => void;
}) {
  const [open, setOpen] = useState(false);
  const workers = useSyncExternalStore(store.subscribe, open ? store.getSnapshot : store.getOverview);
  const current = workers.filter(worker => worker.parent_run_id === runId && !worker.parent_worker_id);
  const latest = new Map<string, TeamWorker>();
  for (const worker of current) {
    const key = worker.profile_id || worker.worker_id;
    const previous = latest.get(key);
    if (!previous || startedAt(worker) >= startedAt(previous)) latest.set(key, worker);
  }
  const rows = [...latest.values()];
  if (!rows.length) return null;
  const working = rows.filter(worker => ['running', 'needs_input'].includes(worker.status)).length;
  return <section className="wb-inline-team" aria-label={`Team for ${lead}`}>
    <button type="button" className="wb-inline-team-toggle" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <ChevronRight size={13} /><strong>Lead · {lead}</strong><span>{working ? `${working} working` : `${rows.length} workers`}</span>
    </button>
    {open && <ul className="wb-team-slots">{rows.map(worker => <li className="wb-team-slot" key={worker.worker_id}>
      <div className="wb-team-slot-heading"><button type="button" onClick={() => onOpenWorker(worker)} title="Open worker conversation"><strong>{worker.worker_name || agentLabel(worker.backend)} · {agentLabel(worker.backend)}</strong></button><span>{["running", "needs_input"].includes(worker.status) ? workerState(worker) : "Available"}</span></div>
      <ul className="wb-worker-task-summary">{workerTaskSummary(worker).map((line, index) => <li key={index}>{line}</li>)}</ul>
    </li>)}</ul>}
  </section>;
}
