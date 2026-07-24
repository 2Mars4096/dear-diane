export type TaskFamily =
  | "direct"
  | "debugging"
  | "research"
  | "design"
  | "meeting"
  | "manufacturing"
  | "general";

export interface TaskBlueprintContractProjection {
  goal: string;
  nonGoals: string[];
  constraints: string[];
  permissions: string[];
  risks: string[];
  budget: string[];
  acceptanceCriteria: string[];
}

export interface TaskBlueprintNodeProjection {
  id: string;
  parentId: string;
  branchId: string;
  kind: string;
  title: string;
  description: string;
  dependsOn: string[];
  artifacts: string[];
  validation: string[];
  status: string;
  parallelSafe: boolean;
  topologyRole: string;
  capabilityRequirements: string[];
  criterionIds: string[];
  loopPolicy: string[];
  supersedes: string[];
  supersededBy: string[];
}

export interface TaskBlueprintEdgeProjection {
  id: string;
  from: string;
  to: string;
  kind: string;
  condition: string;
  loopNodeId: string;
}

export interface TaskBlueprintProjection {
  schema: "dan_task_blueprint_v1";
  blueprintId: string;
  taskId: string;
  revisionId: string;
  revision: number | null;
  parentRevisionIds: string[];
  family: TaskFamily;
  contract: TaskBlueprintContractProjection;
  nodes: TaskBlueprintNodeProjection[];
  edges: TaskBlueprintEdgeProjection[];
  readyNodeIds: string[];
  deferredNodeIds: string[];
  activeNodeIds: string[];
  completedNodeIds: string[];
  blockedNodeIds: string[];
  entryNodeIds: string[];
  terminalNodeIds: string[];
  boundedLoopNodeIds: string[];
  requiredCriterionIds: string[];
  uncoveredCriterionIds: string[];
  updateReason: string;
}

export interface ExecutionAttemptProjection {
  attemptId: string;
  blueprintRevisionId: string;
  status: string;
  phase: string;
  runId: string;
  backend: string;
  taskId: string;
  blueprintId: string;
  blueprintRevision: number | null;
  createdAt: string;
  startedAt: string;
  finishedAt: string;
  workers: string[];
  models: string[];
  tools: string[];
  retryCount: number | null;
  maxRetries: number | null;
  schedule: string;
  nodeStates: Record<string, string>;
}

export interface TaskFamilyPresentation {
  label: string;
  lens: string;
  blueprintHint: string;
  nodeNoun: string;
}

const FAMILY_PRESENTATION: Record<TaskFamily, TaskFamilyPresentation> = {
  direct: {
    label: "Direct action",
    lens: "Outcome route",
    blueprintHint: "A short route from request to verified outcome.",
    nodeNoun: "step",
  },
  debugging: {
    label: "Debugging",
    lens: "Hypotheses & checks",
    blueprintHint: "Competing causes narrow through probes, fixes, and regression checks.",
    nodeNoun: "probe",
  },
  research: {
    label: "Research",
    lens: "Evidence map",
    blueprintHint: "Questions branch into evidence, claims, synthesis, and coverage gates.",
    nodeNoun: "inquiry",
  },
  design: {
    label: "Design",
    lens: "Variants & gates",
    blueprintHint: "Constraints shape parallel variants, critique, selection, and refinement.",
    nodeNoun: "move",
  },
  meeting: {
    label: "Meeting",
    lens: "Decisions & owners",
    blueprintHint: "Proposals become confirmed decisions, owned actions, and reconciliation gates.",
    nodeNoun: "decision",
  },
  manufacturing: {
    label: "Manufacturing",
    lens: "Process & approvals",
    blueprintHint: "Specifications flow through feasibility, compliance, sourcing, and approval gates.",
    nodeNoun: "operation",
  },
  general: {
    label: "Adaptive task",
    lens: "Task topology",
    blueprintHint: "Work can branch, converge, loop, or revise as evidence changes.",
    nodeNoun: "step",
  },
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asText(value: unknown) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function unique(items: string[]) {
  return [...new Set(items.map((item) => item.trim()).filter(Boolean))];
}

function recordLines(record: Record<string, unknown>) {
  return Object.entries(record).flatMap(([key, value]) => {
    const text = asText(value);
    if (text) return [`${key.replace(/_/g, " ")}: ${text}`];
    if (Array.isArray(value)) {
      const items = value.flatMap(stringItems);
      return items.length ? [`${key.replace(/_/g, " ")}: ${items.join(", ")}`] : [];
    }
    return [];
  });
}

function stringItems(value: unknown): string[] {
  if (Array.isArray(value)) return unique(value.flatMap(stringItems));
  const text = asText(value);
  if (text) return [text];
  const record = asRecord(value);
  if (!record) return [];
  const label =
    asText(record.label) ||
    asText(record.name) ||
    asText(record.title) ||
    asText(record.id);
  return label ? [label] : recordLines(record);
}

function numberValue(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function stringRecord(value: unknown) {
  const record = asRecord(value);
  if (!record) return {};
  return Object.fromEntries(
    Object.entries(record).flatMap(([key, item]) => {
      const normalizedKey = key.trim();
      const normalizedValue = asText(item).toLowerCase();
      return normalizedKey && normalizedValue ? [[normalizedKey, normalizedValue]] : [];
    }),
  );
}

function normalizeFamily(value: unknown): TaskFamily {
  const family = asText(value).toLowerCase().replace(/[\s_-]+/g, "_");
  if (["direct", "simple", "simple_action", "action"].includes(family)) return "direct";
  if (["debug", "debugging", "repair"].includes(family)) return "debugging";
  if (["research", "investigation", "analysis"].includes(family)) return "research";
  if (["design", "creative"].includes(family)) return "design";
  if (["meeting", "meeting_mode"].includes(family)) return "meeting";
  if (["manufacturing", "production", "fabrication"].includes(family)) return "manufacturing";
  return "general";
}

function acceptanceCriterionItems(value: unknown) {
  if (!Array.isArray(value)) return stringItems(value);
  return unique(
    value.flatMap((item) => {
      const record = asRecord(item);
      if (!record) return stringItems(item);
      const description = asText(record.description);
      return description ? [description] : stringItems(item);
    }),
  );
}

function riskItems(value: unknown) {
  const record = asRecord(value);
  if (!record) return stringItems(value);
  const entries = [
    asText(record.level) ? `Level: ${asText(record.level)}` : "",
    stringItems(record.hazards).length
      ? `Hazards: ${stringItems(record.hazards).join(", ")}`
      : "",
    stringItems(record.mitigations).length
      ? `Mitigations: ${stringItems(record.mitigations).join(", ")}`
      : "",
    stringItems(record.prohibited_actions).length
      ? `Prohibited: ${stringItems(record.prohibited_actions).join(", ")}`
      : "",
  ];
  return unique(entries.some(Boolean) ? entries : recordLines(record));
}

const BUDGET_LABELS: Record<string, string> = {
  max_wall_seconds: "Wall time",
  max_work_seconds: "Work time",
  max_cost_usd: "Cost USD",
  max_tokens: "Tokens",
  max_tool_calls: "Tool calls",
  max_parallel_workers: "Parallel workers",
  max_revisions: "Revisions",
  max_auto_fix_rounds: "Auto-fix rounds",
  max_validation_cycles: "Validation cycles",
};

function budgetItems(value: unknown) {
  const record = asRecord(value);
  if (!record) return stringItems(value);
  return unique(
    Object.entries(record).flatMap(([key, item]) => {
      const text = asText(item);
      if (!text) return [];
      return [`${BUDGET_LABELS[key] || key.replace(/_/g, " ")}: ${text}`];
    }),
  );
}

function normalizeNodeStatus(value: unknown) {
  const status = asText(value).toLowerCase();
  if (["done", "complete", "completed", "satisfied"].includes(status)) return "done";
  if (["active", "running", "executing", "validating", "repairing"].includes(status)) return "active";
  if (["ready", "runnable"].includes(status)) return "ready";
  if (["blocked", "failed", "cancelled", "canceled"].includes(status)) return "blocked";
  if (["queued", "pending"].includes(status)) return "queued";
  return status || "planned";
}

function normalizeContract(value: unknown): TaskBlueprintContractProjection {
  const contract = asRecord(value) ?? {};
  return {
    goal: asText(contract.goal) || asText(contract.objective),
    nonGoals: unique([
      ...stringItems(contract.non_goals),
      ...stringItems(contract.out_of_scope),
    ]),
    constraints: stringItems(contract.constraints),
    permissions: unique([
      ...stringItems(contract.permissions),
      ...stringItems(contract.authority),
    ]),
    risks: unique([...riskItems(contract.risk), ...riskItems(contract.risks)]),
    budget: unique([...budgetItems(contract.budget), ...budgetItems(contract.budgets)]),
    acceptanceCriteria: unique([
      ...acceptanceCriterionItems(contract.acceptance_criteria),
      ...acceptanceCriterionItems(contract.completion_criteria),
      ...acceptanceCriterionItems(contract.definition_of_done),
    ]),
  };
}

function nodeKind(record: Record<string, unknown>) {
  return (
    asText(record.kind) ||
    asText(record.node_type) ||
    asText(record.type) ||
    asText(record.task_kind) ||
    "work"
  )
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
}

function normalizeNodes(value: unknown): TaskBlueprintNodeProjection[] {
  const items = Array.isArray(value) ? value : [];
  const nodes: TaskBlueprintNodeProjection[] = [];
  const seen = new Set<string>();
  const visit = (item: unknown, inheritedParent = "", inheritedBranch = "") => {
    const record = asRecord(item);
    if (!record) return;
    const id = asText(record.node_id) || asText(record.task_id) || asText(record.id);
    if (!id || seen.has(id)) return;
    seen.add(id);
    const kind = nodeKind(record);
    const title =
      asText(record.title) ||
      asText(record.goal) ||
      asText(record.summary) ||
      `${kind.replace(/_/g, " ")} ${nodes.length + 1}`;
    const node: TaskBlueprintNodeProjection = {
      id,
      parentId: asText(record.parent_id) || asText(record.parent) || inheritedParent,
      branchId: asText(record.branch_id) || asText(record.branch) || inheritedBranch,
      kind,
      title,
      description: asText(record.description) || asText(record.detail) || asText(record.goal),
      dependsOn: unique([
        ...stringItems(record.depends_on),
        ...stringItems(record.dependencies),
      ]),
      artifacts: unique([
        ...stringItems(record.artifacts),
        ...stringItems(record.artifact_ids),
        ...stringItems(record.artifact_refs),
        ...stringItems(record.deliverables),
        ...stringItems(record.outputs),
      ]),
      validation: unique([
        ...stringItems(record.validation),
        ...stringItems(record.acceptance_criteria),
        ...stringItems(record.gates),
        ...stringItems(record.checks),
        ...stringItems(record.criterion_ids),
      ]),
      status: normalizeNodeStatus(record.status ?? record.state),
      parallelSafe: typeof record.parallel_safe === "boolean" ? record.parallel_safe : true,
      topologyRole: asText(record.topology_role),
      capabilityRequirements: stringItems(record.capability_requirements),
      criterionIds: stringItems(record.criterion_ids),
      loopPolicy: stringItems(record.loop_policy),
      supersedes: stringItems(record.supersedes),
      supersededBy: stringItems(record.superseded_by),
    };
    nodes.push(node);
    const children = record.nodes ?? record.children ?? record.tasks ?? record.subtasks;
    if (Array.isArray(children)) {
      for (const child of children) visit(child, id, node.branchId);
    }
  };
  for (const item of items) visit(item);
  return nodes;
}

function normalizeEdges(value: unknown, nodes: TaskBlueprintNodeProjection[]) {
  if (!Array.isArray(value)) return [];
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges: TaskBlueprintEdgeProjection[] = [];
  for (const item of value) {
    const edge = asRecord(item);
    if (!edge) continue;
    const from =
      asText(edge.source_node_id) ||
      asText(edge.from) ||
      asText(edge.source) ||
      asText(edge.predecessor_id);
    const to =
      asText(edge.target_node_id) ||
      asText(edge.to) ||
      asText(edge.target) ||
      asText(edge.successor_id);
    if (!from || !to || !nodeIds.has(from) || !nodeIds.has(to)) continue;
    const kind = asText(edge.kind).toLowerCase() || "dependency";
    edges.push({
      id: asText(edge.edge_id) || asText(edge.id) || `${from}:${kind}:${to}`,
      from,
      to,
      kind,
      condition: asText(edge.condition),
      loopNodeId: asText(edge.loop_node_id),
    });
    if (["dependency", "validates"].includes(kind)) {
      const target = nodes.find((node) => node.id === to);
      if (target) target.dependsOn = unique([...target.dependsOn, from]);
    }
  }
  return edges;
}

function stateIds(state: Record<string, unknown>, ...keys: string[]) {
  return unique(keys.flatMap((key) => stringItems(state[key])));
}

function applyDerivedState(nodes: TaskBlueprintNodeProjection[], value: unknown) {
  const state = asRecord(value) ?? {};
  const ready = stateIds(state, "ready_node_ids", "ready_task_ids", "ready");
  const deferred = stateIds(state, "deferred_node_ids", "deferred_task_ids", "future_node_ids");
  const active = stateIds(state, "active_node_ids", "active_task_ids", "active");
  const completed = stateIds(state, "completed_node_ids", "completed_task_ids", "completed");
  const blocked = stateIds(state, "blocked_node_ids", "blocked_task_ids", "blocked");
  const entry = stateIds(state, "entry_node_ids");
  const terminal = stateIds(state, "terminal_node_ids");
  const boundedLoops = stateIds(state, "bounded_loop_node_ids");
  const requiredCriteria = stateIds(state, "required_criterion_ids");
  const uncoveredCriteria = stateIds(state, "uncovered_criterion_ids");
  const statusById = new Map<string, string>();
  for (const id of deferred) statusById.set(id, "planned");
  for (const id of entry) statusById.set(id, "ready");
  for (const id of ready) statusById.set(id, "ready");
  for (const id of active) statusById.set(id, "active");
  for (const id of completed) statusById.set(id, "done");
  for (const id of blocked) statusById.set(id, "blocked");
  const explicitStates = asRecord(state.node_states) ?? asRecord(state.task_states) ?? {};
  for (const [id, rawStatus] of Object.entries(explicitStates)) {
    statusById.set(id, normalizeNodeStatus(rawStatus));
  }
  for (const node of nodes) {
    const derived = statusById.get(node.id);
    if (derived) node.status = derived;
  }
  return {
    ready: unique([...ready, ...entry]),
    deferred,
    active,
    completed,
    blocked,
    entry,
    terminal,
    boundedLoops,
    requiredCriteria,
    uncoveredCriteria,
  };
}

function isBlueprintRecord(record: Record<string, unknown>) {
  return asText(record.schema) === "dan_task_blueprint_v1";
}

function directBlueprintCandidate(record: Record<string, unknown>) {
  if (isBlueprintRecord(record)) return record;
  const nested = asRecord(record.task_blueprint);
  if (nested && isBlueprintRecord(nested)) return nested;
  return null;
}

function collectRecords(value: unknown, maxDepth = 6) {
  const records: Record<string, unknown>[] = [];
  const seen = new Set<unknown>();
  const visit = (current: unknown, depth: number) => {
    if (depth > maxDepth || seen.has(current)) return;
    if (Array.isArray(current)) {
      seen.add(current);
      for (const item of current) visit(item, depth + 1);
      return;
    }
    const record = asRecord(current);
    if (!record) return;
    seen.add(current);
    records.push(record);
    for (const nested of Object.values(record)) {
      if (nested && typeof nested === "object") visit(nested, depth + 1);
    }
  };
  visit(value, 0);
  return records;
}

function normalizeBlueprint(record: Record<string, unknown>): TaskBlueprintProjection {
  const nodes = normalizeNodes(record.nodes ?? record.tasks);
  const edges = normalizeEdges(record.edges, nodes);
  const derived = applyDerivedState(nodes, record.derived_state ?? record.state);
  const contract = normalizeContract(record.contract);
  return {
    schema: "dan_task_blueprint_v1",
    blueprintId: asText(record.blueprint_id) || asText(record.id),
    taskId: asText(record.task_id),
    revisionId: asText(record.revision_id) || asText(record.version_id),
    revision: numberValue(record.revision),
    parentRevisionIds: unique([
      ...stringItems(record.parent_revision_ids),
      ...stringItems(record.parent_revision_id),
    ]),
    family: normalizeFamily(record.family ?? record.task_family),
    contract,
    nodes,
    edges,
    readyNodeIds: derived.ready,
    deferredNodeIds: derived.deferred,
    activeNodeIds: derived.active,
    completedNodeIds: derived.completed,
    blockedNodeIds: derived.blocked,
    entryNodeIds: derived.entry,
    terminalNodeIds: derived.terminal,
    boundedLoopNodeIds: derived.boundedLoops,
    requiredCriterionIds: derived.requiredCriteria,
    uncoveredCriterionIds: derived.uncoveredCriteria,
    updateReason: asText(record.update_reason) || asText(record.revision_reason),
  };
}

function looksLikeAttempt(record: Record<string, unknown>) {
  return Boolean(
    asText(record.attempt_id) &&
      (record.blueprint_revision_id !== undefined ||
        record.run_id !== undefined ||
        record.backend !== undefined ||
        record.phase !== undefined),
  );
}

function summaryItems(record: Record<string, unknown>, ...keys: string[]) {
  return unique(keys.flatMap((key) => stringItems(record[key])));
}

function nestedSummaryItems(record: Record<string, unknown>, ...keys: string[]) {
  return unique(
    collectRecords([record.policy_snapshot, record.results], 3).flatMap((nested) =>
      summaryItems(nested, ...keys),
    ),
  );
}

function nestedNumberValue(record: Record<string, unknown>, ...keys: string[]) {
  for (const nested of collectRecords([record.policy_snapshot, record.results], 3)) {
    for (const key of keys) {
      const value = numberValue(nested[key]);
      if (value !== null) return value;
    }
  }
  return null;
}

function nestedTextValue(record: Record<string, unknown>, ...keys: string[]) {
  for (const nested of collectRecords([record.policy_snapshot, record.results], 3)) {
    for (const key of keys) {
      const value = asText(nested[key]);
      if (value) return value;
    }
  }
  return "";
}

function normalizeAttempt(record: Record<string, unknown>): ExecutionAttemptProjection {
  return {
    attemptId: asText(record.attempt_id) || asText(record.id),
    blueprintRevisionId: asText(record.blueprint_revision_id) || asText(record.revision_id),
    status: asText(record.status) || "planned",
    phase: asText(record.phase),
    runId: asText(record.run_id),
    backend: asText(record.backend),
    taskId: asText(record.task_id),
    blueprintId: asText(record.blueprint_id),
    blueprintRevision: numberValue(record.blueprint_revision),
    createdAt: asText(record.created_at),
    startedAt: asText(record.started_at),
    finishedAt: asText(record.finished_at) || asText(record.completed_at),
    workers: unique([
      ...summaryItems(record, "worker_summary", "workers", "worker_ids", "worker"),
      ...nestedSummaryItems(record, "worker_summary", "workers", "worker_ids", "worker"),
    ]),
    models: unique([
      ...summaryItems(record, "model_summary", "models", "model_ids", "model"),
      ...nestedSummaryItems(record, "model_summary", "models", "model_ids", "model"),
    ]),
    tools: unique([
      ...summaryItems(record, "tool_summary", "tools", "tool_ids", "tool"),
      ...nestedSummaryItems(record, "tool_summary", "tools", "tool_ids", "tool"),
    ]),
    retryCount:
      numberValue(record.retry_count ?? record.retries) ??
      nestedNumberValue(record, "retry_count", "retries"),
    maxRetries:
      numberValue(record.max_retries ?? record.retry_limit) ??
      nestedNumberValue(record, "max_retries", "retry_limit"),
    schedule:
      asText(record.schedule) ||
      asText(record.scheduling) ||
      asText(record.execution_mode) ||
      asText(record.parallelism) ||
      nestedTextValue(record, "schedule", "scheduling", "execution_mode", "parallelism"),
    nodeStates: stringRecord(record.node_states),
  };
}

export function taskFamilyPresentation(family: TaskFamily) {
  return FAMILY_PRESENTATION[family] ?? FAMILY_PRESENTATION.general;
}

export function normalizeTaskBlueprintProjection(value: unknown): TaskBlueprintProjection | null {
  const candidates = collectRecords(value)
    .map(directBlueprintCandidate)
    .filter((record): record is Record<string, unknown> => Boolean(record));
  if (candidates.length === 0) return null;
  return normalizeBlueprint(candidates[candidates.length - 1]!);
}

export function normalizeExecutionAttemptProjections(value: unknown) {
  const attemptsByKey = new Map<string, ExecutionAttemptProjection>();
  for (const record of collectRecords(value)) {
    const direct = asRecord(record.execution_attempt);
    const candidates = [
      ...(direct ? [direct] : []),
      ...(Array.isArray(record.execution_attempts)
        ? record.execution_attempts.flatMap((item) => (asRecord(item) ? [asRecord(item)!] : []))
        : []),
      ...(looksLikeAttempt(record) ? [record] : []),
    ];
    for (const candidate of candidates) {
      if (!looksLikeAttempt(candidate)) continue;
      const attempt = normalizeAttempt(candidate);
      const key = `${attempt.attemptId}:${attempt.blueprintRevisionId}:${attempt.runId}`;
      attemptsByKey.set(key, attempt);
    }
  }
  return [...attemptsByKey.values()];
}
