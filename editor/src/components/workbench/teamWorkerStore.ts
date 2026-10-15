import { workerState, type TeamWorker } from './teamPresentation';

/** Full worker detail changes notify the panel, without invalidating workspace status. */
export class TeamWorkerStore {
  private rows: TeamWorker[] = [];
  private overview: TeamWorker[] = [];
  private overviewKey = '';
  private listeners = new Set<() => void>();
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getSnapshot = () => this.rows;
  getOverview = () => this.overview;
  update = (value: TeamWorker[] | ((previous: TeamWorker[]) => TeamWorker[])) => {
    const next = typeof value === 'function' ? value(this.rows) : value;
    if (next === this.rows || JSON.stringify(next) === JSON.stringify(this.rows)) return;
    this.rows = next;
    const key = JSON.stringify(next.map(worker => [worker.worker_id, worker.parent_run_id, worker.backend, worker.worker_name, worker.status, workerState(worker), worker.created_at, worker.attempt_started_at]));
    if (key !== this.overviewKey) { this.overviewKey = key; this.overview = next; }
    this.listeners.forEach(listener => listener());
  };
}
