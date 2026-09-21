export type Totals = { input_fresh: number; cache_read: number; cache_write: number; output: number; reasoning: number; total: number; peak_context: number; cache_hit: number; calls: number; rounds: number; tool_calls: number; compactions: number; cold_rebuilds: number; models: string[] };
export type SessionRow = { id: string; backend: string; account: string; session_id: string; title: string; cwd: string; updated_at: number; totals: Totals; subagents: number; subagent_total: number; total: number; heaviest_round: number; dan?: { role: string; run_id: string; worker_id: string } | null };
export type Share = { key: string; count: number; added: number; burden: number; weight: number; share: number };
export type Step = { i: number; round: number; kind: string; tool?: string; action: string; stage: string; detail: string; added: number; result?: number; burden: number; weight: number; error?: boolean; flags?: string[]; child_total?: number };
export type Round = { index: number; title: string; started_at: string | null; calls: number; steps: number; total: number; output: number; peak_context: number; cache_hit: number; top_stage: string };
export type Report = SessionRow & { rounds: Round[]; context_curve: number[]; by_action: Share[]; by_stage: Share[]; unattributed: number; context_total: number; top_steps: Step[];
  flags: { flag: string; count: number; burden: number; examples: string[] }[]; subagents: { name: string; type: string; totals: Totals }[]; advice: { tokens: number; title: string; detail: string }[]; labelled_by: string };
export type Day = { day: string; total: number; sessions: number; claude: number; codex: number };
export type Overview = { sessions: number; total: number; subagent_total: number; totals: Totals; days: Day[]; by_project: Share[]; by_agent: Share[]; by_model: Share[]; by_stage: Share[]; by_action: Share[];
  flags: Report["flags"]; top_sessions: Pick<SessionRow, "id" | "title" | "backend" | "cwd" | "total" | "updated_at">[]; concentration: number; advice: Report["advice"]; actions: Record<string, string>; stages: Record<string, string> };
export type SessionFilter = "all" | "claude" | "codex" | "dan";

export const BACKENDS: Record<string, string> = { claude: "Claude Code", codex: "Codex" };
export const FLAGS: Record<string, string> = { oversized_output: "Large tool results", duplicate_read: "Files re-read unchanged", repeated_command: "Repeated commands", failed_call: "Failed tool calls" };

export function formatTokens(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0";
  if (value >= 1e9) return `${(value / 1e9).toFixed(value >= 1e10 ? 0 : 1)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(value >= 1e7 ? 0 : 1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(value >= 1e4 ? 0 : 1)}k`;
  return String(Math.round(value));
}

export function percent(share: number): string {
  if (!(share > 0)) return "0%";
  return share < 0.01 ? "<1%" : `${Math.round(share * 100)}%`;
}

export function folderName(cwd: string): string {
  return cwd.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
}

export function visibleSessions(rows: SessionRow[], filter: SessionFilter, sort: "recent" | "tokens"): SessionRow[] {
  const kept = rows.filter((row) => filter === "all" || (filter === "dan" ? Boolean(row.dan) : row.backend === filter));
  return [...kept].sort((a, b) => sort === "tokens" ? b.total - a.total : b.updated_at - a.updated_at);
}

/** Points for an SVG polyline of context size per model call. */
export function curvePoints(values: number[], width: number, height: number): string {
  const peak = Math.max(...values, 1);
  return values.map((value, index) => `${(values.length < 2 ? 0 : (index / (values.length - 1)) * width).toFixed(1)},${(height - (value / peak) * height).toFixed(1)}`).join(" ");
}

/** Every calendar day from the first to the last entry, so quiet days show as gaps. */
export function fillDays(days: Day[]): Day[] {
  if (!days.length) return [];
  const byDay = new Map(days.map((day) => [day.day, day]));
  const filled: Day[] = [];
  const end = new Date(`${days[days.length - 1].day}T00:00:00Z`).getTime();
  for (let time = new Date(`${days[0].day}T00:00:00Z`).getTime(); time <= end && filled.length < 120; time += 86_400_000) {
    const key = new Date(time).toISOString().slice(0, 10);
    filled.push(byDay.get(key) ?? { day: key, total: 0, sessions: 0, claude: 0, codex: 0 });
  }
  return filled;
}
