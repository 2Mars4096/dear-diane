import { TeamWorkerStore } from "./teamWorkerStore";
import { namedWorkers } from "./workerNames";
import type { WorkerProfiles } from "./modelSelection";
import { lazy, Suspense, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { Check, ChevronDown, CircleAlert, Square, X } from "lucide-react";
import { agentLabel, elapsed, leadActivity, resultLine, startedAt, stripWorkers, taskTitle, workerPhase, workerState, workerReview, workerIntegration, workerTaskSummary, workerTaskTitle, orderedTeamRuns, type TeamRunDetail, type TeamWorker, type WorkerRequest } from "./teamPresentation";

export type TeamSlot = { profile_id: string; name: string; backend: string };

const WorkerConversation = lazy(() => import("./WorkerConversation"));

/** Polls the lead's team; faster while anything is running. */
export function useTeamWorkers(parentIds: string[], leadRunning: boolean, activeRunId = "", overviewOnly = false) {
  const [store] = useState(() => new TeamWorkerStore());
  const workers = useSyncExternalStore(store.subscribe, overviewOnly ? store.getOverview : store.getSnapshot);
  const setWorkers = store.update;
  const [rosters, setRosters] = useState<Record<string, TeamSlot[]>>({});
  const [leadRequests, setLeadRequests] = useState<Record<string, WorkerRequest[]>>({});
  const [error, setError] = useState("");
  const ids = JSON.stringify([...new Set([...parentIds, ...(activeRunId ? [activeRunId] : [])])].sort());
  useEffect(() => { setWorkers([]); setRosters({}); setLeadRequests({}); setError(""); }, [ids]);
  useEffect(() => {
    const parents = JSON.parse(ids) as string[];
    if (!parents.length) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lastFullRefresh = 0;
    const controller = new AbortController();
    const cache = new Map<string, { workers: TeamWorker[]; roster: TeamSlot[]; requests: WorkerRequest[] }>();
    const refresh = async () => {
      try {
        if (document.hidden) return;
        const full = !cache.size || Date.now() - lastFullRefresh >= 60000;
        const targets = full ? parents : parents.filter(id => id === activeRunId || cache.get(id)?.workers.some(worker => ["running", "needs_input"].includes(worker.status)));
        // Bounded requests; no overlapping refresh rounds or repeated history fan-out.
        for (let i = 0; i < targets.length; i += 4) {
          await Promise.all(targets.slice(i, i + 4).map(async id => {
            const response = await fetch(`/api/native-workers/${encodeURIComponent(id)}`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]) });
            if (!response.ok) throw new Error("Team status is unavailable.");
            const data = await response.json();
            cache.set(id, { workers: data.workers || [], roster: data.roster || [], requests: data.lead_requests || [] });
          }));
        }
        if (full) lastFullRefresh = Date.now();
        if (!disposed) {
          const nextWorkers = parents.flatMap(id => cache.get(id)?.workers || []);
          const nextRosters = Object.fromEntries(parents.map(id => [id, cache.get(id)?.roster || []]));
          setWorkers(nextWorkers);
          setRosters(previous => JSON.stringify(previous) === JSON.stringify(nextRosters) ? previous : nextRosters);
          const nextRequests = Object.fromEntries(parents.map(id => [id, cache.get(id)?.requests || []]));
          setLeadRequests(previous => JSON.stringify(previous) === JSON.stringify(nextRequests) ? previous : nextRequests);
          setError("");
        }
      } catch (error) { if (!disposed) setError(error instanceof Error ? error.message : String(error)); }
      finally {
        if (!disposed) timer = setTimeout(() => void refresh(), leadRunning ? 1500 : 5000);
      }
    };
    void refresh();
    return () => { disposed = true; controller.abort(); window.clearTimeout(timer); };
  }, [ids, leadRunning, activeRunId]);
  async function stop(worker: TeamWorker) {
    try {
      const response = await fetch(`/api/native-workers/${encodeURIComponent(worker.parent_run_id)}/${encodeURIComponent(worker.worker_id)}/stop`, { method: "POST" });
      if (!response.ok) throw new Error(`Could not stop ${agentLabel(worker.backend)}.`);
      const updated = await response.json() as TeamWorker;
      setWorkers((rows) => rows.map((row) => row.worker_id === updated.worker_id ? { ...row, ...updated } : row));
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
  }
  return { store, workers, rosters, leadRequests, error, stop, update: (updated: TeamWorker) => setWorkers(rows => rows.map(row => row.worker_id === updated.worker_id ? updated : row)) };
}

/** One stable line above the transcript: who is doing what right now. */
export function TeamStrip({ workers, lead, leadRunning, open, onToggle, activeRunId }: { activeRunId?: string; workers: TeamWorker[]; lead: string; leadRunning: boolean; open: boolean; onToggle: () => void }) {
  const current = activeRunId ? workers.filter(worker => worker.parent_run_id === activeRunId) : orderedTeamRuns(workers)[0]?.workers ?? [];
  if (!current.length) return null;
  const { shown, more, settled } = stripWorkers(current);
  const leadNow = leadActivity(current, leadRunning);
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

const emptyTeamRows: TeamWorker[] = [];
const emptyTeamSnapshot = () => emptyTeamRows;
const noTeamSubscription = () => () => {};

/** Side panel: one lane per task, live work first, finished work settled below. */
export function TeamPanel({ workers, error, onStop, onClose, header, onChange, selectedWorker, activeRunId, onOpenLead, runDetails = {}, rosters = {}, lead = "Lead", configuredProfiles = {}, store, active = true }: { active?: boolean; store?: TeamWorkerStore; configuredProfiles?: WorkerProfiles; lead?: string; rosters?: Record<string, TeamSlot[]>; runDetails?: Record<string, TeamRunDetail>; activeRunId?: string; onOpenLead?: (runId: string) => void; selectedWorker?: string; onChange?: (worker: TeamWorker) => void; workers: TeamWorker[]; error: string; onStop: (worker: TeamWorker) => void; onClose: () => void; header?: ReactNode }) {
  const detailedWorkers = useSyncExternalStore(active && store ? store.subscribe : noTeamSubscription, store?.getSnapshot ?? emptyTeamSnapshot);
  if (store) workers = detailedWorkers;
  const branches = (rows: TeamWorker[], parent = "", seen = new Set<string>(), compact = false): ReactNode => rows.filter(worker => (worker.parent_worker_id || "") === parent && !seen.has(worker.worker_id)).map(worker => {
    const visited = new Set(seen).add(worker.worker_id);
    return <li key={worker.worker_id}><TeamLane active={active} compact={compact} worker={worker} onStop={onStop} onChange={onChange} selected={selectedWorker === worker.worker_id} />{rows.some(child => child.parent_worker_id === worker.worker_id) && <ul className="wb-team-children" aria-label="Child sessions">{branches(rows, worker.worker_id, visited)}</ul>}</li>;
  });
  const runs = orderedTeamRuns(workers, activeRunId, runDetails);
  return <aside className="wb-activity-panel wb-team-panel" aria-label="Team">
    {header ?? <div className="wb-panel-heading"><span>Team</span><button onClick={onClose} aria-label="Close team"><X size={17} /></button></div>}
    {error && <p className="wb-team-error" role="alert">{error}</p>}
    <div className="wb-team-lanes">
      {!activeRunId && Object.values(configuredProfiles).some(profile => profile.enabled || profile.backend) && <section aria-label="Configured team">
        <p className="wb-team-lead-identity">Lead · {lead}</p>
        <ul className="wb-team-slots">{Object.entries(namedWorkers(configuredProfiles)).filter(([, profile]) => profile.enabled || profile.backend).map(([id, profile]) => {
          const latest = workers.filter(worker => !worker.parent_worker_id && (worker.profile_id || (worker.origin ? "" : worker.backend)) === id).sort((a, b) => startedAt(b) - startedAt(a))[0];
          return <li key={id} className="wb-team-slot"><div className="wb-team-slot-heading"><strong>{profile.name} · {agentLabel(profile.backend || id)}</strong><span>Idle</span></div>{latest && <ul className="wb-worker-task-summary" aria-label={`${profile.name} task summary`}>{workerTaskSummary(latest).map((text, index) => <li key={index}>{text}</li>)}</ul>}</li>;
        })}</ul>
      </section>}
      {runs.map(run => <TeamRunBranch key={run.id} title={run.title} startedAt={run.startedAt} current={run.id === activeRunId} count={rosters[run.id]?.length || run.workers.length} selected={run.workers.some(worker => worker.worker_id === selectedWorker)} onOpenLead={onOpenLead ? () => onOpenLead(run.id) : undefined}>
        {run.id === activeRunId && <p className="wb-team-lead-identity">Lead · {lead}</p>}
        {rosters[run.id]?.length ? <>
          <p className="wb-team-run-meta">{rosters[run.id].filter(slot => run.workers.some(worker => worker.profile_id === slot.profile_id && ["running", "needs_input"].includes(worker.status))).length} working · {rosters[run.id].filter(slot => !run.workers.some(worker => worker.profile_id === slot.profile_id && ["running", "needs_input"].includes(worker.status))).length} idle</p>
          <ul className="wb-team-slots">{rosters[run.id].map(slot => {
            const assigned = run.workers.filter(worker => !worker.parent_worker_id && (worker.profile_id || (worker.origin ? "" : worker.backend)) === slot.profile_id);
            const live = assigned.filter(worker => ["running", "needs_input"].includes(worker.status));
            const settled = assigned.filter(worker => !["running", "needs_input"].includes(worker.status));
            const summaryWorkers = live.length ? live : [...settled].sort((a, b) => startedAt(b) - startedAt(a)).slice(0, 1);
            const selected = assigned.some(worker => worker.worker_id === selectedWorker);
            const allChildren = run.workers.filter(worker => !!worker.parent_worker_id);
            return <li key={slot.profile_id} className="wb-team-slot">
              <div className="wb-team-slot-heading"><strong>{slot.name} · {agentLabel(slot.backend)}</strong><span>{live.length ? "Working" : "Idle"}</span></div>
              {!!summaryWorkers.length && <ul className="wb-worker-task-summary" aria-label={`${slot.name} task summary`}>{summaryWorkers.flatMap(worker => workerTaskSummary(worker).map((text, index) => <li key={`${worker.worker_id}-${index}`}>{text}</li>))}</ul>}
              {!!live.length && <ul className="wb-team-worker-tasks" aria-label={`${slot.name} tasks`}>{branches([...live, ...allChildren], "", new Set(), true)}</ul>}
              {!!settled.length && <details open={selected || undefined} className="wb-team-past-tasks"><summary>{settled.length} previous {settled.length === 1 ? "assignment" : "assignments"}</summary><ul className="wb-team-children">{branches([...settled, ...allChildren])}</ul></details>}
            </li>;
          })}</ul>
          <ul className="wb-team-children">{branches(run.workers.filter(worker => !!worker.parent_worker_id || !rosters[run.id].some(slot => slot.profile_id === (worker.profile_id || (worker.origin ? "" : worker.backend)))))}</ul>
        </> : <ul className="wb-team-children" aria-label="Workers">{branches(run.workers)}</ul>}
        {!run.workers.length && !rosters[run.id]?.length && <p className="wb-activity-empty">No delegated workers in this run.</p>}
      </TeamRunBranch>)}
      {!runs.length && !Object.values(configuredProfiles).some(profile => profile.enabled || profile.backend) && <p className="wb-activity-empty">No team tasks in this conversation.</p>}
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

function WorkerElapsed({ since, attempt, active = true }: { since: number; attempt: boolean; active?: boolean }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [since, active]);
  return <span className="wb-lane-time" title={attempt ? "Elapsed time for this attempt" : "Elapsed since this session started"}>{elapsed(now / 1000 - since)}</span>;
}

function TeamLane({ worker, onStop, onChange, selected, compact, active }: { active?: boolean; compact?: boolean; selected?: boolean; onChange?: (worker: TeamWorker) => void; worker: TeamWorker; onStop: (worker: TeamWorker) => void }) {
  const [open, setOpen] = useState(false);
  useEffect(() => { if (selected) setOpen(true); }, [selected]);
  const phase = workerPhase(worker);
  const running = worker.status === "running" || worker.status === "needs_input";
  const since = startedAt(worker);
  const outcome = running ? "" : resultLine(worker);
  const actions = (worker.actions ?? []).slice(-8);
  const agent = agentLabel(worker.backend);
  return <article className={`wb-lane${compact ? " wb-lane-compact" : ""}`} data-phase={phase} data-status={worker.status}>
    <button type="button" className="wb-lane-title" aria-expanded={open} onClick={() => setOpen(!open)} title={worker.prompt}>
      <span className="wb-lane-mark" aria-hidden="true">{running ? <span className="wb-live-dot" /> : worker.status === "completed" ? <Check size={12} /> : phase === "attention" ? <CircleAlert size={12} /> : <span className="wb-lane-idle" />}</span>
      <strong>{compact ? workerState(worker) : workerTaskTitle(worker)}</strong>
      <ChevronDown size={12} />
    </button>
    <div className="wb-lane-meta">
      {!compact && <span>{worker.origin === "codex_subagent" ? "Codex subagent" : worker.worker_name || "Worker"} · {agent} · {running ? <span key={workerState(worker)} className="wb-team-now">{workerState(worker)}</span> : workerState(worker)}</span>}
      {running && since > 0 && <WorkerElapsed active={active} since={since} attempt={worker.attempt_started_at !== undefined} />}
      {running && worker.can_stop !== false && <button type="button" onClick={() => onStop(worker)} aria-label={`Stop ${agent}: ${taskTitle(worker.prompt)}`}><Square size={9} />Stop</button>}
    </div>
    {worker.status === "completed" && <p className="wb-lane-review">{workerReview(worker)}</p>}
    {outcome && <p className="wb-lane-result">{outcome}</p>}
    {open && <div className="wb-lane-details">
      <p className="wb-eyebrow">Current activity</p>
      <p className="wb-worker-current-action">{worker.activity || workerState(worker)}</p>
      <dl className="wb-worker-handoff">
        <dt>{worker.parent_worker_id ? "Assignment from parent worker" : "Assignment from lead"}</dt><dd>{worker.prompt}</dd>
        {!!worker.task_contract?.acceptance?.length && <><dt>Acceptance criteria</dt><dd><ul>{worker.task_contract.acceptance.map((criterion, index) => <li key={index}>{criterion}</li>)}</ul></dd></>}
        <dt>Lead review</dt><dd>{workerReview(worker)}{worker.lead_review?.attempt === worker.attempt && worker.lead_review?.evidence?.map((evidence, index) => <p key={index}>{evidence}</p>)}</dd>
        <dt>Integration</dt><dd>{workerIntegration(worker)}</dd>
        <dt>Result</dt><dd>{worker.response || worker.error || (running ? "Work in progress" : "No result recorded")}</dd>
      </dl>
      <p className="wb-eyebrow">Worker session</p>
      <Suspense fallback={<p>Loading conversation…</p>}><WorkerConversation active={active} worker={worker} onChange={onChange} /></Suspense>
      {actions.length > 0 && <ol>{actions.map((action, index) => <li key={`${action.at}-${index}`}>{action.text}</li>)}</ol>}
    </div>}
  </article>;
}
