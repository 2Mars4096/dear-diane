import type { ChatV2AgentRunEvent } from "../../lib/chatV2Api";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function text(value: unknown): string { return typeof value === "string" ? value : ""; }

export function eventAgentName(event: ChatV2AgentRunEvent): string {
  const payload = event.payload ?? {};
  const backend = text(payload.backend ?? payload.agent_backend ?? payload.worker_backend).toLowerCase();
  const name = text(payload.worker_name ?? payload.agent_name);
  if (name.trim()) return /^(?:Super\s+)?DAN$/i.test(name.trim()) ? "Diane" : name;
  if (backend.includes("claude")) return "Claude Code";
  if (backend.includes("codex")) return "Codex";
  if (backend.includes("antigravity")) return "Antigravity";
  if (backend.includes("cursor")) return "Cursor";
  return "Diane";
}

/** Read only emitted reasoning/tool data; never manufacture hidden thinking. */
export function eventDisclosure(event: ChatV2AgentRunEvent) {
  const payload = event.payload ?? {};
  const raw = record(payload.codex_event ?? payload.claude_event ?? payload.raw);
  const nativeEvent = Object.keys(record(raw.event)).length ? record(raw.event) : raw;
  const item = record(nativeEvent.item ?? nativeEvent.content_block ?? payload.item);
  const kind = text(item.type || nativeEvent.type || payload.kind || event.source_event_type || event.type);
  const normalized = kind.replace(/[A-Z]/g, letter => `_${letter.toLowerCase()}`).replace(/[./]/g, "_");
  const thinking = /reasoning|thinking/i.test(kind);
  const blocks = (value: unknown): string => Array.isArray(value) ? value.map(block => text(record(block).text)).filter(Boolean).join("\n\n") : text(value);
  const delta = record(nativeEvent.delta);
  const command = text(item.command || record(item.input).command || payload.command);
  const changes = Array.isArray(item.changes) ? item.changes.map(change => text(record(change).path)).filter(Boolean) : [];
  const body = blocks(item.text || item.thinking || item.summary || item.content)
    || text(item.aggregated_output || item.aggregatedOutput || item.output || nativeEvent.output || record(nativeEvent.error).message || text(nativeEvent.delta) || delta.thinking || delta.text || nativeEvent.text || payload.output || payload.text)
    || (changes.length ? changes.map(path => `• ${path}`).join("\n") : "");
  const telemetry = /token_usage|token_count|rate_limit|heartbeat|thread_status|account_updated/.test(normalized);
  const commandEvent = Boolean(command) || /command_execution|tool_use|tool_result/.test(normalized);
  const labels: Record<string, string> = {
    agent_message: "Writing response", file_change: "Updating files", web_search: "Searching the web",
    turn_started: "Started working", turn_completed: "Work finished", worker_finished: "Worker finished",
    worker_input_requested: "Needs your input", worker_input_answered: "Input received",
    thread_started: "Session opened", error: "Error", mcp_tool_call: "Using a connected tool",
  };
  const summary = text(event.summary).trim();
  const genericWorkerStatus = /^(?:codex|claude|dan|diane|cursor|antigravity)(?: code)? (?:team member|worker): /i.test(summary);
  const streamedFragment = /(?:^|_)(?:delta|summary_part_added|content_part_added)$/.test(normalized) || normalized === "content_block_start" || normalized === "content_block_stop";
  const readableSummary = summary && !genericWorkerStatus && !/^[a-z_]+(?:[._/][a-z_]+)+$/.test(summary) && !/^[{[]/.test(summary);
  const label = thinking ? "Thinking summary" : commandEvent ? (item.status === "completed" ? "Command finished" : "Running command")
    : labels[normalized] || (readableSummary ? summary : "Activity update");
  const exitCode = item.exit_code ?? item.exitCode;
  return { label, body, command, thinking, hidden: streamedFragment || ((telemetry || label === "Activity update") && !body && !command),
    output: commandEvent, exitCode: typeof exitCode === "number" ? exitCode : undefined };

}

/** Full native agent messages, token deltas, and snapshots are distinct shapes. */
export function streamedMessageContent(previous: string, event: ChatV2AgentRunEvent): string {
  if (event.type !== "model_text_delta") return previous;
  const payload = event.payload ?? {};
  if (eventDisclosure(event).thinking) return previous;
  if (typeof payload.accumulated === "string") return payload.accumulated;
  if (typeof payload.delta === "string") return previous + payload.delta;
  const body = text(payload.text);
  if (!body || body === previous) return previous;
  return previous ? `${previous}\n\n${body}` : body;
}

/** A short status summary must never replace its longer streamed answer. */
export function completedMessageContent(previous: string, summary: string, finalText = ""): string {
  if (finalText) return finalText;
  if (previous && (!summary || previous.startsWith(summary))) return previous;
  return summary || previous || "Completed.";
}
