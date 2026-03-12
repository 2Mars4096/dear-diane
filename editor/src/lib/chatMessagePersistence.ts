import type { ChatMessage, ToolCallInfo } from "../types/chat";

export function safeTokenUsage(
  tu: unknown,
): { prompt: number; completion: number } | null {
  if (!tu || typeof tu !== "object") return null;
  const raw = tu as Record<string, unknown>;
  const p = typeof raw.prompt === "number" ? raw.prompt : 0;
  const c = typeof raw.completion === "number" ? raw.completion : 0;
  return p + c > 0 ? { prompt: p, completion: c } : null;
}

function normalizeTimestamp(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = new Date(value).getTime();
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return Date.now();
}

function normalizeRunRef(value: unknown): ChatMessage["runRef"] {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const runId = typeof raw.run_id === "string" ? raw.run_id : "";
  const scope = typeof raw.scope === "string" ? raw.scope : "";
  const status = typeof raw.status === "string" ? raw.status : "";
  if (!runId && !scope && !status) return null;
  return {
    runId,
    scope,
    status,
    targetNodeId:
      typeof raw.target_node_id === "string" ? raw.target_node_id : undefined,
    targetSubgraphKey:
      typeof raw.target_subgraph_key === "string"
        ? raw.target_subgraph_key
        : undefined,
  };
}

function normalizeToolCalls(value: unknown): ToolCallInfo[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.map((entry, index) => {
    const tc = entry as Record<string, unknown>;
    const status = tc.status;
    return {
      id:
        typeof tc.id === "string" && tc.id.trim()
          ? tc.id
          : `tool-call-${index}`,
      toolName: typeof tc.tool_name === "string" ? tc.tool_name : "",
      argsPreview: typeof tc.args_preview === "string" ? tc.args_preview : "",
      status:
        status === "running" || status === "success" || status === "error"
          ? status
          : "success",
      outputPreview:
        typeof tc.output_preview === "string" ? tc.output_preview : undefined,
      durationMs:
        typeof tc.duration_ms === "number" ? tc.duration_ms : undefined,
    };
  });
}

export function toBackendMessage(m: ChatMessage): Record<string, unknown> {
  return {
    id: m.id,
    role: m.role,
    content: m.content,
    timestamp: new Date(m.timestamp).toISOString(),
    token_usage: m.tokenUsage
      ? { prompt: m.tokenUsage.prompt, completion: m.tokenUsage.completion }
      : null,
    estimated_cost: m.estimatedCost ?? null,
    mutation_plan: m.mutationPlan ?? null,
    dry_run_result: m.dryRunResult ?? null,
    mutation_id: m.mutationId ?? null,
    mutation_status: m.mutationStatus ?? null,
    run_ref: m.runRef
      ? {
          run_id: m.runRef.runId,
          scope: m.runRef.scope,
          status: m.runRef.status,
          target_node_id: m.runRef.targetNodeId ?? null,
          target_subgraph_key: m.runRef.targetSubgraphKey ?? null,
        }
      : null,
    mentions: m.mentions ?? [],
    tool_calls:
      m.toolCalls?.map((tc) => ({
        id: tc.id,
        tool_name: tc.toolName,
        args_preview: tc.argsPreview,
        status: tc.status,
        output_preview: tc.outputPreview ?? null,
        duration_ms: tc.durationMs ?? null,
      })) ?? [],
    run_events: m.runEvents ?? [],
    attachments: m.attachments ?? [],
  };
}

export function fromBackendMessage(m: Record<string, unknown>): ChatMessage {
  return {
    id: m.id as string,
    role: m.role as ChatMessage["role"],
    content: m.content as string,
    timestamp: normalizeTimestamp(m.timestamp),
    tokenUsage: safeTokenUsage(m.token_usage),
    estimatedCost:
      typeof m.estimated_cost === "number" ? m.estimated_cost : null,
    mutationPlan: m.mutation_plan ?? null,
    dryRunResult: (m.dry_run_result as Record<string, unknown>) ?? null,
    mutationId: (m.mutation_id as string) ?? null,
    mutationStatus:
      (m.mutation_status as ChatMessage["mutationStatus"]) ?? null,
    runRef: normalizeRunRef(m.run_ref),
    mentions: (m.mentions as ChatMessage["mentions"]) ?? [],
    toolCalls: normalizeToolCalls(m.tool_calls),
    runEvents: (m.run_events as ChatMessage["runEvents"]) ?? undefined,
    attachments: (m.attachments as ChatMessage["attachments"]) ?? undefined,
  };
}
