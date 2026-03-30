import type { RunInfo } from "./api";
import type { RunEventPayload } from "../types/chat";

const CHAT_EVENT_TYPES = new Set([
  "run_started",
  "run_completed",
  "run_failed",
  "run_cancelled",
  "node_started",
  "node_completed",
  "node_failed",
  "node_output",
  "tool_call_started",
  "tool_call_result",
  "human_input_needed",
]);

type TerminalRunStatus = "completed" | "failed" | "cancelled";

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function extractRunError(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  if (!value || typeof value !== "object") {
    return null;
  }
  for (const nested of Object.values(value as Record<string, unknown>)) {
    if (typeof nested === "string" && nested.trim()) {
      return nested.trim();
    }
  }
  return null;
}

function normalizeTerminalRunStatus(status: string): TerminalRunStatus | null {
  if (status === "completed" || status === "failed" || status === "cancelled") {
    return status;
  }
  return null;
}

export function mapRawRunEventToChatPayload(
  event: Record<string, unknown>,
  scope: string,
  target?: string | null,
): RunEventPayload | null {
  const eventType = asString(event.event_type) ?? asString(event.type) ?? "";
  if (!CHAT_EVENT_TYPES.has(eventType)) return null;

  const runId = asString(event.run_id) ?? "";
  const nodeId = asString(event.node_id);
  const data = asRecord(event.data);
  const error =
    extractRunError(event.error) ??
    extractRunError(data.error) ??
    extractRunError(data.errors) ??
    "unknown error";

  let summary: string;
  switch (eventType) {
    case "run_started": {
      const scopeLabel = scope !== "full" ? ` (${scope})` : "";
      const targetLabel = target ? ` on ${target}` : "";
      summary = `Run started${scopeLabel}${targetLabel}`;
      break;
    }
    case "run_completed":
      summary = "Run completed successfully";
      break;
    case "run_failed":
      summary = `Run failed: ${error}`;
      break;
    case "run_cancelled":
      summary = `Run cancelled: ${asString(data.reason) ?? "cancelled"}`;
      break;
    case "node_started":
      summary = `Node '${nodeId ?? "unknown"}' started`;
      break;
    case "node_completed":
      summary = `Node '${nodeId ?? "unknown"}' completed`;
      break;
    case "node_failed":
      summary = `Node '${nodeId ?? "unknown"}' failed: ${error}`;
      break;
    case "node_output":
      summary = `Node '${nodeId ?? "unknown"}' produced output`;
      break;
    case "tool_call_started": {
      const toolName = asString(data.tool_id) ?? asString(data.tool_name) ?? "unknown";
      summary = `Tool '${toolName}' called on node '${nodeId ?? "unknown"}'`;
      break;
    }
    case "tool_call_result": {
      const toolName = asString(data.tool_id) ?? asString(data.tool_name) ?? "unknown";
      summary = `Tool '${toolName}' completed on node '${nodeId ?? "unknown"}'`;
      break;
    }
    case "human_input_needed":
      summary = `Waiting for input: ${asString(data.prompt) ?? "Input required"}`;
      break;
    default:
      return null;
  }

  return {
    type: "run_event",
    event_type: eventType,
    node_id: nodeId ?? undefined,
    summary,
    detail: {
      run_id: runId,
      scope,
      target: target ?? null,
      error: eventType === "run_failed" || eventType === "node_failed" ? error : null,
      data,
    },
  };
}

export function mergeRunEventPayloads(
  existing: RunEventPayload[] | null | undefined,
  incoming: RunEventPayload[],
): RunEventPayload[] {
  const merged = [...(existing ?? [])];
  const seen = new Set(
    merged.map((event) =>
      JSON.stringify([
        event.event_type,
        event.node_id ?? null,
        event.summary,
        event.detail ?? null,
      ]),
    ),
  );
  for (const event of incoming) {
    const key = JSON.stringify([
      event.event_type,
      event.node_id ?? null,
      event.summary,
      event.detail ?? null,
    ]);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(event);
  }
  return merged;
}

function buildSyntheticRunEventsFromRunInfo(
  runInfo: RunInfo,
  scope: string,
  target?: string | null,
): RunEventPayload[] {
  const status = normalizeTerminalRunStatus(runInfo.status);
  if (!status) return [];

  const events: RunEventPayload[] = [];
  if (status === "failed") {
    for (const [nodeId, error] of Object.entries(runInfo.errors ?? {})) {
      if (!error) continue;
      const syntheticNodeId = nodeId.startsWith("__") ? undefined : nodeId;
      events.push({
        type: "run_event",
        event_type: "node_failed",
        node_id: syntheticNodeId,
        summary: syntheticNodeId
          ? `Node '${syntheticNodeId}' failed: ${error}`
          : `Run failed: ${error}`,
        detail: {
          run_id: runInfo.run_id,
          scope,
          target: target ?? null,
          data: { error },
        },
      });
    }
  }

  const firstError = Object.values(runInfo.errors ?? {}).find(
    (value): value is string => typeof value === "string" && value.trim().length > 0,
  );
  const terminalSummary =
    status === "completed"
      ? "Run completed successfully"
      : status === "cancelled"
        ? "Run cancelled"
        : `Run failed: ${firstError ?? "unknown error"}`;

  events.push({
    type: "run_event",
    event_type:
      status === "completed"
        ? "run_completed"
        : status === "cancelled"
          ? "run_cancelled"
          : "run_failed",
    summary: terminalSummary,
    detail: {
      run_id: runInfo.run_id,
      scope,
      target: target ?? null,
      data: status === "failed" && firstError ? { error: firstError } : {},
    },
  });

  return events;
}

export function recoverTerminalRunStateFromSnapshot(args: {
  runInfo: RunInfo;
  rawEvents?: Array<Record<string, unknown>>;
  scope: string;
  target?: string | null;
}): { status: TerminalRunStatus; runEvents: RunEventPayload[] } | null {
  const status = normalizeTerminalRunStatus(args.runInfo.status);
  if (!status) return null;

  const mappedEvents = (args.rawEvents ?? [])
    .map((event) => mapRawRunEventToChatPayload(event, args.scope, args.target))
    .filter((event): event is RunEventPayload => event !== null);
  const syntheticEvents = buildSyntheticRunEventsFromRunInfo(
    args.runInfo,
    args.scope,
    args.target,
  );

  return {
    status,
    runEvents: mergeRunEventPayloads(mappedEvents, syntheticEvents),
  };
}
