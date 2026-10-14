export type WorkerRequest = { id: string | number; method: string; params: { questions?: { id: string; question: string; options?: { label: string; description?: string }[] }[]; [key: string]: unknown } };
export type TeamAction = { text: string; at: number };
export type TeamWorker = {
  worker_id: string; parent_run_id: string; backend: string; status: string;
  prompt: string; response: string; error: string; native_session_id: string;
  can_stop?: boolean; can_reply?: boolean; can_apply?: boolean; source_workspace?: string; workspace_root?: string; requests?: WorkerRequest[]; origin?: string;
  profile_id?: string; worker_name?: string; parent_worker_id?: string;
  attempt?: number; task_contract?: { scope?: string; acceptance?: string[] };
  lead_review?: { attempt: number; decision: string; evidence?: string[] } | null;
  baseline_tree?: string; integrated_tree?: string; integrated_attempt?: number;
  created_at?: number; attempt_started_at?: number; activity?: string; actions?: TeamAction[];
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
  if (worker.status === "needs_input" || worker.status === "failed" || worker.status === "interrupted") return "attention";
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
  return worker.attempt_started_at ?? worker.created_at ?? worker.actions?.[0]?.at ?? 0;
}
export function elapsed(seconds: number): string {
  if (seconds < 60) return "<1m";
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** Only a review of this exact attempt is current. Native-owned workers have no Diane review contract. */
export function workerReview(worker: TeamWorker): string {
  const review = worker.lead_review;
  if (review && review.attempt === worker.attempt) {
    return ({ accepted: "Accepted by lead", revise: "Changes requested", blocked: "Review blocked" } as Record<string, string>)[review.decision] || "Review not recorded";
  }
  if (worker.origin === "codex_subagent") return "Review not reported";
  return worker.status === "completed" ? "Awaiting lead review" : "Not reviewed";
}
export function workerIntegration(worker: TeamWorker): string {
  if (worker.integrated_tree && worker.integrated_attempt === worker.attempt && worker.attempt !== undefined) return "Merged into parent workspace";
  if (worker.integrated_tree) return "Application recorded; may include an earlier attempt";
  return worker.baseline_tree ? "Not applied to project" : "No separate worktree integration recorded";
}


export type TeamRunDetail = { title?: string; startedAt?: number };
export function teamRunTitles(messages: { role: string; content: string; taskRunRef?: { runId?: string | null } | null }[]): Record<string, TeamRunDetail> {
  let request = "";
  const runs: Record<string, TeamRunDetail> = {};
  for (const message of messages) {
    if (message.role === "user") request = message.content;
    const id = message.taskRunRef?.runId;
    if (id && !runs[id]) runs[id] = { title: taskTitle(request) };
  }
  return runs;
}

export function orderedTeamRuns(workers: TeamWorker[], activeRunId = "", details: Record<string, TeamRunDetail> = {}) {
  const ids = [...new Set(workers.map(worker => worker.parent_run_id))];
  if (activeRunId && !ids.includes(activeRunId)) ids.push(activeRunId);
  return ids.map(id => {
    const rows = workers.filter(worker => worker.parent_run_id === id);
    const times = rows.map(worker => worker.created_at || startedAt(worker)).filter(time => time > 0);
    const time = details[id]?.startedAt || (times.length ? Math.min(...times) * 1000 : 0);
    return { id, workers: rows, startedAt: time, title: details[id]?.title || (rows.length ? rows.map(worker => taskTitle(worker.prompt)).slice(0, 2).join(" · ") : "Current task") };
  }).sort((a, b) => Number(b.id === activeRunId) - Number(a.id === activeRunId) || b.startedAt - a.startedAt);
}
