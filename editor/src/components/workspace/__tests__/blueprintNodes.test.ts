import { describe, expect, it, vi } from "vitest";
import {
  activeThreadArchivedSummaryForTest,
  buildSessionGroupsForTest,
  blueprintTimelineItemsForTest,
  blueprintLiveStatusForTest,
  buildBlueprintNodesForTest,
  conversationUserChunksForTest,
  liveTaskGraphRevisionsForTest,
  liveTaskTreeForTest,
  noteRailViewForFacetForTest,
  normalizeStructuredMarkdownForTest,
  queueRowsFromTasksForTest,
  restorableThreadTargetForTest,
  sessionCardDisplayForTest,
  sessionHasNewReadyResponseForTest,
  sessionProgressTaskForTest,
  sessionReadyResponseAtForTest,
  selectActiveRunningTaskForTest,
  sessionStatusTasksForTest,
  shouldAutoRestoreSessionForTest,
  taskGroupElapsedCounterForTest,
  workPlanHeaderSubtitleForTest,
  workspaceComposerPlaceholderForTest,
  workspaceAgentExecutePayloadForTest,
  workspaceAgentOptionsForTest,
  workspaceIdForTasksForTest,
} from "../ChunkWorkspaceApp";
import type {
  ChatV2AgentRunEvent,
  ChatV2TaskSnapshot,
  ChatV2ThreadSummary,
} from "../../../lib/chatV2Api";

const baseArgs = {
  tasks: [],
  agentEvents: [],
  chunks: [],
  activeRunId: "",
  activeRunningTask: null,
  queueRows: [],
  activeThreadTitle: "",
};

function findTreeParentId(
  nodes: ReturnType<typeof liveTaskTreeForTest>,
  targetId: string,
  parentId: string | null = null,
): string | null {
  for (const node of nodes) {
    if (node.id === targetId) return parentId;
    const found = findTreeParentId(node.children, targetId, node.id);
    if (found !== null) return found;
  }
  return null;
}

function task(overrides: Partial<ChatV2TaskSnapshot>): ChatV2TaskSnapshot {
  const { metadata, ...rest } = overrides;
  return {
    task_id: "task-1",
    thread_id: "thread-1",
    status: "queued",
    phase: "",
    latest_progress: "",
    latest_artifact_refs: [],
    blocker: "",
    trace_refs: [],
    ...rest,
    metadata: {
      ...(metadata ?? {}),
    },
  };
}

function thread(overrides: Partial<ChatV2ThreadSummary>): ChatV2ThreadSummary {
  return {
    id: "thread-1",
    title: "New Super DAN Session",
    workflow_id: "_scratch",
    message_count: 0,
    created_at: "2026-06-25T12:00:00.000Z",
    updated_at: "2026-06-25T12:01:00.000Z",
    mode: "agent",
    ...overrides,
  };
}

describe("workspace blueprint nodes", () => {
  it("keeps Super DAN as the default agent selection", () => {
    const options = workspaceAgentOptionsForTest();
    const payload = workspaceAgentExecutePayloadForTest("super_dan_default");

    expect(options.map((option) => option.id)).toEqual([
      "super_dan_default",
      "super_dan_kimi_k26",
      "codex",
    ]);
    expect(payload.backend).toBe("super_dan");
    expect(payload.profile_policy).toMatchObject({
      backend: "super_dan",
      surface_profile: "super_tui",
    });
    expect(payload.profile_policy).not.toHaveProperty("model");
    expect(payload.metadata).toMatchObject({
      selected_agent: "super_dan_default",
      gui_for: "dan super-tui",
    });
  });

  it("routes the Kimi option through the existing Super DAN backend", () => {
    const payload = workspaceAgentExecutePayloadForTest("super_dan_kimi_k26");

    expect(payload.backend).toBe("super_dan");
    expect(payload.profile_policy).toMatchObject({
      backend: "super_dan",
      model: "kimi-k2.6",
    });
    expect(payload.metadata).toMatchObject({
      selected_agent: "super_dan_kimi_k26",
      selected_model: "kimi-k2.6",
      gui_for: "dan super-tui",
    });
  });

  it("routes the Codex option through the Codex backend without a model override", () => {
    const payload = workspaceAgentExecutePayloadForTest("codex");

    expect(payload.backend).toBe("codex");
    expect(payload.profile_policy).toMatchObject({
      backend: "codex",
      surface_profile: "super_tui",
    });
    expect(payload.profile_policy).not.toHaveProperty("model");
    expect(payload.metadata).toMatchObject({
      selected_agent: "codex",
      selected_backend: "codex",
      gui_for: "codex exec",
    });
  });

  it("keeps active-run composer placeholders short and steerable", () => {
    const longStepTitle =
      "1: Understand the M1.5 milestone scope and current integration state";

    expect(
      workspaceComposerPlaceholderForTest({
        hasActiveRun: true,
        placement: "steer",
        selectedBlueprintTitle: longStepTitle,
      }),
    ).toBe("Type to steer the active run. Leave empty to stop.");

    expect(
      workspaceComposerPlaceholderForTest({
        hasActiveRun: true,
        placement: "queue",
        selectedBlueprintTitle: longStepTitle,
      }),
    ).toBe("Type the next message to run after the current one.");
  });

  it("uses selected context in the composer only when no run is active", () => {
    expect(
      workspaceComposerPlaceholderForTest({
        hasActiveRun: false,
        placement: "steer",
        selectedBlueprintTitle: "Final response",
      }),
    ).toBe("Ask Super DAN about Final response");
  });

  it("keeps Notes taxonomy selections inside their taxonomy rails", () => {
    expect(noteRailViewForFacetForTest("tag:italy")).toBe("tags");
    expect(noteRailViewForFacetForTest("section:blogs")).toBe("sections");
    expect(noteRailViewForFacetForTest("category:travel")).toBe("sections");
    expect(noteRailViewForFacetForTest("all")).toBeNull();
  });

  it("resolves a task back to the selected workspace id for session grouping", () => {
    const workspaceTask = task({
      metadata: {
        workspace_id: "ra-neo",
        workspace_root: "/Users/lizhi/Downloads/local_projects/ra-neo",
      },
    });

    expect(
      workspaceIdForTasksForTest([workspaceTask], [
        { id: "scratch", pinnedPaths: ["/Users/lizhi/Downloads/local_projects/scratch"] },
        { id: "ra-neo", pinnedPaths: ["/Users/lizhi/Downloads/local_projects/ra-neo"] },
      ]),
    ).toBe("ra-neo");
  });

  it("resolves a task back to a workspace from the stored root path", () => {
    const workspaceTask = task({
      metadata: {
        workspace_id: "/Users/lizhi/Downloads/local_projects/ra-neo",
        workspace_root: "/Users/lizhi/Downloads/local_projects/ra-neo",
      },
    });

    expect(
      workspaceIdForTasksForTest([workspaceTask], [
        { id: "scratch", pinnedPaths: ["/Users/lizhi/Downloads/local_projects/scratch"] },
        { id: "ra-neo", pinnedPaths: ["/Users/lizhi/Downloads/local_projects/ra-neo"] },
      ]),
    ).toBe("ra-neo");
  });

  it("prefers the newest task metadata when restoring a mixed-history thread", () => {
    const oldTask = task({
      task_id: "task-old",
      metadata: {
        workspace_id: "scratch",
        run_updated_at: "2026-06-24T10:00:00.000Z",
      },
    });
    const newTask = task({
      task_id: "task-new",
      metadata: {
        workspace_id: "ra-neo",
        run_updated_at: "2026-06-24T11:00:00.000Z",
      },
    });

    expect(
      workspaceIdForTasksForTest([oldTask, newTask], [
        { id: "scratch", pinnedPaths: ["/Users/lizhi/Downloads/local_projects/scratch"] },
        { id: "ra-neo", pinnedPaths: ["/Users/lizhi/Downloads/local_projects/ra-neo"] },
      ]),
    ).toBe("ra-neo");
  });

  it("does not turn a blank new session title into a fake blueprint", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeThreadTitle: "New Super DAN Session",
    });

    expect(nodes).toEqual([]);
  });

  it("keeps recent user chat turns available for conversation boxes", () => {
    const chunks = conversationUserChunksForTest(
      [
        {
          id: "message:user-1",
          kind: "chat",
          title: "You · Request",
          body: "First request",
          status: "clean",
          meta: "user",
          role: "user",
        },
        {
          id: "message:assistant-1",
          kind: "chat",
          title: "DAN · Answer",
          body: "Answer",
          status: "clean",
          meta: "assistant",
          role: "assistant",
        },
        {
          id: "message:user-2",
          kind: "chat",
          title: "You · Request",
          body: "Second request",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      1,
    );

    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.id).toBe("message:user-2");
    expect(chunks[0]?.body).toBe("Second request");
  });

  it("labels blank Super DAN sessions as having no request yet", () => {
    expect(sessionCardDisplayForTest(thread({}), [])).toMatchObject({
      title: "New Super DAN Session",
      detail: expect.stringContaining("No request yet"),
    });
  });

  it("uses recovered Super DAN task history for session card labels", () => {
    const recoveredTask = task({
      thread_id: "thread-1",
      status: "completed",
      metadata: {
        run_updated_at: "2026-06-25T12:05:00.000Z",
        last_surface_turn: {
          text: "help me summarize this project",
        },
      },
    });

    expect(
      sessionCardDisplayForTest(
        thread({
          title: "New Super DAN Session",
          message_count: 0,
        }),
        [recoveredTask],
      ),
    ).toMatchObject({
      title: "help me summarize this project",
      detail: expect.stringContaining("1 run · done"),
    });
  });

  it("shows elapsed work time on session card labels", () => {
    const completed = task({
      thread_id: "thread-1",
      status: "completed",
      metadata: {
        run_created_at: "2026-06-25T12:00:00.000Z",
        run_updated_at: "2026-06-25T12:07:20.000Z",
      },
    });

    expect(sessionCardDisplayForTest(thread({}), [completed]).detail).toContain("total worked 7m");

    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-25T12:03:30.000Z"));
    try {
      const running = task({
        thread_id: "thread-1",
        status: "running",
        metadata: {
          run_created_at: "2026-06-25T12:00:00.000Z",
          run_updated_at: "2026-06-25T12:02:00.000Z",
        },
      });

      expect(sessionCardDisplayForTest(thread({}), [running]).detail).toContain("total working 4m");
    } finally {
      vi.useRealTimers();
    }

    const queued = task({
      thread_id: "thread-1",
      status: "queued",
      metadata: {
        run_created_at: "2026-06-25T12:00:00.000Z",
        run_updated_at: "2026-06-25T12:01:00.000Z",
      },
    });

    expect(sessionCardDisplayForTest(thread({}), [queued]).detail).not.toContain("worked");
  });

  it("formats one aggregate live Work Panel duration counter", () => {
    const completed = task({
      task_id: "done-task",
      status: "completed",
      metadata: {
        active_run_id: "done-run",
        run_created_at: "2026-06-25T12:00:00.000Z",
        run_updated_at: "2026-06-25T12:07:20.000Z",
      },
    });

    expect(taskGroupElapsedCounterForTest([completed])).toEqual({
      value: "7:20",
      active: false,
    });

    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-25T12:03:30.000Z"));
    try {
      const running = task({
        task_id: "running-task",
        status: "running",
        metadata: {
          active_run_id: "active-run",
          run_created_at: "2026-06-25T12:00:00.000Z",
          run_updated_at: "2026-06-25T12:02:00.000Z",
        },
      });

      expect(taskGroupElapsedCounterForTest([completed, running])).toEqual({
        value: "10:50",
        active: true,
      });
    } finally {
      vi.useRealTimers();
    }

    const queued = task({
      task_id: "queued-task",
      status: "queued",
      metadata: {
        active_run_id: "queued-run",
        run_created_at: "2026-06-25T12:00:00.000Z",
        run_updated_at: "2026-06-25T12:01:00.000Z",
      },
    });

    expect(taskGroupElapsedCounterForTest([queued])).toBeNull();
  });

  it("marks a session response as new only until that ready response has been seen", () => {
    const completed = task({
      thread_id: "thread-1",
      status: "completed",
      latest_progress: "Here is the project summary.",
      metadata: {
        run_updated_at: "2026-06-25T12:05:00.000Z",
      },
    });

    expect(sessionReadyResponseAtForTest([completed])).toBe("2026-06-25T12:05:00.000Z");
    expect(sessionHasNewReadyResponseForTest([completed])).toBe(true);
    expect(
      sessionHasNewReadyResponseForTest([completed], "2026-06-25T12:05:00.000Z"),
    ).toBe(false);
    expect(
      sessionHasNewReadyResponseForTest([completed], "2026-06-25T12:06:00.000Z"),
    ).toBe(false);
  });

  it("uses task update timestamps for ready response freshness", () => {
    const completed = task({
      thread_id: "thread-1",
      status: "completed",
      latest_progress: "Here is the project summary.",
      metadata: {
        updated_at: "2026-06-25T12:05:00.000Z",
      },
    });

    expect(sessionReadyResponseAtForTest([completed])).toBe("2026-06-25T12:05:00.000Z");
    expect(sessionHasNewReadyResponseForTest([completed])).toBe(true);
  });

  it("does not use the session response dot for running work or generic completion receipts", () => {
    const running = task({
      thread_id: "thread-1",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: {
        run_updated_at: "2026-06-25T12:04:00.000Z",
      },
    });
    const receiptOnly = task({
      thread_id: "thread-1",
      status: "completed",
      latest_progress: "Super DAN completed.",
      metadata: {
        run_updated_at: "2026-06-25T12:05:00.000Z",
      },
    });

    expect(sessionHasNewReadyResponseForTest([running])).toBe(false);
    expect(sessionHasNewReadyResponseForTest([receiptOnly])).toBe(false);
  });

  it("treats structured backend answers as ready responses", () => {
    const completed = task({
      thread_id: "thread-1",
      status: "completed",
      latest_progress: "Run finished.",
      metadata: {
        run_updated_at: "2026-06-25T12:05:00.000Z",
        backend_result: {
          answer: "This project is a dual-prototype Three.js and Godot game workspace.",
        },
      },
    });

    expect(sessionHasNewReadyResponseForTest([completed])).toBe(true);
  });

  it("separates the raw operator request from the request card summary", () => {
    const rawRequest = "help me review this project?";
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-raw-request",
          kind: "chat",
          title: "You · Request",
          body: rawRequest,
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
    });

    const request = nodes.find((node) => node.kind === "request");
    expect(request).toMatchObject({
      title: "Operator request",
      detail: "Request constraints captured",
      body: "Ready for request understanding.",
      rawRequest,
    });
    expect(request?.previewBody).not.toContain(rawRequest);
  });

  it("uses user-facing work-plan subtitles instead of repeating internal step titles", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "planning-run",
      activeRunningTask: task({
        task_id: "planning-task",
        status: "running",
        metadata: { active_run_id: "planning-run" },
      }),
      tasks: [
        task({
          task_id: "planning-task",
          status: "running",
          metadata: { active_run_id: "planning-run" },
        }),
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.planning.started",
          run_id: "planning-run",
          task_id: "planning-task",
          summary: "Planning next steps.",
        },
      ],
    });

    const planning = nodes.find((node) => node.kind === "plan") ?? null;
    expect(planning?.title).toBe("Blueprint planning");
    expect(planning).toMatchObject({
      status: "active",
      body: "Waiting for the planner to emit a task graph.",
    });
    expect(workPlanHeaderSubtitleForTest(planning)).toBe("Planning next steps");
  });

  it("hides unplanned future phases while DAN is still understanding the request", () => {
    const activeTask = task({
      task_id: "understanding-first-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: { active_run_id: "understanding-first-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "understanding-first-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      chunks: [
        {
          id: "message:user-understanding-first",
          kind: "chat",
          title: "You · Request",
          body: "Update README with the current project status",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "live.request_understanding.briefed",
          task_id: "understanding-first-task",
          run_id: "understanding-first-run",
          payload: {
            request_understanding_schema: "super_dan_request_understanding_v1",
            source: "rule_generation_brief",
            request_kind: "general",
            original_request: "Update README with the current project status",
            rule_generation_brief: [
              "Generate request-specific criteria before treating execution as complete.",
            ],
          },
        },
      ],
    });

    expect(nodes.find((node) => node.id === "blueprint:understanding")).toMatchObject({
      status: "active",
      detail: "Rule-generation brief sent to DAN",
    });
    expect(nodes.find((node) => node.id === "blueprint:planning")).toBeUndefined();
    expect(nodes.find((node) => node.kind === "build")).toBeUndefined();
    expect(nodes.find((node) => node.kind === "validation")).toBeUndefined();
    expect(nodes.find((node) => node.kind === "answer")).toBeUndefined();
  });

  it("builds live preview status from current task progress and new event results", () => {
    const activeTask = task({
      task_id: "live-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: { active_run_id: "live-run" },
    });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_started",
        source_event_type: "live.generic_build.started",
        run_id: "live-run",
        task_id: "live-task",
        summary: "Working in this workspace.",
      },
      {
        type: "worker_progress",
        source_event_type: "tool.completed",
        run_id: "live-run",
        task_id: "live-task",
        payload: {
          tool_id: "file_edit",
          result: {
            path: "README.md",
            changed: true,
          },
        },
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "live-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
    });

    const build = nodes.find((node) => node.kind === "build");
    expect(build).toBeTruthy();
    const status = blueprintLiveStatusForTest(build!, [activeTask], events, activeTask);
    expect(status.status).toBe("In progress");
    expect(status.now).toContain("Thinking with kimi-k2.6");
    expect(status.latestUpdate).toContain("Changed `README.md`");
    expect(status.results).toContain("- Changed: `README.md`");
  });

  it("shows validation scope, deterministic checks, semantic checks, and branch results", () => {
    const validationTask = task({
      task_id: "validation-task",
      status: "completed",
      metadata: { active_run_id: "validation-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "validation-run",
      tasks: [validationTask],
      agentEvents: [
        {
          type: "worker_completed",
          source_event_type: "live.validation.completed",
          run_id: "validation-run",
          task_id: "validation-task",
          summary: "Validation passed.",
          payload: {
            passed: true,
            validation_scope: "branch_frontier",
            completion_scope: "current_frontier",
            validated_branch_ids: ["b1"],
            validated_task_ids: ["1-1"],
            branch_results: [
              {
                branch_id: "b1",
                task_ids: ["1-1"],
                status: "passed",
                evidence: ["README.md inspected"],
              },
            ],
            deterministic_checks: [
              {
                check: "npm test -- blueprintNodes",
                source: "command",
                status: "passed",
                evidence: "60 tests passed",
              },
            ],
            llm_semantic_checks: [
              {
                check: "Compared delivery against the project-summary request.",
                status: "passed",
                evidence: "Final answer directly explains the project.",
              },
            ],
            graph_level_validation: {
              all_ready_branches_passed: true,
              remaining_deferred_task_ids: ["2-1"],
            },
          },
        },
      ],
    });

    const validation = nodes.find((node) => node.kind === "validation");
    expect(validation?.previewBody).toContain("### Validated Scope");
    expect(validation?.previewBody).toContain("branch_frontier");
    expect(validation?.previewBody).toContain("Branches: b1");
    expect(validation?.previewBody).toContain("### Deterministic Checks");
    expect(validation?.previewBody).toContain("npm test -- blueprintNodes");
    expect(validation?.previewBody).toContain("### LLM Semantic Checks");
    expect(validation?.previewBody).toContain("Compared delivery against the project-summary request.");
    expect(validation?.previewBody).toContain("### Branch Results");
    expect(validation?.previewBody).toContain("b1");
    expect(validation?.previewBody).toContain("### Graph Validation");
    expect(validation?.previewBody).toContain("all ready branches passed: true");
  });

  it("restores section and list structure in flattened final-answer markdown", () => {
    const flattened =
      "This is **ra-neo**, a tactical command game. ### What it is right now The project is in early prototyping: 1. **Three.js browser prototype** (`src/`) - A minimal ECS core. 2. **Godot 4.x desktop client** (`godot/`) - A native prototype. ### Core design pillars - **Visibility:** every entity is visible. - **Momentum:** formations move together.";

    const normalized = normalizeStructuredMarkdownForTest(flattened);

    expect(normalized).toContain(
      "This is **ra-neo**, a tactical command game.\n\n### What it is right now\n\nThe project",
    );
    expect(normalized).toContain("\n1. **Three.js browser prototype**");
    expect(normalized).toContain("\n- A minimal ECS core.");
    expect(normalized).toContain("\n\n### Core design pillars\n\n- **Visibility:** every entity is visible.");
  });

  it("restores flattened pipe tables in final-answer markdown", () => {
    const flattened =
      "### Current state (evidence-backed) | Area | Status | Evidence ||------|--------|----------|| Godot core scripts | Implemented | `godot/src/core/*.gd` || Browser ECS | Implemented | `src/ecs/World.js`";

    const normalized = normalizeStructuredMarkdownForTest(flattened);

    expect(normalized).toContain(
      "### Current state (evidence-backed)\n\n| Area | Status | Evidence |\n| ------ | -------- | ---------- |",
    );
    expect(normalized).toContain(
      "| Godot core scripts | Implemented | `godot/src/core/*.gd` |\n| Browser ECS | Implemented | `src/ecs/World.js` |",
    );
  });

  it("explains denied tool calls with the policy reason", () => {
    const activeTask = task({
      task_id: "denied-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: { active_run_id: "denied-run" },
    });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "tool_used",
        source_event_type: "tool.started",
        run_id: "denied-run",
        task_id: "denied-task",
        summary: "tool.started",
        payload: {
          tool_id: "file_read",
          arguments: { path: "private-notes.md" },
        },
      },
      {
        type: "tool_used",
        source_event_type: "tool.policy_denied",
        run_id: "denied-run",
        task_id: "denied-task",
        summary: "tool.policy_denied",
        payload: {
          tool_id: "file_read",
          arguments: { path: "private-notes.md" },
          reason: "operator_intent_blocks_file_read:private-notes.md",
        },
      },
      {
        type: "tool_used",
        source_event_type: "tool.denied",
        run_id: "denied-run",
        task_id: "denied-task",
        summary: "denied",
        payload: {
          tool_id: "file_read",
          arguments: { path: "private-notes.md" },
        },
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "denied-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
    });

    const build = nodes.find((node) => node.kind === "build");
    expect(build).toBeTruthy();
    const status = blueprintLiveStatusForTest(build!, [activeTask], events, activeTask);
    expect(status.latestUpdate).toContain("file read denied for `private-notes.md`");
    expect(status.latestUpdate).toContain("current request blocks reading `private-notes.md`");
    expect(status.recentUpdates).toContain("Using file read.");
    expect(status.recentUpdates).not.toContain("denied");
  });

  it("treats assessment-only project reviews as answer work, not workspace mutation", () => {
    const activeTask = task({
      task_id: "review-task",
      status: "running",
      latest_progress: "Inspecting the project structure.",
      metadata: { active_run_id: "review-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "review-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      chunks: [
        {
          id: "message:user-review",
          kind: "chat",
          title: "You · Request",
          body: "help me review this project?",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          run_id: "review-run",
          task_id: "review-task",
          summary: "Working in this workspace.",
        },
      ],
    });

    const request = nodes.find((node) => node.kind === "request");
    expect(request).toMatchObject({
      detail: "Request constraints captured",
      body: "Ready for request understanding.",
    });
    expect(request?.previewBody).toContain(
      "Review response expected; file edits are not expected unless requested.",
    );
    expect(nodes.find((node) => node.id === "blueprint:planning")).toBeUndefined();
    expect(nodes.find((node) => node.id === "blueprint:build")).toMatchObject({
      title: "Prepare review response",
      detail: "Review response; no file edits expected",
      body: "Review response; no file edits expected",
    });
  });

  it("treats project-summary wording as an in-session answer request", () => {
    const activeTask = task({
      task_id: "project-summary-task",
      status: "running",
      latest_progress: "Inspecting the project structure.",
      metadata: { active_run_id: "project-summary-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "project-summary-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      chunks: [
        {
          id: "message:user-project-summary",
          kind: "chat",
          title: "You · Request",
          body: "what is this project about?",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          run_id: "project-summary-run",
          task_id: "project-summary-task",
          summary: "Working in this workspace.",
        },
      ],
    });

    expect(nodes.find((node) => node.id === "blueprint:build")).toMatchObject({
      title: "Prepare answer",
      detail: "Project answer; no file edits expected",
      body: "Project answer; no file edits expected",
    });
    expect(nodes.map((node) => node.title).join("\n")).not.toContain("Execute workspace change");
  });

  it("treats follow-up no-edit wording as direct response work", () => {
    const activeTask = task({
      task_id: "summary-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: { active_run_id: "summary-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "summary-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      chunks: [
        {
          id: "message:user-summary",
          kind: "chat",
          title: "You · Request",
          body: "wait, don't edit anything, i want you to help me summary what this project is about",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          run_id: "summary-run",
          task_id: "summary-task",
          summary: "Working in this workspace.",
        },
      ],
    });

    expect(nodes.find((node) => node.id === "blueprint:build")).toMatchObject({
      title: "Prepare direct response",
      detail: "No file edits allowed",
      body: "No file edits allowed",
    });
    expect(nodes.map((node) => node.title).join("\n")).not.toContain("Execute workspace change");
  });

  it("does not auto-restore the previous thread while a new session is being created", () => {
    expect(
      shouldAutoRestoreSessionForTest({
        activeThreadPresent: false,
        creatingSession: true,
        targetThreadId: "previous-thread",
        threadCount: 3,
      }),
    ).toBe(false);
    expect(
      shouldAutoRestoreSessionForTest({
        activeThreadPresent: false,
        creatingSession: false,
        targetThreadId: "previous-thread",
        threadCount: 3,
      }),
    ).toBe(true);
  });

  it("does not pick archived sessions as restore targets", () => {
    const archived = thread({ id: "archived-thread", archived: true });
    const active = thread({ id: "active-thread", archived: false });

    expect(restorableThreadTargetForTest([archived, active], "archived-thread")).toBeNull();
    expect(restorableThreadTargetForTest([archived, active], "active-thread")).toMatchObject({
      id: "active-thread",
    });
  });

  it("detects when the selected Work Plan thread has become archived", () => {
    const archived = thread({ id: "archived-thread", archived: true });
    const active = thread({ id: "active-thread", archived: false });

    expect(
      activeThreadArchivedSummaryForTest(
        { id: "archived-thread", workflowId: "_scratch" },
        [archived, active],
      ),
    ).toMatchObject({ id: "archived-thread" });
    expect(
      activeThreadArchivedSummaryForTest(
        { id: "active-thread", workflowId: "_scratch" },
        [archived, active],
      ),
    ).toBeNull();
  });

  it("keeps archived sessions in one group with workspace subgroups", () => {
    const groups = buildSessionGroupsForTest({
      threads: [
        thread({ id: "active-ra", title: "Active RA" }),
        thread({ id: "archived-ra", title: "Old RA", archived: true }),
        thread({
          id: "archived-scratch",
          title: "Old Scratch",
          workflow_id: "_scratch",
          archived: true,
        }),
      ],
      workspaces: [
        {
          id: "ra-neo",
          name: "ra-neo",
          pinnedPaths: ["/Users/lizhi/Downloads/local_projects/ra-neo"],
          activeThreadId: "active-ra",
          openThreadIds: ["active-ra"],
        },
      ],
      threadQuery: "",
      threadWorkspaces: {
        "_scratch:active-ra": "ra-neo",
        "_scratch:archived-ra": "ra-neo",
      },
      taskWorkspaceByThreadId: new Map(),
    });

    const archived = groups.find((group) => group.id === "archived");
    expect(archived).toMatchObject({
      name: "Archived",
      threads: expect.arrayContaining([
        expect.objectContaining({ id: "archived-ra" }),
        expect.objectContaining({ id: "archived-scratch" }),
      ]),
    });
    expect(archived?.subgroups?.map((group) => group.name)).toEqual(["ra-neo", "Scratch"]);
    expect(archived?.subgroups?.[0].threads.map((item) => item.id)).toEqual(["archived-ra"]);
    expect(archived?.subgroups?.[1].threads.map((item) => item.id)).toEqual([
      "archived-scratch",
    ]);
  });

  it("keeps a newly selected blank session empty while another session runs in the background", () => {
    const backgroundRunning = task({
      task_id: "other-running",
      thread_id: "other-thread",
      status: "running",
      latest_progress: "Working in another session",
      metadata: { active_run_id: "other-run" },
    });
    const sessionStatusTasks = sessionStatusTasksForTest([], [backgroundRunning]);

    expect(selectActiveRunningTaskForTest(sessionStatusTasks)?.thread_id).toBe("other-thread");

    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeThreadTitle: "New Super DAN Session",
      tasks: [],
      activeRunId: "",
      activeRunningTask: null,
    });

    expect(nodes).toEqual([]);
  });

  it("lets the selected session task snapshot override the background cache", () => {
    const background = task({
      task_id: "same-task",
      thread_id: "selected-thread",
      status: "queued",
      latest_progress: "Older cached state",
    });
    const selected = task({
      task_id: "same-task",
      thread_id: "selected-thread",
      status: "running",
      latest_progress: "Fresh selected state",
      metadata: { active_run_id: "selected-run" },
    });

    const merged = sessionStatusTasksForTest([selected], [background]);

    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({
      status: "running",
      latest_progress: "Fresh selected state",
    });
  });

  it("shows session progress actions only for genuinely running tasks", () => {
    const running = task({
      task_id: "running-task",
      thread_id: "running-thread",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: { active_run_id: "running-run" },
    });
    const queued = task({
      task_id: "queued-task",
      thread_id: "queued-thread",
      status: "queued",
      metadata: { active_run_id: "queued-run" },
    });
    const completed = task({
      task_id: "completed-task",
      thread_id: "completed-thread",
      status: "completed",
      metadata: { active_run_id: "completed-run" },
    });

    expect(sessionProgressTaskForTest([queued, running, completed], "running-thread")).toMatchObject({
      task_id: "running-task",
    });
    expect(sessionProgressTaskForTest([queued, running, completed], "queued-thread")).toBeNull();
    expect(sessionProgressTaskForTest([queued, running, completed], "completed-thread")).toBeNull();
  });

  it("projects emitted task graphs into ready and future blueprint nodes", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeThreadTitle: "Seminar trip planner",
      chunks: [
        {
          id: "message:user-1",
          kind: "chat",
          title: "You · Request",
          body: "Build a seminar trip planner",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.plan_validation.completed",
          summary: "Plan validation passed.",
          payload: {
            task_graph: [
              {
                task_id: "1-1",
                goal: "Create app shell",
                depends_on: [],
                owned_paths: ["index.html"],
                deliverables: ["index.html"],
                validation: ["open the page"],
                parallel_safe: true,
              },
              {
                task_id: "2-1",
                goal: "Add persistence",
                depends_on: ["1-1"],
                owned_paths: ["app.js"],
                deliverables: ["app.js"],
                validation: ["localStorage smoke"],
                parallel_safe: false,
              },
            ],
            ready_task_ids: ["1-1"],
            deferred_task_ids: ["2-1"],
            plan_files: [".dan-super/runs/turn-01/plans/1-trip-planner.md"],
          },
        },
      ],
    });

    expect(nodes.map((node) => node.title)).toContain("Blueprint planning");
    expect(nodes.find((node) => node.id === "blueprint:task:1-1")?.status).toBe("ready");
    const futureNode = nodes.find((node) => node.id === "blueprint:task:2-1");
    expect(futureNode?.status).toBe("future");
    expect(futureNode?.compact).toBe(true);
  });

  it("renders revisioned task graph snapshots with branch-local state", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeThreadTitle: "Seminar trip planner",
      chunks: [
        {
          id: "message:user-graph",
          kind: "chat",
          title: "You · Request",
          body: "Build a seminar trip planner",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.task_graph.updated",
          summary: "Initial task graph emitted.",
          payload: {
            task_graph_state: {
              schema: "super_dan_task_graph_v1",
              revision: 4,
              version_id: "v2.b1.2",
              root_version_id: "v2",
              source: "planner",
              update_scope: "initial",
              update_reason: "Planner emitted the first task graph.",
              changed_task_ids: ["1-1"],
              changed_branch_ids: ["1"],
              tasks: [
                {
                  task_id: "1-1",
                  branch_id: "1",
                  goal: "Create app shell",
                  depends_on: [],
                  owned_paths: ["index.html"],
                  deliverables: ["index.html"],
                  validation: ["open the page"],
                  state: "ready",
                  parallel_safe: true,
                },
              ],
              ready_task_ids: ["1-1"],
            },
          },
        },
        {
          type: "worker_started",
          source_event_type: "live.task_graph.updated",
          summary: "Task graph updated.",
          payload: {
            task_graph_state: {
              schema: "super_dan_task_graph_v1",
              revision: 5,
              version_id: "v2.b1.3+b2.1",
              root_version_id: "v2",
              base_version_id: "v2",
              parent_version_ids: ["v2.b1.2", "v2"],
              source: "validator",
              update_scope: "branch_local",
              update_reason: "Validator advanced the ready frontier.",
              changed_task_ids: ["1-1"],
              changed_branch_ids: ["1", "2"],
              tasks: [
                {
                  task_id: "1-1",
                  branch_id: "1",
                  goal: "Create app shell",
                  depends_on: [],
                  owned_paths: ["index.html"],
                  deliverables: ["index.html"],
                  validation: ["open the page"],
                  state: "done",
                  parallel_safe: true,
                },
                {
                  task_id: "1-2",
                  branch_id: "1",
                  goal: "Add trip data",
                  depends_on: [],
                  owned_paths: ["data/trips.json"],
                  deliverables: ["data/trips.json"],
                  validation: ["read data file"],
                  state: "ready",
                  parallel_safe: true,
                },
                {
                  task_id: "2-1",
                  branch_id: "2",
                  goal: "Add persistence",
                  depends_on: ["1-1", "1-2"],
                  owned_paths: ["app.js"],
                  deliverables: ["app.js"],
                  validation: ["localStorage smoke"],
                  state: "deferred",
                  parallel_safe: false,
                },
              ],
              ready_task_ids: ["1-2"],
              deferred_task_ids: ["2-1"],
              active_task_ids: [],
              completed_task_ids: ["1-1"],
              parallel_groups: [["1-1", "1-2"]],
              branches: [
                {
                  branch_id: "1",
                  task_ids: ["1-1", "1-2"],
                  ready_task_ids: ["1-2"],
                  active_task_ids: [],
                  completed_task_ids: ["1-1"],
                  deferred_task_ids: [],
                },
                {
                  branch_id: "2",
                  task_ids: ["2-1"],
                  ready_task_ids: [],
                  active_task_ids: [],
                  completed_task_ids: [],
                  deferred_task_ids: ["2-1"],
                },
              ],
              branch_refs: [
                {
                  branch_id: "1",
                  version_id: "v2.b1.3",
                  parent_version_id: "v2.b1.2",
                  base_version_id: "v2",
                  local_revision: 3,
                  changed: true,
                },
                {
                  branch_id: "2",
                  version_id: "v2.b2.1",
                  parent_version_id: "v2",
                  base_version_id: "v2",
                  local_revision: 1,
                  changed: true,
                },
              ],
            },
          },
        },
      ],
    });

    const planNode = nodes.find((node) => node.id === "blueprint:planning");
    expect(planNode?.detail).toContain("v2.b1.3+b2.1");
    expect(planNode?.meta).toBe("validator");
    expect(planNode?.body).toContain("Validator advanced the ready frontier.");
    expect(planNode?.previewBody).toContain("Graph Version");
    expect(planNode?.previewBody).toContain("Parents: v2.b1.2, v2");
    expect(planNode?.previewBody).toContain("Parallel Groups");
    expect(planNode?.previewBody).toContain("Branch Refs");
    expect(planNode?.previewBody).toContain("1: 2 tasks, 1 ready, 1 done");
    expect(nodes.find((node) => node.id === "blueprint:task:1-1")?.status).toBe("done");
    const readyNode = nodes.find((node) => node.id === "blueprint:task:1-2");
    expect(readyNode?.status).toBe("ready");
    expect(readyNode?.meta).toContain("branch 1");
    expect(nodes.find((node) => node.id === "blueprint:task:2-1")?.status).toBe("future");

    const tree = liveTaskTreeForTest(nodes);
    expect(tree.map((node) => node.id)).toEqual([
      "blueprint:task:1-1",
      "blueprint:task:1-2",
      "blueprint:task:2-1",
    ]);
    expect(findTreeParentId(tree, "blueprint:task:1-1")).toBeNull();
    expect(findTreeParentId(tree, "blueprint:task:1-2")).toBeNull();
    expect(findTreeParentId(tree, "blueprint:task:2-1")).toBeNull();

    const graphRevisions = liveTaskGraphRevisionsForTest(nodes);
    expect(graphRevisions.map((revision) => revision.label)).toEqual([
      "v2.b1.2",
      "v2.b1.3+b2.1",
    ]);
    expect(graphRevisions[1]).toMatchObject({
      id: "v2.b1.3+b2.1",
      label: "v2.b1.3+b2.1",
      meta: expect.stringContaining("validator"),
      reason: "Validator advanced the ready frontier.",
    });
    expect(graphRevisions[0]?.branches[0]?.nodes.map((node) => node.status)).toEqual([
      "ready",
    ]);
    expect(graphRevisions[1]?.branches.map((branch) => branch.label)).toEqual([
      "Branch 1",
      "Branch 2",
    ]);
    expect(graphRevisions[1]?.branches[0]?.nodes.map((node) => node.id)).toEqual([
      "blueprint:task:1-1",
      "blueprint:task:1-2",
    ]);
  });

  it("keeps sequential run phases at the same tree level without emitted tasks", () => {
    const activeTask = task({
      task_id: "active-no-dag",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: { active_run_id: "run-no-dag" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "run-no-dag",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      chunks: [
        {
          id: "message:user-no-dag",
          kind: "chat",
          title: "You · Request",
          body: "Help me understand this project",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          summary: "Working in this workspace.",
          task_id: "active-no-dag",
          run_id: "run-no-dag",
        },
      ],
    });

    const tree = liveTaskTreeForTest(nodes);
    expect(tree).toEqual([]);
    expect(liveTaskGraphRevisionsForTest(nodes)).toEqual([]);
  });

  it("settles earlier broad phases when execution has already completed without a graph", () => {
    const activeTask = task({
      task_id: "stale-phase-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: { active_run_id: "stale-phase-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "stale-phase-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      chunks: [
        {
          id: "message:user-stale-phase",
          kind: "chat",
          title: "You · Request",
          body: "what is this project about",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "live.request_understanding.briefed",
          task_id: "stale-phase-task",
          run_id: "stale-phase-run",
          payload: {
            request_understanding_schema: "super_dan_request_understanding_v1",
            source: "rule_generation_brief",
            request_kind: "general",
            original_request: "what is this project about",
            rule_generation_brief: [
              "Answer in-session unless the operator explicitly asks for a saved artifact.",
            ],
          },
        },
        {
          type: "completed",
          source_event_type: "live.generic_build.completed",
          summary: "Execution completed.",
          task_id: "stale-phase-task",
          run_id: "stale-phase-run",
        },
      ],
    });

    const understanding = nodes.find((node) => node.id === "blueprint:understanding");
    const planning = nodes.find((node) => node.id === "blueprint:planning");
    const build = nodes.find((node) => node.kind === "build");

    expect(understanding).toMatchObject({
      status: "done",
      detail: "Request context handed into later work",
    });
    expect(planning).toBeUndefined();
    expect(build).toMatchObject({
      status: "done",
      title: "Prepare answer",
    });
    expect(liveTaskGraphRevisionsForTest(nodes)).toEqual([]);
  });

  it("derives generic target blueprint steps before the backend emits a planning DAG", () => {
    const activeTask = task({
      task_id: "seminar-active",
      status: "running",
      latest_progress: "Working: round=1 model=kimi-k2.6 tools=10.",
      metadata: { active_run_id: "seminar-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "seminar-run",
      activeRunningTask: activeTask,
      activeThreadTitle: "Seminar trip planner",
      tasks: [activeTask],
      chunks: [
        {
          id: "message:user-seminar",
          kind: "chat",
          title: "You · Request",
          body:
            'Build a small self-contained "seminar trip planner" in /private/tmp/dan-blueprint-smoke/trip-planner. ' +
            "Make one HTML file, one CSS file, one JS file, plus a README. Include an editable agenda with sessions, meals, travel blocks, and free time; " +
            "a packing checklist grouped by category; a budget table with automatic totals; notes saved to localStorage; a day filter and search box; " +
            "a compact mobile layout; sample data; and a quick local smoke check.",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          summary: "Working in this workspace.",
          task_id: "seminar-active",
          run_id: "seminar-run",
        },
      ],
    });

    const taskTitles = nodes.filter((node) => node.kind === "task").map((node) => node.title);
    expect(nodes.find((node) => node.id === "blueprint:planning")).toMatchObject({
      detail: "3 projected tasks · 1 ready now",
      meta: "request target: /private/tmp/dan-blueprint-smoke/trip-planner",
    });
    expect(taskTitles).toEqual(
      expect.arrayContaining([
        expect.stringContaining("Understand explicit target scope"),
        expect.stringContaining("Execute the current target slice"),
        expect.stringContaining("Validate and summarize target coverage"),
      ]),
    );
    expect(taskTitles.join(" ")).not.toContain("packing checklist");
    expect(taskTitles.join(" ")).not.toContain("budget table");
    expect(nodes.find((node) => node.id === "blueprint:understanding")).toMatchObject({
      title: "Understand request",
      status: "done",
      detail: "Request context handed into later work",
    });
    expect(nodes.find((node) => node.id === "blueprint:task:1")).toMatchObject({
      status: "active",
      detail: expect.stringContaining("/private/tmp/dan-blueprint-smoke/trip-planner"),
    });
    expect(nodes.find((node) => node.kind === "request")?.previewBody).toContain("### Targets");
  });

  it("renders structured request-understanding details in the preview body", () => {
    const activeTask = task({
      task_id: "understanding-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: {
        active_run_id: "understanding-run",
        operator_context: {
          raw_text: "Review this project and do not edit files.",
          target_paths: ["README.md"],
          hard_constraints: ["Do not edit files."],
          validation_requirements: ["Ground findings in project evidence."],
        },
      },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "understanding-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "live.request_understanding.completed",
          task_id: "understanding-task",
          run_id: "understanding-run",
          payload: {
            request_understanding_schema: "super_dan_request_understanding_v1",
            source: "model_authored",
            request_kind: "general",
            original_request: "Review this project and do not edit files.",
            target_paths: ["README.md"],
            aspect_reviews: [
              {
                aspect: "who",
                request_comment: "No explicit audience was named.",
                confidence: 0.64,
              },
              {
                aspect: "evidence",
                request_comment: "Use inspection notes and explicit limitations.",
                confidence: 0.78,
              },
            ],
            confidence_scoped_acceptance: [
              {
                criterion: "Cover confidently doable requirements or explain blockers.",
                confidence: 0.8,
                action: "do_or_explain",
              },
            ],
            stop_rule: "Stop only when evidence-backed criteria are covered.",
          },
        },
      ],
    });

    const understanding = nodes.find((node) => node.id === "blueprint:understanding");
    expect(understanding).toMatchObject({
      title: "Understand request",
      status: "done",
      meta: "general",
      detail: "Scope, constraints, targets, and acceptance gates tailored",
      body: "DAN tailored the request-understanding rules for this run.",
    });
    expect(understanding?.previewBody).toContain("Request kind: general · tailored by DAN");
    expect(understanding?.previewBody).toContain("### Aspect Review");
    expect(understanding?.previewBody).toContain("who: No explicit audience was named.");
    expect(understanding?.previewBody).toContain("### Acceptance Criteria");
    expect(understanding?.previewBody).toContain(
      "Cover confidently doable requirements or explain blockers.",
    );
    const request = nodes.find((node) => node.kind === "request");
    expect(request?.previewBody).toContain("README.md");
    expect(request?.previewBody).not.toContain("do_or_explain");
    expect(request?.detail).not.toBe(request?.rawRequest);
    expect(request?.body).not.toBe(request?.rawRequest);
  });

  it("renders request-understanding rules briefs without fake acceptance criteria", () => {
    const activeTask = task({
      task_id: "rules-brief-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: {
        active_run_id: "rules-brief-run",
        operator_context: {
          raw_text: "whats this game",
        },
      },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "rules-brief-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "live.request_understanding.briefed",
          task_id: "rules-brief-task",
          run_id: "rules-brief-run",
          payload: {
            request_understanding_schema: "super_dan_request_understanding_v1",
            source: "rule_generation_brief",
            request_kind: "general",
            original_request: "whats this game",
            rule_generation_brief: [
              "Default what-is-this requests to an in-session answer unless the operator explicitly asks for a saved artifact.",
              "Generated criteria must test semantic delivery, not file existence.",
            ],
            aspect_reviews: [],
            confidence_scoped_acceptance: [],
            stop_rule: "",
          },
        },
      ],
    });

    const understanding = nodes.find((node) => node.id === "blueprint:understanding");
    expect(understanding).toMatchObject({
      title: "Understand request",
      status: "active",
      detail: "Rule-generation brief sent to DAN",
      body: "DAN is asking the model to generate request-specific rules for this run.",
    });
    expect(understanding?.previewBody).toContain("Request kind: general · generating rules");
    expect(understanding?.previewBody).toContain("### Rules Brief");
    expect(understanding?.previewBody).toContain("in-session answer");
    expect(understanding?.previewBody).not.toContain("### Acceptance Criteria");
    expect(understanding?.previewBody).not.toContain("Verify the file was written successfully");
  });

  it("does not treat untitled backend fallback titles as operator requests", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeThreadTitle: "Untitled DAN Super session",
      tasks: [task({ status: "queued", latest_progress: "model.requested" })],
    });

    expect(nodes.find((node) => node.kind === "request")).toBeUndefined();
  });

  it("selects an actually running task over newer queued work", () => {
    const queued = task({
      task_id: "newer-queued",
      status: "queued",
      metadata: { active_run_id: "queued-run" },
    });
    const running = task({
      task_id: "older-running",
      status: "running",
      metadata: { active_run_id: "running-run" },
    });

    expect(selectActiveRunningTaskForTest([queued, running])?.task_id).toBe("older-running");
  });

  it("keeps waiting and input-needed tasks visible without marking them active", () => {
    const rows = queueRowsFromTasksForTest([
      task({ task_id: "waiting", status: "waiting_dependency" }),
      task({ task_id: "input", status: "needs_input" }),
      task({ task_id: "running", status: "running" }),
    ]);

    expect(rows.find((row) => row.id === "task:waiting")).toMatchObject({
      label: "Waiting on dependency",
      active: false,
    });
    expect(rows.find((row) => row.id === "task:input")).toMatchObject({
      label: "Needs input",
      active: false,
    });
    expect(rows.find((row) => row.id === "task:running")).toMatchObject({
      label: "Active run",
      active: true,
    });

    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      queueRows: rows,
    });

    expect(nodes.find((node) => node.id === "blueprint:task:waiting")?.status).toBe("queued");
    expect(nodes.find((node) => node.id === "blueprint:task:input")?.status).toBe("blocked");
    expect(nodes.find((node) => node.id === "blueprint:task:running")?.status).toBe("active");
  });

  it("expands queued follow-ups into visible ghost plan steps", () => {
    const running = task({
      task_id: "running",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: {
        active_run_id: "running-run",
        queue_items: [
          {
            id: "followup-1",
            task_id: "running",
            lane: "append",
            status: "queued",
            text: "wait, don't edit anything, summarize the project",
            position: 1,
          },
        ],
      },
    });
    const rows = queueRowsFromTasksForTest([running]);
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "running-run",
      activeRunningTask: running,
      tasks: [running],
      queueRows: rows,
    });

    expect(rows.find((row) => row.id === "queue:followup-1")).toMatchObject({
      kind: "followup",
      label: "Steering message",
      detail: "wait, don't edit anything, summarize the project",
    });
    expect(nodes.find((node) => node.id === "blueprint:queue:followup-1:request")).toMatchObject({
      title: "Follow-up request",
      status: "done",
      rawRequest: "wait, don't edit anything, summarize the project",
    });
    expect(nodes.find((node) => node.id === "blueprint:queue:followup-1:plan")).toMatchObject({
      title: "Plan follow-up",
      status: "future",
    });
    expect(nodes.find((node) => node.id === "blueprint:queue:followup-1:action")).toMatchObject({
      title: "Work on follow-up",
      status: "future",
    });
    expect(nodes.find((node) => node.id === "blueprint:queue:followup-1:response")).toMatchObject({
      title: "Follow-up response",
      status: "future",
    });
  });

  it("appends an active follow-up without refreshing the completed base plan", () => {
    const runningFollowUp = task({
      task_id: "followup-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: { active_run_id: "followup-run" },
    });
    const rows = queueRowsFromTasksForTest([runningFollowUp]);
    const chunks = [
      {
        id: "message:user-base",
        kind: "chat" as const,
        title: "You · Request",
        body: "what is this project",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
        runId: "base-run",
      },
      {
        id: "agent-answer:base-run",
        kind: "agent" as const,
        title: "DAN · Answer",
        body: "This project is a tactical command game.",
        status: "clean" as const,
        meta: "run.log.completed",
        runId: "base-run",
      },
      {
        id: "message:user-followup",
        kind: "chat" as const,
        title: "You · Request",
        body: "good please keep working",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
        runId: "followup-run",
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "followup-run",
      activeRunningTask: runningFollowUp,
      tasks: [runningFollowUp],
      queueRows: rows,
      chunks,
      agentEvents: [
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "This project is a tactical command game.",
          run_id: "base-run",
          task_id: "base-task",
        },
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          summary: "Thinking with kimi-k2.6.",
          run_id: "followup-run",
          task_id: "followup-task",
        },
      ],
    });

    expect(nodes.find((node) => node.kind === "request")).toMatchObject({
      title: "Operator request",
      rawRequest: "what is this project",
      status: "done",
    });
    expect(nodes.find((node) => node.kind === "answer" && node.runId === "base-run")).toMatchObject({
      title: "Final response",
      body: "This project is a tactical command game.",
      status: "done",
    });
    expect(nodes.find((node) => node.kind === "request" && node.runId === "followup-run")).toMatchObject({
      title: "Operator request",
      rawRequest: "good please keep working",
      status: "done",
    });
    expect(nodes.find((node) => node.kind === "build" && node.runId === "followup-run")).toMatchObject({
      status: "active",
    });
    expect(nodes.map((node) => node.title)).not.toContain("Active run");

    const timeline = blueprintTimelineItemsForTest(nodes, conversationUserChunksForTest(chunks));
    expect(timeline.map((item) => item.id)).toEqual([
      "conversation:message:user-base:before:blueprint:run:base-run:message:user-base",
      "node:blueprint:run:base-run:message:user-base",
      "node:blueprint:run:base-run:understanding",
      "node:blueprint:run:base-run:build",
      "node:blueprint:run:base-run:agent-answer:base-run",
      "conversation:message:user-followup:before:blueprint:run:followup-run:message:user-followup",
      "node:blueprint:run:followup-run:message:user-followup",
      "node:blueprint:run:followup-run:understanding",
      "node:blueprint:run:followup-run:build",
    ]);
  });

  it("recovers old phone-session requests from task metadata and shows stop requests", () => {
    const stopped = task({
      task_id: "phone-task",
      status: "queued",
      latest_progress: "Stop requested; the run will stop at the next safe checkpoint.",
      metadata: {
        active_run_id: "phone-run",
        stop_requested: true,
        stop_requested_at: "2020-01-01T00:00:00+00:00",
        last_surface_turn: {
          text: "Phone app smoke test: verify admission only.",
        },
      },
    });
    const rows = queueRowsFromTasksForTest([stopped]);
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeThreadTitle: "Phone smoke",
      tasks: [stopped],
      queueRows: rows,
    });

    expect(nodes.find((node) => node.kind === "request")).toMatchObject({
      detail: "Request captured",
      rawRequest: "Phone app smoke test: verify admission only.",
    });
    expect(rows[0]).toMatchObject({
      label: "Stop requested",
      status: "stop_requested",
      active: false,
    });
    expect(nodes.find((node) => node.id === "blueprint:task:phone-task")).toMatchObject({
      status: "blocked",
      detail: expect.stringContaining("Stop requested"),
    });
  });

  it("treats old saved running tasks as stale instead of active", () => {
    const stale = task({
      task_id: "review-task",
      status: "running",
      latest_progress: "model.requested",
      metadata: {
        active_run_id: "review-run",
        run_updated_at: "2020-01-01T00:00:00+00:00",
        last_surface_turn: {
          text: "help me review this project. no edits",
        },
      },
    });
    const rows = queueRowsFromTasksForTest([stale]);
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      tasks: [stale],
      queueRows: rows,
    });

    expect(selectActiveRunningTaskForTest([stale])).toBeNull();
    expect(rows[0]).toMatchObject({
      label: "Stale run",
      status: "stale_running",
      active: false,
      detail: expect.stringContaining("Saved run still says running"),
    });
    expect(nodes.find((node) => node.id === "blueprint:build")).toMatchObject({
      title: "Execution needs attention",
      status: "blocked",
      runId: "review-run",
      taskId: "review-task",
    });
    expect(nodes.find((node) => node.id === "blueprint:answer")).toMatchObject({
      status: "blocked",
      detail: "Stopped before final response",
    });
  });

  it("marks terminal failed work as blocked instead of leaving planned nodes ready", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-failed",
          kind: "chat",
          title: "You · Request",
          body: "Run a smoke test",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [
        task({
          task_id: "failed-task",
          status: "failed",
          latest_progress: "Invalid request: tool schema rejected",
          metadata: { active_run_id: "failed-run" },
        }),
      ],
    });

    expect(nodes.find((node) => node.id === "blueprint:build")).toMatchObject({
      title: "Execution needs attention",
      status: "blocked",
      runId: "failed-run",
      taskId: "failed-task",
    });
    expect(nodes.find((node) => node.id === "blueprint:answer")).toMatchObject({
      status: "blocked",
      detail: "Stopped before final response",
    });
  });

  it("does not use the user request as the needs-attention reason", () => {
    const blockedTask = task({
      task_id: "blocked-task",
      status: "blocked",
      latest_progress: "Super DAN needs attention.",
      metadata: {
        active_run_id: "blocked-run",
        last_surface_turn: {
          text: "is it fully implemented?",
        },
      },
    });
    const blockedEvent: ChatV2AgentRunEvent = {
      type: "blocked",
      source_event_type: "run.log.blocked",
      summary: "Super DAN needs attention.",
      task_id: "blocked-task",
      run_id: "blocked-run",
    };
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-blocked",
          kind: "chat",
          title: "You · Request",
          body: "is it fully implemented?",
          status: "clean",
          meta: "user",
          role: "user",
        },
        {
          id: "agent-terminal:blocked-run",
          kind: "agent",
          title: "Super DAN · needs attention",
          body: "Super DAN needs attention.",
          status: "error",
          meta: "run.log.blocked",
          runId: "blocked-run",
          taskId: "blocked-task",
        },
      ],
      tasks: [blockedTask],
      agentEvents: [blockedEvent],
    });

    const build = nodes.find((node) => node.id === "blueprint:build");
    expect(build).toMatchObject({
      title: "Execution needs attention",
      status: "blocked",
      detail: "DAN blocked this step but did not emit a specific reason.",
      body: "DAN blocked this step but did not emit a specific reason.",
    });
    expect(build?.body).not.toContain("is it fully implemented?");

    const answer = nodes.find((node) => node.kind === "answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "blocked",
      body: "DAN blocked this step but did not emit a specific reason.",
    });
    expect(answer?.body).not.toContain("Super DAN needs attention");
    expect(answer?.previewBody).toContain("DAN blocked this step but did not emit a specific reason.");
    expect(answer?.previewBody).not.toContain("is it fully implemented?");

    const liveStatus = blueprintLiveStatusForTest(build!, [blockedTask], [blockedEvent], null);
    expect(liveStatus.latestUpdate).toBe(
      "DAN needs attention, but did not emit a specific reason.",
    );
  });

  it("renders completed read-only exact-answer runs without edit-file wording", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-readonly",
          kind: "chat",
          title: "You · Request",
          body: "Reply exactly OK. Do not edit files or run external commands.",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [task({ task_id: "readonly-task", status: "completed" })],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "OK",
          task_id: "readonly-task",
          run_id: "readonly-run",
        },
        {
          type: "worker_completed",
          source_event_type: "live.validation.completed",
          summary: "Validation passed.",
          payload: {
            passed: true,
            comparison_note:
              "Operator policy did not require workspace mutation; no changed files were required.",
          },
        },
      ],
    });

    expect(nodes.find((node) => node.id === "blueprint:build")).toMatchObject({
      title: "Prepare direct response",
      detail: "No file edits or shell commands allowed",
      status: "done",
    });
    expect(nodes.find((node) => node.id === "blueprint:answer")).toMatchObject({
      title: "Final response",
      body: "OK",
      status: "done",
      compact: false,
      runId: "readonly-run",
      taskId: "readonly-task",
    });
    expect(nodes.map((node) => `${node.title} ${node.detail}`).join("\n")).not.toContain(
      "Execute workspace change Use tools, edit files",
    );
  });

  it("uses completed task progress as the final blueprint answer when terminal events are thin", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-readonly-reload",
          kind: "chat",
          title: "You · Request",
          body: "Reply exactly OK. Do not edit files or run external commands.",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [
        task({
          task_id: "readonly-task",
          status: "completed",
          latest_progress: "OK",
          metadata: { active_run_id: "readonly-run" },
        }),
      ],
      agentEvents: [
        {
          type: "worker_completed",
          source_event_type: "live.validation.completed",
          summary: "Validation passed.",
          payload: {
            passed: true,
            comparison_note:
              "Operator policy did not require workspace mutation; no changed files were required.",
          },
        },
      ],
    });

    expect(nodes.find((node) => node.id === "blueprint:answer")).toMatchObject({
      body: "OK",
      status: "done",
      runId: "readonly-run",
      taskId: "readonly-task",
    });
  });

  it("keeps workspace outcome evidence from replacing the final answer", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "review-run",
      chunks: [
        {
          id: "message:user-review-project",
          kind: "chat",
          title: "You · Request",
          body: "Help me review this project.",
          status: "clean",
          meta: "user",
          role: "user",
        },
        {
          id: "agent-answer:review-run",
          kind: "agent",
          title: "DAN · Answer",
          body: "I reviewed the project and updated the README with the current status.",
          status: "clean",
          meta: "run.log.completed",
          runId: "review-run",
        },
        {
          id: "agent-outcome:review-run",
          kind: "agent",
          title: "DAN · Outcome",
          body: "- Changed: `README.md`",
          status: "clean",
          meta: "workspace changes",
          runId: "review-run",
        },
      ],
    });

    const answer = nodes.find((node) => node.kind === "answer");
    expect(answer).toMatchObject({
      title: "Final response",
      body: "I reviewed the project and updated the README with the current status.",
      status: "done",
    });
    expect(answer?.body).not.toContain("Changed:");
    expect(answer?.previewBody).toContain("### Workspace Changes");
    expect(answer?.previewBody).toContain("Changed: `README.md`");
  });

  it("recovers the final summary from structured terminal payloads", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-payload-summary",
          kind: "chat",
          title: "You · Request",
          body: "Help me review this project and update README.md with the findings.",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [task({ task_id: "review-task", status: "completed" })],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "completed",
          task_id: "review-task",
          run_id: "review-run",
          payload: {
            result: {
              change_summary: ["Reviewed the project and updated the README status summary."],
              changed_files: ["README.md"],
            },
          },
        },
      ],
    });

    const answer = nodes.find((node) => node.kind === "answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "done",
      runId: "review-run",
      taskId: "review-task",
    });
    expect(answer?.body).toContain("Reviewed the project");
    expect(answer?.body).not.toBe("Super DAN completed.");
    expect(answer?.previewBody).toContain("### Summary");
    expect(answer?.previewBody).toContain("### Files");
  });

  it("keeps structured final progress user-facing instead of rendering raw internal JSON", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-review-project",
          kind: "chat",
          title: "You · Request",
          body: "Help me review this project and create PROJECT_REVIEW.md with the findings.",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [
        task({
          task_id: "review-task",
          status: "completed",
          latest_progress: JSON.stringify({
            candidate_id: "super-dan-live-general-001",
            change_summary: [
              "Created PROJECT_REVIEW.md with project status, architecture, and blockers.",
              "Fixed README.md milestone status to reflect actual progress.",
              "Added docs/review-notes.md with code-level pointers.",
            ],
            validation_summary: ["Workspace review notes were generated."],
            changed_files: ["PROJECT_REVIEW.md", "README.md", "docs/review-notes.md"],
            execution_quality: { score: 0.92 },
          }),
          metadata: { active_run_id: "review-run" },
        }),
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:answer");
    expect(answer?.body).toContain("Created PROJECT_REVIEW.md");
    expect(answer?.body).not.toContain("candidate_id");
    expect(answer?.body).not.toContain("execution_quality");
    expect(answer?.body).not.toContain("{");
    expect(answer?.previewBody).toContain("### Summary");
    expect(answer?.previewBody).toContain("### Files");
    expect(answer?.previewBody).toContain("### Checks");
    expect(answer?.previewBody).not.toContain("candidate_id");
    expect(answer?.previewBody).not.toContain("execution_quality");
  });

  it("unwraps fenced structured final progress before rendering the final response", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-fenced-json",
          kind: "chat",
          title: "You · Request",
          body: "Repair the project overview and README status.",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [
        task({
          task_id: "fenced-json-task",
          status: "completed",
          latest_progress: [
            "```json",
            JSON.stringify(
              {
                answer: "Repaired the overview and README status so they match the current project state.",
                candidate_id: "super-dan-live-general-001",
                change_summary: [
                  "Removed false references from PROJECT_OVERVIEW.md.",
                  "Corrected README.md milestone status.",
                ],
                files_changed: ["PROJECT_OVERVIEW.md", "README.md"],
              },
              null,
              2,
            ),
            "```",
          ].join("\n"),
          metadata: { active_run_id: "fenced-json-run" },
        }),
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "done",
      body: "Repaired the overview and README status so they match the current project state.",
    });
    expect(answer?.body).not.toContain("```");
    expect(answer?.body).not.toContain("candidate_id");
    expect(answer?.body).not.toContain("{");
    expect(answer?.previewBody).toContain("### Summary");
    expect(answer?.previewBody).toContain("### What Changed");
    expect(answer?.previewBody).toContain("### Files");
    expect(answer?.previewBody).not.toContain("```");
    expect(answer?.previewBody).not.toContain("candidate_id");
  });

  it("unwraps one-line fenced structured final progress before rendering the final response", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-one-line-json",
          kind: "chat",
          title: "You · Request",
          body: "What is this game?",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [
        task({
          task_id: "one-line-json-task",
          status: "completed",
          latest_progress: `\`\`\`json ${JSON.stringify({
            answer:
              "**ra-neo** is a real-time strategy prototype about large visible swarms.",
            candidate_id: "super-dan-live-review-001",
            summary: [
              "Browser-first ECS and flowfield project with a Godot validation layer.",
            ],
            remaining_work: ["Validate the 1,000-agent frame budget."],
          })} \`\`\``,
          metadata: { active_run_id: "one-line-json-run" },
        }),
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "done",
      body: "**ra-neo** is a real-time strategy prototype about large visible swarms.",
    });
    expect(answer?.body).not.toContain("```");
    expect(answer?.body).not.toContain("candidate_id");
    expect(answer?.body).not.toContain("{");
    expect(answer?.previewBody).toContain("### Summary");
    expect(answer?.previewBody).toContain("### What Changed");
    expect(answer?.previewBody).toContain("### Needs Attention");
    expect(answer?.previewBody).not.toContain("```");
    expect(answer?.previewBody).not.toContain("candidate_id");
  });

  it("does not fall back to raw JSON when structured final progress has only internal fields", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-internal-only",
          kind: "chat",
          title: "You · Request",
          body: "Help me review this project.",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [
        task({
          task_id: "internal-only-task",
          status: "completed",
          latest_progress: JSON.stringify({
            candidate_id: "super-dan-live-general-001",
            execution_quality: { score: 0.92 },
          }),
          metadata: { active_run_id: "internal-only-run" },
        }),
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:answer");
    expect(answer).toMatchObject({
      status: "blocked",
      detail: "Final answer missing",
      meta: "missing answer",
      compact: false,
    });
    expect(answer?.body).toContain("did not return");
    expect(answer?.previewBody).not.toContain("candidate_id");
    expect(answer?.previewBody).not.toContain("{");
  });

  it("marks generic completed events as missing the requested final answer", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-summary",
          kind: "chat",
          title: "You · Request",
          body: "Wait, don't edit anything. Help me summarize what this project is about.",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [
        task({
          task_id: "summary-task",
          status: "completed",
          latest_progress: "",
          metadata: { active_run_id: "summary-run" },
        }),
      ],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "completed",
          task_id: "summary-task",
          run_id: "summary-run",
        },
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "blocked",
      detail: "Final answer missing",
      body: "The run finished, but DAN did not return the in-session answer this request asked for.",
      runId: "summary-run",
      taskId: "summary-task",
      compact: false,
    });
    expect(answer?.body).not.toContain("Super DAN completed");
    expect(answer?.previewBody).toContain("### Remaining Attention");
    expect(answer?.previewBody).toContain("Ask DAN to answer in this session");
  });

  it("does not treat a run-finished terminal chunk as a human-readable project answer", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-project-about",
          kind: "chat",
          title: "You · Request",
          body: "Help me understand what this project is about",
          status: "clean",
          meta: "user",
          role: "user",
        },
        {
          id: "agent-terminal:project-about-run",
          kind: "agent",
          title: "Super DAN · completed",
          body: "Run finished.",
          status: "clean",
          meta: "run.log.completed",
          runId: "project-about-run",
          taskId: "project-about-task",
        },
      ],
      tasks: [
        task({
          task_id: "project-about-task",
          status: "completed",
          latest_progress: "",
          metadata: { active_run_id: "project-about-run" },
        }),
      ],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "Run finished.",
          task_id: "project-about-task",
          run_id: "project-about-run",
        },
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:agent-terminal:project-about-run");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "blocked",
      detail: "Final answer missing",
      meta: "missing answer",
      body: "The run finished, but DAN did not return the in-session answer this request asked for.",
      compact: false,
    });
    expect(answer?.body).not.toBe("Run finished.");
    expect(answer?.previewBody).toContain("Ask DAN to answer in this session");
  });

  it("does not treat project-summary file receipts as the requested session answer", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "summary-run",
      chunks: [
        {
          id: "message:user-project-summary",
          kind: "chat",
          title: "You · Request",
          body: "what is this project about?",
          status: "clean",
          meta: "user",
          role: "user",
        },
        {
          id: "agent-outcome:summary-run",
          kind: "agent",
          title: "DAN · Outcome",
          body: "- Changed: `PROJECT_SUMMARY.md`",
          status: "clean",
          meta: "workspace changes",
          runId: "summary-run",
          taskId: "summary-task",
        },
      ],
      tasks: [
        task({
          task_id: "summary-task",
          status: "completed",
          latest_progress: JSON.stringify({
            summary: [
              "Created a project overview file instead of answering in the session.",
            ],
            change_summary: ["Created PROJECT_SUMMARY.md"],
            files_created: ["PROJECT_SUMMARY.md"],
          }),
          metadata: { active_run_id: "summary-run" },
        }),
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "blocked",
      detail: "Final answer missing",
      body:
        "The run produced workspace or file evidence, but DAN did not return the in-session answer this request asked for.",
    });
    expect(answer?.body).not.toContain("PROJECT_SUMMARY.md");
    expect(answer?.previewBody).toContain("### Workspace Changes");
    expect(answer?.previewBody).toContain("Changed: `PROJECT_SUMMARY.md`");
    expect(answer?.previewBody).toContain("Ask DAN to answer in this session");
  });

  it("does not reuse an old completed answer while a newer run is active", () => {
    const activeTask = task({
      task_id: "active-task",
      status: "running",
      latest_progress: "Working: round=1 model=kimi-k2.6 tools=10.",
      metadata: { active_run_id: "active-run" },
    });

    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "active-run",
      activeRunningTask: activeTask,
      chunks: [
        {
          id: "message:user-current",
          kind: "chat",
          title: "You · Request",
          body: "Build a seminar trip planner",
          status: "clean",
          meta: "user",
          role: "user",
        },
        {
          id: "agent-answer:old-run",
          kind: "agent",
          title: "DAN · Answer",
          body: "blueprint smoke test ok",
          status: "clean",
          meta: "run.log.completed",
          runId: "old-run",
        },
      ],
      tasks: [
        task({
          task_id: "old-task",
          status: "completed",
          latest_progress: "blueprint smoke test ok",
          metadata: { active_run_id: "old-run" },
        }),
        activeTask,
      ],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "blueprint smoke test ok",
          task_id: "old-task",
          run_id: "old-run",
        },
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          summary: "Working: round=1 model=kimi-k2.6 tools=10.",
          task_id: "active-task",
          run_id: "active-run",
        },
      ],
    });

    expect(nodes.find((node) => node.kind === "answer" && node.runId === "old-run")).toMatchObject({
      status: "done",
      body: "blueprint smoke test ok",
    });
    expect(nodes.find((node) => node.kind === "build" && node.runId === "active-run")).toMatchObject({
      status: "active",
      runId: "active-run",
      taskId: "active-task",
    });
    const activeAnswer = nodes.find((node) => node.kind === "answer" && node.runId === "active-run");
    expect(activeAnswer).toBeUndefined();
  });

  it("keeps timeout events out of the final answer while retry is active", () => {
    const activeTask = task({
      task_id: "retry-task",
      status: "running",
      latest_progress: "Retrying after timeout",
      metadata: { active_run_id: "retry-run" },
    });

    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "retry-run",
      activeRunningTask: activeTask,
      chunks: [
        {
          id: "message:user-timeout",
          kind: "chat",
          title: "You · Request",
          body: "Build a seminar trip planner",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [activeTask],
      agentEvents: [
        {
          type: "failed",
          source_event_type: "model.timeout",
          summary: "timeout",
          task_id: "retry-task",
          run_id: "retry-run",
        },
        {
          type: "worker_started",
          source_event_type: "live.builder_retry.started",
          summary: "Retrying",
          task_id: "retry-task",
          run_id: "retry-run",
        },
      ],
    });

    expect(nodes.find((node) => node.id === "blueprint:repair")).toMatchObject({
      status: "active",
      runId: "retry-run",
      taskId: "retry-task",
    });
    expect(nodes.find((node) => node.id === "blueprint:answer")).toBeUndefined();
  });
});
