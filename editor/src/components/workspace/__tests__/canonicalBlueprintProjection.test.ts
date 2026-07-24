import { describe, expect, it } from "vitest";
import type { ChatV2AgentRunEvent, ChatV2TaskSnapshot } from "../../../lib/chatV2Api";
import { normalizeTaskBlueprintProjection } from "../../../lib/taskBlueprintProjection";
import {
  buildBlueprintNodesForTest,
  liveTaskGraphRevisionsForTest,
} from "../ChunkWorkspaceApp";

function task(overrides: Partial<ChatV2TaskSnapshot>): ChatV2TaskSnapshot {
  const { metadata, ...rest } = overrides;
  return {
    task_id: "task-1",
    thread_id: "thread-1",
    status: "running",
    phase: "execute",
    latest_progress: "Working through the current attempt.",
    latest_artifact_refs: [],
    blocker: "",
    trace_refs: [],
    ...rest,
    metadata: { ...(metadata ?? {}) },
  };
}

const canonicalBlueprint = {
  schema: "dan_task_blueprint_v1",
  blueprint_id: "bp-debug",
  task_id: "task-1",
  revision_id: "bp-debug.r2",
  revision: 2,
  parent_revision_ids: ["bp-debug.r1"],
  family: "debugging",
  contract: {
    goal: "Fix the intermittent save failure",
    non_goals: ["Redesign persistence"],
    permissions: ["Edit workspace files"],
    risk: ["Do not touch production data"],
    budget: ["Two repair attempts"],
    acceptance_criteria: ["Regression test passes"],
  },
  nodes: [
    {
      node_id: "hypothesis",
      kind: "work",
      title: "Test the race-condition hypothesis",
      topology_role: "hypothesis",
      active: false,
    },
    {
      node_id: "evidence",
      kind: "artifact",
      title: "Capture reproduction evidence",
      topology_role: "evidence",
      active: false,
    },
    {
      node_id: "variant",
      kind: "work",
      title: "Compare two candidate fixes",
      topology_role: "variant",
      active: true,
    },
    {
      node_id: "approval",
      kind: "gate",
      title: "Approve the safe patch",
      topology_role: "approval",
      active: false,
    },
    {
      node_id: "repair-loop",
      kind: "loop",
      title: "Revise after failed validation",
      topology_role: "revision_cycle",
      loop_policy: { max_attempts: 2 },
      active: false,
    },
    {
      node_id: "package",
      kind: "composite",
      title: "Package the validated result",
      topology_role: "composite",
      active: false,
    },
  ],
  edges: [
    {
      edge_id: "e1",
      source_node_id: "hypothesis",
      target_node_id: "evidence",
      kind: "produces",
    },
    {
      edge_id: "e2",
      source_node_id: "evidence",
      target_node_id: "variant",
      kind: "dependency",
    },
    {
      edge_id: "e3",
      source_node_id: "variant",
      target_node_id: "approval",
      kind: "validates",
    },
    {
      edge_id: "e4",
      source_node_id: "approval",
      target_node_id: "repair-loop",
      kind: "feedback",
      condition: "approval rejects the patch",
      loop_node_id: "repair-loop",
    },
    {
      edge_id: "e5",
      source_node_id: "approval",
      target_node_id: "package",
      kind: "alternative",
      condition: "approval accepts the patch",
    },
  ],
  derived_state: {
    entry_node_ids: ["hypothesis"],
    active_node_ids: ["variant"],
    terminal_node_ids: ["package"],
    bounded_loop_node_ids: ["repair-loop"],
    required_criterion_ids: ["regression"],
    uncovered_criterion_ids: [],
  },
  update_reason: "Validation introduced a bounded repair route.",
};

const baseArgs = {
  agentEvents: [],
  chunks: [
    {
      id: "message:user-canonical",
      kind: "chat" as const,
      title: "You · Request",
      body: "Fix the intermittent save failure",
      status: "clean" as const,
      meta: "user",
      role: "user" as const,
      runId: "run-2",
    },
  ],
  activeRunId: "run-2",
  queueRows: [],
  activeThreadTitle: "Fix intermittent save failure",
};

describe("canonical Task Blueprint Work projection", () => {
  it("reads canonical blueprint and attempt data promoted through task metadata", () => {
    const activeTask = task({
      metadata: {
        active_run_id: "run-2",
        task_blueprint: canonicalBlueprint,
        execution_attempts: [
          {
            schema: "dan_execution_attempt_v1",
            attempt_id: "attempt-1",
            task_id: "task-1",
            blueprint_id: "bp-debug",
            blueprint_revision_id: "bp-debug.r1",
            blueprint_revision: 1,
            status: "failed",
            phase: "validate",
            run_id: "run-1",
            backend: "codex",
          },
          {
            schema: "dan_execution_attempt_v1",
            attempt_id: "attempt-2",
            task_id: "task-1",
            blueprint_id: "bp-debug",
            blueprint_revision_id: "bp-debug.r2",
            blueprint_revision: 2,
            status: "running",
            phase: "execute",
            run_id: "run-2",
            backend: "super_dan",
            policy_snapshot: { models: ["gpt-5.6"], tools: ["file_read", "file_edit"] },
            node_states: {
              hypothesis: "completed",
              evidence: "completed",
              variant: "completed",
              approval: "active",
              "repair-loop": "planned",
              package: "planned",
            },
            results: { workers: ["debugger", "reviewer"] },
          },
        ],
      },
    });

    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      tasks: [activeTask],
      activeRunningTask: activeTask,
    });
    const planning = nodes.find((node) => node.id === "blueprint:planning");

    expect(planning?.graphContext).toMatchObject({
      canonicalBlueprint: true,
      blueprintId: "bp-debug",
      blueprintRevisionId: "bp-debug.r2",
      taskFamily: "debugging",
      contract: { goal: "Fix the intermittent save failure" },
      boundedLoopNodeIds: ["repair-loop"],
    });
    expect(planning?.graphContext?.executionAttempts).toHaveLength(2);
    expect(planning?.graphContext?.executionAttempts?.[1]).toMatchObject({
      attemptId: "attempt-2",
      status: "running",
      backend: "super_dan",
      workers: ["debugger", "reviewer"],
      models: ["gpt-5.6"],
      tools: ["file_read", "file_edit"],
      nodeStates: {
        hypothesis: "completed",
        evidence: "completed",
        variant: "completed",
        approval: "active",
        "repair-loop": "planned",
        package: "planned",
      },
    });

    expect(nodes.find((node) => node.graphTaskId === "hypothesis")?.kind).toBe("hypothesis");
    expect(nodes.find((node) => node.graphTaskId === "evidence")?.kind).toBe("evidence");
    expect(nodes.find((node) => node.graphTaskId === "variant")?.kind).toBe("variant");
    expect(nodes.find((node) => node.graphTaskId === "approval")?.kind).toBe("approval");
    expect(nodes.find((node) => node.graphTaskId === "repair-loop")?.kind).toBe("loop");
    expect(nodes.find((node) => node.graphTaskId === "package")?.kind).toBe("composite");
    expect(nodes.find((node) => node.graphTaskId === "variant")?.status).toBe("done");
    expect(nodes.find((node) => node.graphTaskId === "approval")?.status).toBe("active");
    expect(planning?.graphContext?.activeTaskIds).toEqual(["approval"]);
    expect(planning?.graphContext?.completedTaskIds).toEqual([
      "hypothesis",
      "evidence",
      "variant",
    ]);
    expect(
      normalizeTaskBlueprintProjection(canonicalBlueprint)?.nodes.find(
        (node) => node.id === "variant",
      )?.status,
    ).toBe("active");

    const revisions = liveTaskGraphRevisionsForTest(nodes);
    expect(revisions).toHaveLength(1);
    expect(revisions[0]?.label).toBe("bp-debug.r2");
    expect(revisions[0]?.planEdges.map((edge) => edge.kind)).toEqual([
      "produces",
      "dependency",
      "validates",
      "feedback",
      "alternative",
    ]);
    expect(
      revisions[0]?.branches.flatMap((branch) => branch.nodes).find((node) =>
        node.id.endsWith(":approval"),
      )?.status,
    ).toBe("active");
  });

  it("accepts canonical blueprint and attempt envelopes from live event rows", () => {
    const activeTask = task({ metadata: { active_run_id: "run-2" } });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_progress",
        source_event_type: "live.task_blueprint.updated",
        run_id: "run-2",
        task_id: "task-1",
        payload: {
          data: {
            task_blueprint: canonicalBlueprint,
            execution_attempt: {
              schema: "dan_execution_attempt_v1",
              attempt_id: "attempt-live",
              task_id: "task-1",
              blueprint_id: "bp-debug",
              blueprint_revision_id: "bp-debug.r2",
              blueprint_revision: 2,
              status: "running",
              phase: "execute",
              run_id: "run-2",
              backend: "codex",
            },
          },
        },
      },
    ];

    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      tasks: [activeTask],
      activeRunningTask: activeTask,
      agentEvents: events,
    });
    const context = nodes.find((node) => node.id === "blueprint:planning")?.graphContext;

    expect(context?.canonicalBlueprint).toBe(true);
    expect(context?.blueprintRevisionId).toBe("bp-debug.r2");
    expect(context?.executionAttempts).toEqual([
      expect.objectContaining({ attemptId: "attempt-live", backend: "codex", status: "running" }),
    ]);
  });

  it("continues to project legacy task_graph_state events", () => {
    const activeTask = task({ metadata: { active_run_id: "legacy-run" } });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_progress",
        source_event_type: "live.task_graph.updated",
        run_id: "legacy-run",
        task_id: "task-1",
        payload: {
          task_graph_state: {
            schema: "super_dan_task_graph_v1",
            revision: 8,
            version_id: "legacy.r8",
            source: "planner",
            update_scope: "plan_execution",
            tasks: [
              {
                task_id: "legacy-node",
                goal: "Keep the legacy route visible",
                state: "active",
                depends_on: [],
              },
            ],
            active_task_ids: ["legacy-node"],
          },
        },
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "legacy-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
      chunks: baseArgs.chunks.map((chunk) => ({ ...chunk, runId: "legacy-run" })),
    });
    const planning = nodes.find((node) => node.id === "blueprint:planning");

    expect(planning?.graphContext?.canonicalBlueprint).not.toBe(true);
    expect(planning?.graphContext?.graphVersionId).toBe("legacy.r8");
    const legacyGraphNodes = liveTaskGraphRevisionsForTest(nodes).flatMap((revision) =>
      revision.branches.flatMap((branch) => branch.nodes),
    );
    expect(legacyGraphNodes.map((node) => node.id)).toContain("blueprint:task:legacy-node");
    expect(legacyGraphNodes.find((node) => node.id === "blueprint:task:legacy-node")?.status).toBe(
      "active",
    );
  });
});
