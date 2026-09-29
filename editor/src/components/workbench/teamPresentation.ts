export type TeamAction = { text: string; at: number };
export type TeamWorker = {
  worker_id: string; parent_run_id: string; backend: string; status: string;
  prompt: string; response: string; error: string; native_session_id: string;
  can_stop?: boolean; origin?: string;
  created_at?: number; activity?: string; actions?: TeamAction[];
};
export type WorkerPhase = "attention" | "active" | "settled";

const agentNames: Record<string, string> = { codex: "Codex", claude: "Claude", antigravity: "Antigravity", cursor: "Cursor", dan: "Diane" };
export function agentLabel(backend: string): string {
  return agentNames[backend] ?? (backend ? backend[0].toUpperCase() + backend.slice(1) : "Agent");
}

function clip(text: string, limit: number): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length <= limit ? line : `${line.slice(0, limit - 1).trimEnd()}…`;
}

/** The first line or sentence of the delegated prompt names the task. */
export function taskTitle(prompt: string): string {
  const first = prompt.trim().split(/\n/)[0].replace(/^[#>*\-\s]+/, "");
  const sentence = first.match(/^.{12,}?[.!?](?=\s|$)/)?.[0] ?? first;
  return clip(sentence.replace(/[.!]$/, ""), 64) || "Untitled task";
}

export function workerPhase(worker: TeamWorker): WorkerPhase {
  if (worker.status === "running") return "active";
  if (worker.status === "needs_input" || worker.status === "failed") return "attention";
  return "settled";
}

/** Present tense for running work; a plain state otherwise. */
export function workerState(worker: TeamWorker): string {
  switch (worker.status) {
    case "running": return lowerFirst(worker.activity || "Starting");
    case "needs_input": return "needs your input";
    case "failed": return "failed";
    case "interrupted": return "interrupted";
    case "stopped": return "stopped";
    case "completed": return "done";
    default: return worker.status.replaceAll("_", " ");
  }
}
function lowerFirst(text: string): string {
  return /^[A-Z][a-z]/.test(text) ? text[0].toLowerCase() + text.slice(1) : text;
}

/** One line of outcome for a settled lane. */
export function resultLine(worker: TeamWorker): string {
  const source = worker.status === "failed" ? worker.error : worker.response || worker.error;
  const line = source.split("\n").map((row) => row.replace(/^[#>*\-\s`]+/, "").trim()).find(Boolean) ?? "";
  return clip(line, 120);
}

const rank: Record<WorkerPhase, number> = { attention: 0, active: 1, settled: 2 };
/** Strip entries: needs-attention first, then running, each in start order. */
export function stripWorkers(workers: TeamWorker[], limit = 2) {
  const live = workers.filter((worker) => workerPhase(worker) !== "settled")
    .map((worker, index) => ({ worker, index }))
    .sort((a, b) => rank[workerPhase(a.worker)] - rank[workerPhase(b.worker)] || a.index - b.index)
    .map(({ worker }) => worker);
  return { shown: live.slice(0, limit), more: Math.max(0, live.length - limit), settled: workers.length - live.length };
}

/** The lead's action is derived from what its team is actually doing. */
export function leadActivity(workers: TeamWorker[], leadRunning: boolean): string {
  if (!leadRunning) return "";
  if (workers.some((worker) => worker.status === "running")) return "coordinating";
  return workers.length ? "combining results" : "";
}

export function startedAt(worker: TeamWorker): number {
  return worker.actions?.[0]?.at ?? worker.created_at ?? 0;
}
export function elapsed(seconds: number): string {
  if (seconds < 60) return "<1m";
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
