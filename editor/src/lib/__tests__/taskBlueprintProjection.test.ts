import { describe, expect, it } from "vitest";
import {
  normalizeExecutionAttemptProjections,
  normalizeTaskBlueprintProjection,
  taskFamilyPresentation,
  type TaskFamily,
} from "../taskBlueprintProjection";

function blueprint(family: TaskFamily = "debugging") {
  return {
    schema: "dan_task_blueprint_v1",
    blueprint_id: "bp-1",
    task_id: "task-1",
    revision_id: "bp-1.r3",
    revision: 3,
    parent_revision_ids: ["bp-1.r2"],
    family,
    contract: {
      goal: "Find and fix the intermittent failure",
      non_goals: ["Rewrite the service"],
      constraints: ["Keep the public API stable"],
      permissions: ["Read and edit the workspace"],
      risk: {
        level: "high",
        hazards: ["Production mutation"],
        mitigations: ["Use isolated fixtures"],
        prohibited_actions: ["Write production data"],
      },
      budget: { max_wall_seconds: 120, max_cost_usd: 3, max_tokens: 8_000 },
      acceptance_criteria: [
        {
          criterion_id: "criterion-reproduce",
          description: "Reproduction passes",
          required: true,
          evidence_required: ["test log"],
        },
        {
          criterion_id: "criterion-regression",
          description: "Regression test passes",
          required: true,
          approval_required: true,
        },
      ],
    },
    nodes: [
      {
        node_id: "probe",
        kind: "work",
        title: "Probe the failing path",
        topology_role: "hypothesis",
        capability_requirements: ["code inspection", "test execution"],
        criterion_ids: ["criterion-reproduce"],
        artifact_refs: ["artifacts/reproduction.log"],
        active: false,
      },
      {
        node_id: "repair-loop",
        kind: "loop",
        title: "Revise the candidate fix",
        topology_role: "revision_cycle",
        loop_policy: { max_attempts: 3, stop_when: "tests pass" },
        supersedes: ["fix-v1"],
        active: true,
      },
      {
        node_id: "gate",
        kind: "gate",
        title: "Run regression checks",
        topology_role: "validation_gate",
        criterion_ids: ["criterion-regression"],
        active: false,
      },
    ],
    edges: [
      {
        edge_id: "edge-dependency",
        source_node_id: "probe",
        target_node_id: "repair-loop",
        kind: "dependency",
      },
      {
        edge_id: "edge-feedback",
        source_node_id: "gate",
        target_node_id: "repair-loop",
        kind: "feedback",
        condition: "regression fails",
        loop_node_id: "repair-loop",
      },
      {
        edge_id: "edge-validates",
        source_node_id: "repair-loop",
        target_node_id: "gate",
        kind: "validates",
      },
    ],
    derived_state: {
      active_node_ids: ["repair-loop"],
      entry_node_ids: ["probe"],
      terminal_node_ids: ["gate"],
      bounded_loop_node_ids: ["repair-loop"],
      required_criterion_ids: ["criterion-reproduce", "criterion-regression"],
      uncovered_criterion_ids: ["criterion-regression"],
    },
    update_reason: "The failed regression introduced a bounded repair loop.",
  };
}

describe("task blueprint projection", () => {
  it("normalizes the canonical contract, semantic topology, loop state, and typed edges", () => {
    const result = normalizeTaskBlueprintProjection({ data: { task_blueprint: blueprint() } });

    expect(result).toMatchObject({
      schema: "dan_task_blueprint_v1",
      blueprintId: "bp-1",
      taskId: "task-1",
      revisionId: "bp-1.r3",
      revision: 3,
      parentRevisionIds: ["bp-1.r2"],
      family: "debugging",
      activeNodeIds: ["repair-loop"],
      entryNodeIds: ["probe"],
      terminalNodeIds: ["gate"],
      boundedLoopNodeIds: ["repair-loop"],
      uncoveredCriterionIds: ["criterion-regression"],
    });
    expect(result?.contract.acceptanceCriteria).toEqual([
      "Reproduction passes",
      "Regression test passes",
    ]);
    expect(result?.contract.acceptanceCriteria).toHaveLength(2);
    expect(result?.contract.constraints).toEqual(["Keep the public API stable"]);
    expect(result?.contract.risks).toEqual([
      "Level: high",
      "Hazards: Production mutation",
      "Mitigations: Use isolated fixtures",
      "Prohibited: Write production data",
    ]);
    expect(result?.contract.budget).toEqual([
      "Wall time: 120",
      "Cost USD: 3",
      "Tokens: 8000",
    ]);
    expect(result?.nodes.find((node) => node.id === "probe")).toMatchObject({
      status: "ready",
      topologyRole: "hypothesis",
      capabilityRequirements: ["code inspection", "test execution"],
      artifacts: ["artifacts/reproduction.log"],
    });
    expect(result?.nodes.find((node) => node.id === "repair-loop")).toMatchObject({
      status: "active",
      topologyRole: "revision_cycle",
      supersedes: ["fix-v1"],
    });
    expect(result?.nodes.find((node) => node.id === "repair-loop")?.loopPolicy).toEqual([
      "max attempts: 3",
      "stop when: tests pass",
    ]);
    expect(result?.nodes.find((node) => node.id === "repair-loop")?.dependsOn).toEqual([
      "probe",
    ]);
    expect(result?.nodes.find((node) => node.id === "gate")?.dependsOn).toEqual([
      "repair-loop",
    ]);
    expect(result?.edges.map((edge) => edge.kind)).toEqual([
      "dependency",
      "feedback",
      "validates",
    ]);
    expect(result?.edges[1]).toMatchObject({
      from: "gate",
      to: "repair-loop",
      condition: "regression fails",
      loopNodeId: "repair-loop",
    });
  });

  it("keeps all six task-family presentations distinct and useful", () => {
    const families: TaskFamily[] = [
      "direct",
      "debugging",
      "research",
      "design",
      "meeting",
      "manufacturing",
    ];
    const presentations = families.map(taskFamilyPresentation);

    expect(new Set(presentations.map((item) => item.label)).size).toBe(6);
    expect(new Set(presentations.map((item) => item.lens)).size).toBe(6);
    expect(presentations.map((item) => item.lens)).toEqual([
      "Outcome route",
      "Hypotheses & checks",
      "Evidence map",
      "Variants & gates",
      "Decisions & owners",
      "Process & approvals",
    ]);
    for (const family of families) {
      expect(normalizeTaskBlueprintProjection(blueprint(family))?.family).toBe(family);
    }
  });

  it("normalizes and preserves multiple execution attempts with nested resources", () => {
    const attempts = normalizeExecutionAttemptProjections({
      execution_attempts: [
        {
          schema: "dan_execution_attempt_v1",
          attempt_id: "attempt-1",
          task_id: "task-1",
          blueprint_id: "bp-1",
          blueprint_revision_id: "bp-1.r2",
          blueprint_revision: 2,
          status: "failed",
          phase: "validation",
          run_id: "run-1",
          backend: "codex",
          created_at: "2026-07-21T01:00:00Z",
          started_at: "2026-07-21T01:00:01Z",
          finished_at: "2026-07-21T01:03:00Z",
          policy_snapshot: { models: ["gpt-5"], retry_limit: 2 },
          node_states: { probe: "completed", gate: "active" },
          results: { tools: ["workspace_check"], workers: ["validator"] },
        },
        {
          schema: "dan_execution_attempt_v1",
          attempt_id: "attempt-2",
          task_id: "task-1",
          blueprint_id: "bp-1",
          blueprint_revision_id: "bp-1.r3",
          blueprint_revision: 3,
          status: "running",
          phase: "execute",
          run_id: "run-2",
          backend: "super_dan",
          policy_snapshot: { model: "gpt-5.6", tools: ["file_read", "file_edit"] },
          results: { workers: ["debugger", "reviewer"] },
        },
      ],
    });

    expect(attempts).toHaveLength(2);
    expect(attempts[0]).toMatchObject({
      attemptId: "attempt-1",
      blueprintRevisionId: "bp-1.r2",
      blueprintRevision: 2,
      finishedAt: "2026-07-21T01:03:00Z",
      workers: ["validator"],
      models: ["gpt-5"],
      tools: ["workspace_check"],
      maxRetries: 2,
      nodeStates: { probe: "completed", gate: "active" },
    });
    expect(attempts[1]).toMatchObject({
      attemptId: "attempt-2",
      status: "running",
      workers: ["debugger", "reviewer"],
      models: ["gpt-5.6"],
      tools: ["file_read", "file_edit"],
    });
  });
});
