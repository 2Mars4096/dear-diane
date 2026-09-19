import type { ChatV2AgentRunEvent } from "../../lib/chatV2Api";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function text(value: unknown): string { return typeof value === "string" ? value : ""; }

export function eventAgentName(event: ChatV2AgentRunEvent): string {
  const payload = event.payload ?? {};
  const backend = text(payload.backend ?? payload.agent_backend ?? payload.worker_backend).toLowerCase();
  const name = text(payload.worker_name ?? payload.agent_name);
  if (name.trim()) return name;
  if (backend.includes("claude")) return "Claude Code";
  if (backend.includes("codex")) return "Codex";
  return "DAN";
}

/** Read only emitted reasoning/tool data; never manufacture hidden thinking. */
export function eventDisclosure(event: ChatV2AgentRunEvent) {
  const payload = event.payload ?? {};
  const nativeEvent = record(payload.codex_event ?? payload.claude_event);
  const item = record(nativeEvent.item ?? nativeEvent.content_block ?? payload.item);
  const kind = text(item.type || payload.kind || event.source_event_type || event.type);
  const thinking = /reasoning|thinking/i.test(kind);
  const body = text(item.text || item.thinking || item.aggregated_output || payload.output || payload.text);
  return {
    label: thinking ? "Thinking summary" : event.summary || kind.replaceAll("_", " "),
    body,
    command: text(item.command || payload.command),
    thinking,
  };
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
