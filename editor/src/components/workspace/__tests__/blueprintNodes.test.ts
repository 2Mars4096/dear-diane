import { describe, expect, it, vi } from "vitest";
import {
  activeThreadArchivedSummaryForTest,
  blueprintAnchorNodeForTest,
  blueprintCardContentForTest,
  buildSessionGroupsForTest,
  blueprintTimelineItemsForTest,
  blueprintLiveStatusForTest,
  buildBlueprintNodesForTest,
  catalogNotesForTest,
  composerDraftForThreadForTest,
  composerDraftsWithValueForTest,
  conversationUserChunksForTest,
  createTemporaryDraftNoteForTest,
  liveTaskGraphRevisionsForTest,
  liveTaskTreeForTest,
  materializeTemporaryDraftNoteForTest,
  formatHugoPreviewBodyForTest,
  noteMetaItemsForTest,
  notesComposerRequestsNewDraftForTest,
  noteRailViewForFacetForTest,
  normalizeStructuredMarkdownForTest,
  planCardChecklistItemsForTest,
  previewMarkdownContentForTest,
  planTaskChecklistItemsForTest,
  queueDisplayDetailForTest,
  queueRowsFromTasksForTest,
  recentModifiedNotesForTest,
  restorableThreadTargetForTest,
  sessionCardDisplayForTest,
  sessionHasNewReadyResponseForTest,
  sessionProgressTaskForTest,
  sessionReadyResponseAtForTest,
  sharedEvidenceItemsForTest,
  selectActiveRunningTaskForTest,
  settleTaskSnapshotsFromEventsForTest,
  sessionStatusTasksForTest,
  shouldAutoRestoreSessionForTest,
  statusLineUsesMarkdownForTest,
  statusMarkdownContentForTest,
  taskGroupElapsedCounterForTest,
  workspaceComposerPlacementLabelForTest,
  workPlanHeaderSubtitleForTest,
  workspaceComposerPlaceholderForTest,
  workspaceComposerPrimaryActionLabelForTest,
  workspaceComposerSuggestionsForTest,
  workspaceComposerTokenForTest,
  workspaceIntentClassificationForTest,
  workspaceMentionedFilesFromTextForTest,
  workPanelTasksForTest,
  workspaceSelectedSkillInvocationForTest,
  workspaceAgentExecutePayloadForTest,
  workspaceAgentExecutePayloadWithAutonomyForTest,
  workspaceAgentOptionsForTest,
  workspaceModelOptionsForTest,
  workspacePreviewArtifactsForTest,
  workspacePreviewOpenUrlForTest,
  workspaceSelectionFromStorageForTest,
  workspaceSurfaceContextForTest,
  workspaceIdForTasksForTest,
  workspaceRootForTasksForTest,
  workingNoteCardsForTest,
} from "../ChunkWorkspaceApp";
import { renderMarkdownToHtml } from "../../shared/MarkdownRenderer";
import type { WorkspaceFileEntry } from "../../../lib/api";
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

function workspaceFile(relativePath: string, overrides: Partial<WorkspaceFileEntry> = {}): WorkspaceFileEntry {
  return {
    path: `/tmp/workspace/${relativePath}`,
    relative_path: relativePath,
    name: relativePath.split("/").pop() || relativePath,
    parent: relativePath.includes("/") ? relativePath.slice(0, relativePath.lastIndexOf("/")) : "",
    is_directory: false,
    size: 1024,
    mtime: 100,
    depth: Math.max(0, relativePath.split("/").length - 1),
    ...overrides,
  };
}

describe("workspace blueprint nodes", () => {
  it("exposes Super DAN as the only workspace agent with a separate model choice", () => {
    const agentOptions = workspaceAgentOptionsForTest();
    const modelOptions = workspaceModelOptionsForTest("native");
    const payload = workspaceAgentExecutePayloadForTest("native", "native_default");

    expect(agentOptions).toEqual([
      expect.objectContaining({ id: "native", label: "Super DAN", backend: "super_dan" }),
    ]);
    expect(modelOptions.map((option) => option.id)).toEqual(["native_default", "native_kimi_k26"]);
    expect(payload.backend).toBe("super_dan");
    expect(payload.profile_policy).toMatchObject({
      backend: "super_dan",
      surface_profile: "super_tui",
    });
    expect(payload.profile_policy).not.toHaveProperty("model");
    expect(payload.metadata).toMatchObject({
      selected_agent: "native",
      selected_model_option: "native_default",
      gui_for: "dan super-tui",
    });
  });

  it("routes the Kimi option through the existing Super DAN backend", () => {
    const payload = workspaceAgentExecutePayloadForTest("native", "native_kimi_k26");

    expect(payload.backend).toBe("super_dan");
    expect(payload.profile_policy).toMatchObject({
      backend: "super_dan",
      model: "kimi-k2.6",
    });
    expect(payload.metadata).toMatchObject({
      selected_agent: "native",
      selected_model_option: "native_kimi_k26",
      selected_model: "kimi-k2.6",
      gui_for: "dan super-tui",
    });
  });

  it("migrates archived Codex selections onto the supported Super DAN backend", () => {
    const modelOptions = workspaceModelOptionsForTest("codex");
    const payload = workspaceAgentExecutePayloadForTest("codex", "codex_gpt_5_5_high");

    expect(modelOptions).toEqual([]);
    expect(payload.backend).toBe("super_dan");
    expect(payload.profile_policy).toMatchObject({
      backend: "super_dan",
      surface_profile: "super_tui",
    });
    expect(payload.profile_policy).not.toHaveProperty("codex_model");
    expect(payload.profile_policy).not.toHaveProperty("codex_reasoning_effort");
    expect(payload.metadata).toMatchObject({
      selected_agent: "native",
      selected_backend: "super_dan",
      selected_model_option: "native_default",
      gui_for: "dan super-tui",
    });
  });

  it("carries workspace autonomy mode into Agent execute payloads", () => {
    const autoPayload = workspaceAgentExecutePayloadWithAutonomyForTest(
      "native",
      "native_default",
      "auto",
    );
    const reviewPayload = workspaceAgentExecutePayloadWithAutonomyForTest(
      "native",
      "native_default",
      "review",
    );

    expect(autoPayload.profile_policy).toMatchObject({
      autonomy_mode: "auto",
      attention_resolution_mode: "auto",
    });
    expect(autoPayload.approval_policy).toMatchObject({
      mode: "auto_within_workspace",
      attention_resolution: "auto",
    });
    expect(reviewPayload.profile_policy).toMatchObject({
      autonomy_mode: "review",
      attention_resolution_mode: "review",
    });
    expect(reviewPayload.approval_policy).toMatchObject({
      mode: "ask_on_attention",
      attention_resolution: "review",
    });
  });

  it("drops archived Agent selections while preserving the supported native model", () => {
    const selection = workspaceSelectionFromStorageForTest({
      storedAgent: "codex",
      storedModel: "native_default",
      storedModelsByAgent: JSON.stringify({
        native: "native_kimi_k26",
        codex: "codex_gpt_5_5_high",
      }),
    });

    expect(selection.agentId).toBe("native");
    expect(selection.modelId).toBe("native_kimi_k26");
    expect(selection.modelSelectionsByAgent).toEqual({
      native: "native_kimi_k26",
      codex: "native_default",
    });
  });

  it("migrates older single-value workspace selector storage", () => {
    expect(
      workspaceSelectionFromStorageForTest({
        storedAgent: "super_dan_kimi_k26",
      }),
    ).toMatchObject({
      agentId: "native",
      modelId: "native_kimi_k26",
      modelSelectionsByAgent: {
        native: "native_kimi_k26",
        codex: "native_default",
      },
    });

    expect(
      workspaceSelectionFromStorageForTest({
        storedAgent: "codex",
        storedModel: "codex_gpt_5_5_xhigh",
      }),
    ).toMatchObject({
      agentId: "native",
      modelId: "native_default",
      modelSelectionsByAgent: {
        native: "native_default",
        codex: "native_default",
      },
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

  it("labels the composer as a new send when no run is active", () => {
    expect(
      workspaceComposerPrimaryActionLabelForTest({
        hasActiveRun: false,
        placement: "steer",
        isStop: false,
      }),
    ).toBe("Send");
    expect(workspaceComposerPlacementLabelForTest("steer", false)).toBe("New");
    expect(workspaceComposerPlacementLabelForTest("queue", false)).toBe("Next");
  });

  it("labels the composer as steer, next, or stop only for active runs", () => {
    expect(
      workspaceComposerPrimaryActionLabelForTest({
        hasActiveRun: true,
        placement: "steer",
        isStop: false,
      }),
    ).toBe("Steer");
    expect(
      workspaceComposerPrimaryActionLabelForTest({
        hasActiveRun: true,
        placement: "queue",
        isStop: false,
      }),
    ).toBe("Next");
    expect(
      workspaceComposerPrimaryActionLabelForTest({
        hasActiveRun: true,
        placement: "steer",
        isStop: true,
      }),
    ).toBe("Stop");
    expect(workspaceComposerPlacementLabelForTest("steer", true)).toBe("Steer");
  });

  it("creates tmp Hugo drafts with the cursor target in the body", () => {
    const note = createTemporaryDraftNoteForTest({
      now: new Date(2026, 6, 17, 10, 20, 30),
      targetSection: "blogs/travel-plans",
      facet: "tag:travel",
    });

    expect(note.source).toBe("local");
    expect(note.temporary).toBe(true);
    expect(note.draftTargetSection).toBe("blogs/travel-plans");
    expect(note.path).toBe("tmp://notes/20260717-102030");
    expect(note.relativePath).toBe("tmp/drafts/untitled-draft-20260717-102030.md");
    expect(note.content).toContain("draft: true");
    expect(note.content).toContain('tags: ["travel"]');
    expect(note.bodyStartOffset).toBe(note.content.length);
  });

  it("materializes tmp drafts into inferred Hugo bundle paths", () => {
    const note = createTemporaryDraftNoteForTest({
      now: new Date(2026, 6, 17, 10, 20, 30),
      seedText:
        "note: # Turin Automobile Museum Guide\n\nOpening hours and ticket notes for MAUTO.",
      targetSection: "blogs/travel-plans",
      facet: "category:travel",
    });
    const existing = {
      ...note,
      id: "server:/kb/content/blogs/travel-plans/turin-automobile-museum-guide/index.md",
      source: "server" as const,
      path: "/kb/content/blogs/travel-plans/turin-automobile-museum-guide/index.md",
      relativePath: "blogs/travel-plans/turin-automobile-museum-guide/index.md",
      temporary: false,
    };

    const materialized = materializeTemporaryDraftNoteForTest({
      note,
      notes: [note, existing],
      root: "/kb/content",
      facet: "category:travel",
      activeSection: "notes",
      now: new Date(2026, 6, 18, 9, 0, 0),
    });

    expect(materialized).toMatchObject({
      title: "Turin Automobile Museum Guide",
      relativePath: "blogs/travel-plans/turin-automobile-museum-guide-2/index.md",
      path: "/kb/content/blogs/travel-plans/turin-automobile-museum-guide-2/index.md",
      pageID: "blogs-travel-plans-turin-automobile-museum-guide-2",
      categories: ["travel"],
    });
    expect(materialized?.content).toContain('title: "Turin Automobile Museum Guide"');
    expect(materialized?.content).toContain("draft: false");
    expect(materialized?.content).toContain("# Turin Automobile Museum Guide");
  });

  it("detects only clear Notes composer requests for new drafts", () => {
    expect(notesComposerRequestsNewDraftForTest("create a new note about Turin museums")).toBe(true);
    expect(notesComposerRequestsNewDraftForTest("note: Turin museums\n\nTickets and hours")).toBe(true);
    expect(notesComposerRequestsNewDraftForTest("how do I create a note in Hugo?")).toBe(false);
    expect(notesComposerRequestsNewDraftForTest("summarize this page")).toBe(false);
  });

  it("keeps tmp drafts out of catalog lists while Working Now can show them", () => {
    const draft = createTemporaryDraftNoteForTest({
      now: new Date(2026, 6, 17, 10, 20, 30),
      seedText: "create a new note about Turin museums",
      targetSection: "blogs/travel-plans",
    });
    const durableNote = {
      ...draft,
      id: "server:/kb/content/blogs/travel-plans/turin-food/index.md",
      title: "Turin Food",
      source: "server" as const,
      path: "/kb/content/blogs/travel-plans/turin-food/index.md",
      relativePath: "blogs/travel-plans/turin-food/index.md",
      section: "blogs",
      status: "clean" as const,
      temporary: false,
      draftTargetSection: undefined,
      updatedAt: new Date(2026, 6, 18, 9, 0, 0).getTime(),
    };

    expect(catalogNotesForTest([draft, durableNote]).map((note) => note.id)).toEqual([
      durableNote.id,
    ]);
    expect(recentModifiedNotesForTest([draft, durableNote], "/kb/content")).toHaveLength(1);
    expect(workingNoteCardsForTest(draft, "/kb/content")[0]).toMatchObject({
      path: "Uncategorized draft",
      state: "Editing",
    });
  });

  it("renders active Work card content from live branch updates", () => {
    const activeTask = task({
      task_id: "active-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: { active_run_id: "run-1" },
    });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_started",
        source_event_type: "live.generic_build.started",
        run_id: "run-1",
        task_id: "active-task",
        summary: "Inspecting GameLoop.gd and EcsStore.gd.",
        payload: { branch_id: "b1" },
      },
      {
        type: "worker_started",
        source_event_type: "live.generic_build.started",
        run_id: "run-1",
        task_id: "active-task",
        summary: "Checking SpatialHash.gd neighbor queries.",
        payload: { branch_id: "b2" },
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "run-1",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
    });
    const build = nodes.find((node) => node.kind === "build");

    expect(blueprintCardContentForTest(build!, [activeTask], events, activeTask)).toContain(
      "**b1:** Inspecting GameLoop.gd and EcsStore.gd.",
    );
    expect(blueprintCardContentForTest(build!, [activeTask], events, activeTask)).toContain(
      "**b2:** Checking SpatialHash.gd neighbor queries.",
    );
  });

  it("keeps validation tool progress off the completed Execute card", () => {
    const activeTask = task({
      task_id: "active-task",
      status: "running",
      latest_progress: "DAN is working",
      metadata: { active_run_id: "run-1" },
    });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_started",
        source_event_type: "live.generic_build.started",
        run_id: "run-1",
        task_id: "active-task",
        summary: "Editing the workspace.",
      },
      {
        type: "worker_started",
        source_event_type: "live.validation.started",
        run_id: "run-1",
        task_id: "active-task",
        summary: "Validation started.",
      },
      {
        type: "model_request",
        source_event_type: "model.requested",
        run_id: "run-1",
        task_id: "active-task",
        summary: "model.requested",
        payload: { model: "kimi-k2.6" },
      },
      {
        type: "tool_used",
        source_event_type: "tool.started",
        run_id: "run-1",
        task_id: "active-task",
        summary: "tool.started",
        payload: { tool_id: "git_log" },
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "run-1",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
    });
    const build = nodes.find((node) => node.kind === "build")!;
    const validation = nodes.find((node) => node.kind === "validation")!;
    const buildContent = blueprintCardContentForTest(build, [activeTask], events, activeTask);
    const validationContent = blueprintCardContentForTest(validation, [activeTask], events, activeTask);

    expect(build.status).toBe("done");
    expect(validation.status).toBe("active");
    expect(buildContent).toBe("Execution handed off to validation for the current frontier.");
    expect(buildContent).not.toContain("Using git log.");
    expect(validationContent).toContain("Using git log.");
  });

  it("highlights Codex as the active Work card agent", () => {
    const activeTask = task({
      task_id: "codex-task",
      status: "running",
      latest_progress: "Codex is working.",
      metadata: {
        active_run_id: "run-codex",
        selected_agent: "codex",
        selected_backend: "codex",
      },
    });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_started",
        source_event_type: "live.generic_build.started",
        run_id: "run-codex",
        task_id: "codex-task",
        summary: "Codex thread started.",
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "run-codex",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
    });
    const build = nodes.find((node) => node.kind === "build")!;
    const content = blueprintCardContentForTest(build, [activeTask], events, activeTask);

    expect(content).toContain("`Codex` is working.");
    expect(content).toContain("`Codex` thread started.");
  });

  it("shows native active Work card agent as DAN", () => {
    const activeTask = task({
      task_id: "native-task",
      status: "running",
      latest_progress: "",
      metadata: {
        active_run_id: "run-native",
        selected_agent: "native",
        selected_backend: "super_dan",
      },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "run-native",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          run_id: "run-native",
          task_id: "native-task",
          summary: "Native run started.",
        },
      ],
    });
    const build = nodes.find((node) => node.kind === "build")!;

    expect(blueprintCardContentForTest(build, [activeTask], [], activeTask)).toContain("`DAN` is working");
  });

  it("renders done and future Work card content as state-specific summaries", () => {
    const activeTask = task({
      task_id: "active-task",
      status: "running",
      metadata: { active_run_id: "run-1" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "run-1",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          run_id: "run-1",
          task_id: "active-task",
          summary: "Working.",
        },
      ],
    });
    const build = nodes.find((node) => node.kind === "build")!;
    const doneNode = {
      ...build,
      status: "done" as const,
      detail: "old subtitle should not be needed",
      body: "Confirmed the sparse-slot batching bug and summarized the candidate repair.",
    };
    const futureNode = {
      ...build,
      status: "future" as const,
      detail: "old subtitle should not be needed",
      body: "Apply the slot-mapping patch, then run focused Godot validation.",
    };

    expect(blueprintCardContentForTest(doneNode, [], [], null)).toBe(
      "Confirmed the sparse-slot batching bug and summarized the candidate repair.",
    );
    expect(blueprintCardContentForTest(futureNode, [], [], null)).toBe(
      "Apply the slot-mapping patch, then run focused Godot validation.",
    );
  });

  it("uses a Notes-specific composer placeholder outside active runs", () => {
    expect(
      workspaceComposerPlaceholderForTest({
        hasActiveRun: false,
        placement: "steer",
        workspaceMode: "notes",
        selectedBlueprintTitle: "Final response",
        activeNoteTitle: "Knowledge Graph",
      }),
    ).toBe("Ask Super DAN about Knowledge Graph");
  });

  it("detects workspace composer shortcut tokens without treating prices or env vars as skills", () => {
    expect(workspaceComposerTokenForTest("/")).toMatchObject({ trigger: "/", query: "" });
    expect(workspaceComposerTokenForTest("$")).toMatchObject({ trigger: "$", query: "" });
    expect(workspaceComposerTokenForTest("review @docs")).toMatchObject({
      trigger: "@",
      query: "docs",
    });
    expect(workspaceComposerTokenForTest("cost $5")).toBeNull();
    expect(workspaceComposerTokenForTest("$PATH")).toBeNull();
  });

  it("keeps workspace composer drafts scoped to their session", () => {
    const first = { id: "thread-a", workflowId: "_scratch" };
    const second = { id: "thread-b", workflowId: "_scratch" };
    let drafts: Record<string, string> = {};

    drafts = composerDraftsWithValueForTest(drafts, first, "finish the site copy");
    drafts = composerDraftsWithValueForTest(drafts, second, "check the chart colors");

    expect(composerDraftForThreadForTest(drafts, first)).toBe("finish the site copy");
    expect(composerDraftForThreadForTest(drafts, second)).toBe("check the chart colors");
    expect(composerDraftForThreadForTest(drafts, null)).toBe("");

    drafts = composerDraftsWithValueForTest(drafts, first, "");

    expect(composerDraftForThreadForTest(drafts, first)).toBe("");
    expect(composerDraftForThreadForTest(drafts, second)).toBe("check the chart colors");
  });

  it("suggests slash commands, skills, and workspace files from the active token", () => {
    const skills = [
      {
        token: "idea-cart",
        name: "idea-cart",
        description: "Capture follow-up ideas",
        source_scope: "user",
      },
      {
        token: "scaffold-dev",
        name: "scaffold-dev",
        description: "Normalize project docs",
        source_scope: "project",
      },
    ];
    const files = [
      {
        path: "/repo/README.md",
        relative_path: "README.md",
        name: "README.md",
        parent: "",
        is_directory: false,
        size: 120,
        mtime: 1,
        depth: 0,
      },
      {
        path: "/repo/docs/todo.md",
        relative_path: "docs/todo.md",
        name: "todo.md",
        parent: "docs",
        is_directory: false,
        size: 240,
        mtime: 1,
        depth: 1,
      },
    ];

    expect(workspaceComposerSuggestionsForTest("/st")[0]).toMatchObject({
      label: "/status",
      insertText: "/status",
    });
    expect(workspaceComposerSuggestionsForTest("$", { skills }).map((item) => item.label)).toEqual([
      "$idea-cart",
      "$scaffold-dev",
    ]);
    expect(workspaceComposerSuggestionsForTest("$dev", { skills })[0]).toMatchObject({
      label: "$scaffold-dev",
    });
    expect(workspaceComposerSuggestionsForTest("@READ", { files })[0]).toMatchObject({
      label: "@README.md",
    });
    expect(workspaceComposerSuggestionsForTest("review @docs", { files })[0]).toMatchObject({
      label: "@docs/todo.md",
    });
  });

  it("builds a scoped preview artifact list from output files and run artifacts", () => {
    const entries = [
      workspaceFile("src/app.tsx"),
      workspaceFile("docs/architecture.md"),
      workspaceFile("figures/model-fit.png", { mtime: 200 }),
      workspaceFile("website/index.html", { mtime: 150 }),
      workspaceFile("website/apps/chat-bot/index.html", { mtime: 210 }),
      workspaceFile("outputs/report.pdf", { mtime: 180 }),
      workspaceFile("outputs/summary.md", { mtime: 170 }),
    ];
    const artifacts = workspacePreviewArtifactsForTest({
      entries,
      root: "/tmp/workspace",
      tasks: [
        task({
          latest_artifact_refs: [{ path: "/tmp/workspace/exports/final-chart.svg" }],
          metadata: {
            surface_context: {
              mentioned_files: [
                { relative_path: "outputs/summary.md" },
                { path: "/tmp/workspace/docs/architecture.md" },
              ],
            },
          },
        }),
      ],
      events: [
        {
          type: "completed",
          artifact_refs: [{ path: "outputs/report.pdf" }],
        },
        {
          type: "tool.completed",
          source_event_type: "tool.completed",
          payload: {
            result: {
              changed: true,
              path: "website/index.html",
            },
          },
        },
      ],
      chunks: [
        {
          id: "chunk-1",
          kind: "chat" as const,
          title: "You",
          body: "Please inspect @figures/model-fit.png",
          status: "clean",
          meta: "",
          role: "user",
        },
      ],
      limit: 8,
    });

    expect(artifacts).toEqual([
      { path: "outputs/report.pdf", kind: "pdf", source: "artifact" },
      { path: "exports/final-chart.svg", kind: "image", source: "artifact" },
      { path: "website/index.html", kind: "html", source: "session" },
      { path: "outputs/summary.md", kind: "markdown", source: "session" },
      { path: "figures/model-fit.png", kind: "image", source: "session" },
      { path: "docs/architecture.md", kind: "markdown", source: "session" },
    ]);
    expect(artifacts.map((artifact) => artifact.path)).not.toContain("src/app.tsx");
    expect(artifacts.map((artifact) => artifact.path)).not.toContain(
      "website/apps/chat-bot/index.html",
    );
  });

  it("leaves the preview artifact list empty for a session with no artifacts or mentions", () => {
    const artifacts = workspacePreviewArtifactsForTest({
      entries: [
        workspaceFile("website/index.html"),
        workspaceFile("website/apps/chat-bot/index.html"),
        workspaceFile("outputs/report.pdf"),
      ],
      root: "/tmp/workspace",
      tasks: [],
      events: [],
      limit: 8,
    });

    expect(artifacts).toEqual([]);
  });

  it("builds an external browser URL for the current preview file", () => {
    const url = workspacePreviewOpenUrlForTest({
      path: "/tmp/workspace/website/index.html",
      relativePath: "website/index.html",
      root: "/tmp/workspace",
      href: "http://127.0.0.1:5173/workspace",
    });

    expect(url).toMatch(
      /^http:\/\/127\.0\.0\.1:5173\/api\/workspace-files\/preview\/[^/]+\/website\/index\.html$/,
    );
  });

  it("extracts selected skills and mentioned files for workspace composer submits", () => {
    const skills = [
      {
        token: "idea-cart",
        name: "idea-cart",
        description: "Capture follow-up ideas",
        source_scope: "user",
      },
      {
        token: "frontend-design",
        name: "frontend-design",
        description: "Polish UI",
        source_scope: "user",
      },
    ];
    const files = [
      {
        path: "/repo/src/app.tsx",
        relative_path: "src/app.tsx",
        name: "app.tsx",
        parent: "src",
        is_directory: false,
        size: 99,
        mtime: 1,
        depth: 1,
      },
      {
        path: "/repo/src",
        relative_path: "src",
        name: "src",
        parent: "",
        is_directory: true,
        size: 0,
        mtime: 1,
        depth: 0,
      },
    ];

    expect(
      workspaceSelectedSkillInvocationForTest(
        "$idea-cart $frontend-design build this from @src/app.tsx",
        skills,
      ),
    ).toEqual({
      selectedTokens: ["idea-cart", "frontend-design"],
      objective: "build this from @src/app.tsx",
    });
    expect(
      workspaceMentionedFilesFromTextForTest("review @src/app.tsx and @src", files).map(
        (entry) => entry.relative_path,
      ),
    ).toEqual(["src/app.tsx", "src"]);
  });

  it("adds Hugo notes workspace rules for Notes-pane agent runs", () => {
    const context = workspaceSurfaceContextForTest({
      workspaceMode: "notes",
      notesRoot: "/kb/content",
      note: {
        id: "note-graph",
        title: "Knowledge Graph",
        path: "/kb/content/notes/graph/index.md",
        source: "server",
        content:
          "---\n" +
          "title: Knowledge Graph\n" +
          "layout: graph\n" +
          "pageID: graph-home\n" +
          "tags: [knowledge]\n" +
          "categories: [notes]\n" +
          "draft: false\n" +
          "---\n\n" +
          "Body with @paper-a.",
        loaded: true,
        status: "dirty",
        updatedAt: 1,
        tags: [],
        categories: [],
        citations: ["paper-a"],
      },
    });

    expect(context.workspace_mode).toBe("notes");
    expect(context.active_note).toMatchObject({
      title: "Knowledge Graph",
      relative_path: "notes/graph/index.md",
      section: "notes",
      layout: "graph",
      pageID: "graph-home",
      dirty: true,
    });
    expect(context.notes_workspace).toMatchObject({
      kind: "hugo_notes",
      role: "primary_workspace",
      active: true,
      root: "/kb/content",
      active_note: expect.objectContaining({ pageID: "graph-home" }),
    });
    expect(context.notes_workspace.rules).toContain(
      "Treat the notes root as a Hugo content tree, not a scratch folder.",
    );
    expect(context.notes_workspace.write_policy).toContain("create or edit notes only");
  });

  it("adds screenshot attachments to workspace surface context", () => {
    const context = workspaceSurfaceContextForTest({
      attachments: [
        {
          id: "shot-1",
          kind: "figure",
          name: "Screenshot.png",
          mimeType: "image/png",
          path: "/tmp/dan/shot.png",
          size: 128,
          source: "clipboard",
        },
      ],
    });

    expect(context.attachment_count).toBe(1);
    expect(context.appended_attachments).toEqual([
      expect.objectContaining({
        id: "shot-1",
        kind: "figure",
        name: "Screenshot.png",
        local_path: "/tmp/dan/shot.png",
        mime_type: "image/png",
        size_bytes: 128,
        source: "clipboard",
      }),
    ]);
  });

  it("shows a Last Update metadata chip from file mtime when lastmod is absent", () => {
    const updatedAt = Date.UTC(2026, 6, 17, 8, 30);
    const items = noteMetaItemsForTest(
      {
        id: "note-mauto",
        title: "Turin Automobile Museum Guide",
        path: "/kb/content/blogs/travel-plans/italy-2026/guides/turin-automobile-museum/index.md",
        relativePath: "blogs/travel-plans/italy-2026/guides/turin-automobile-museum/index.md",
        source: "server",
        content: "",
        loaded: true,
        status: "clean",
        updatedAt,
        tags: [],
        categories: [],
        citations: [],
      },
      "---\n" +
        "title: Turin Automobile Museum Guide\n" +
        "date: 2026-06-21\n" +
        "author: Adam\n" +
        "---\n\n" +
        "Opening hours and ticket notes.",
    );

    const expected = new Date(updatedAt).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
    expect(Object.fromEntries(items)).toMatchObject({
      Date: expect.any(String),
      "Last Update": expected,
      Author: "Adam",
    });
  });

  it("prefers Hugo lastmod over file mtime for the Last Update metadata chip", () => {
    const items = noteMetaItemsForTest(
      {
        id: "note-mauto",
        title: "Turin Automobile Museum Guide",
        source: "server",
        content: "",
        loaded: true,
        status: "clean",
        updatedAt: Date.UTC(2026, 6, 17, 8, 30),
        tags: [],
        categories: [],
        citations: [],
      },
      "---\n" +
        "title: Turin Automobile Museum Guide\n" +
        "lastmod: 2026-07-18\n" +
        "---\n\n" +
        "Opening hours and ticket notes.",
    );

    expect(Object.fromEntries(items)["Last Update"]).toBe(
      new Date("2026-07-18").toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      }),
    );
  });

  it("limits recent modified Notes pages to the five newest mtimes", () => {
    const notes = Array.from({ length: 6 }, (_, index) => ({
      id: `note-${index + 1}`,
      title: `Note ${index + 1}`,
      relativePath: `blogs/note-${index + 1}/index.md`,
      source: "server" as const,
      content: "",
      loaded: true,
      status: "clean" as const,
      updatedAt: Date.UTC(2026, 5, index + 1, 12, 0),
      tags: [],
      categories: [],
      citations: [],
    }));

    const recent = recentModifiedNotesForTest(notes, "/kb/content");

    expect(recent.map((item) => item.id)).toEqual([
      "note-6",
      "note-5",
      "note-4",
      "note-3",
      "note-2",
    ]);
    expect(recent[0]).toMatchObject({
      path: "blogs/note-6/index.md",
      section: "blogs",
    });
  });

  it("builds a Notes working card with the edited target and body chunk", () => {
    const cards = workingNoteCardsForTest(
      {
        id: "note-mauto",
        title: "Turin Automobile Museum Guide",
        relativePath: "blogs/travel-plans/italy-2026/guides/turin-automobile-museum/index.md",
        source: "server",
        content:
          "---\n" +
          "title: Turin Automobile Museum Guide\n" +
          "pageID: blogs-travel-italy-2026-guide-turin-automobile-museum\n" +
          "---\n\n" +
          "## Tickets\n" +
          "Opening hours, ticket prices, and booking notes for MAUTO.",
        loaded: true,
        status: "dirty",
        updatedAt: Date.UTC(2026, 5, 28, 12, 0),
        tags: [],
        categories: [],
        citations: [],
      },
      "/kb/content",
    );

    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({
      state: "Editing",
      target: "body",
      path: "blogs/travel-plans/italy-2026/guides/turin-automobile-museum/index.md",
    });
    expect(cards[0].snippet).toContain("Tickets Opening hours");
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
        workspace_id: "old-workspace-id",
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

  it("canonicalizes the old Dropbox project root to the local project root", () => {
    const workspaceTask = task({
      metadata: {
        workspace_id: "ws-1779357375714-zeqg2h",
        workspace_root: "/Volumes/data/Dropbox/Projects/deep-agent-network",
      },
    });

    expect(workspaceRootForTasksForTest([workspaceTask])).toBe(
      "/Users/lizhi/Downloads/local_projects/deep-agent-network",
    );
    expect(
      workspaceIdForTasksForTest([workspaceTask], [
        {
          id: "deep-agent-network",
          pinnedPaths: ["/Users/lizhi/Downloads/local_projects/deep-agent-network"],
        },
      ]),
    ).toBe("deep-agent-network");
  });

  it("groups restart-restored scratch sessions by saved workspace root", () => {
    const groups = buildSessionGroupsForTest({
      threads: [
        thread({
          id: "thread-restored",
          title: "Understanding project",
          workflow_id: "_scratch",
        }),
      ],
      workspaces: [],
      threadQuery: "",
      threadWorkspaces: {},
      taskWorkspaceByThreadId: new Map(),
      taskWorkspaceRootByThreadId: new Map([
        ["thread-restored", "/Users/lizhi/Downloads/local_projects/ra-neo"],
      ]),
    });

    expect(groups[0]).toMatchObject({
      id: "project-root:/Users/lizhi/Downloads/local_projects/ra-neo",
      name: "ra-neo",
      root: "/Users/lizhi/Downloads/local_projects/ra-neo",
    });
    expect(groups[0]?.threads.map((item) => item.id)).toEqual(["thread-restored"]);
  });

  it("keeps a mixed-history session anchored to its dominant workspace", () => {
    const raNeoFirst = task({
      task_id: "task-ra-first",
      metadata: {
        workspace_id: "ra-neo",
        workspace_root: "/Users/lizhi/Downloads/local_projects/ra-neo",
        run_created_at: "2026-06-24T10:00:00.000Z",
        run_updated_at: "2026-06-24T10:05:00.000Z",
      },
    });
    const raNeoSecond = task({
      task_id: "task-ra-second",
      metadata: {
        workspace_id: "ra-neo",
        workspace_root: "/Users/lizhi/Downloads/local_projects/ra-neo",
        run_created_at: "2026-06-24T11:00:00.000Z",
        run_updated_at: "2026-06-24T11:05:00.000Z",
      },
    });
    const accidentalCurrentWorkspace = task({
      task_id: "task-current-root",
      metadata: {
        workspace_id: "deep-agent-network",
        workspace_root: "/Users/lizhi/Downloads/local_projects/deep-agent-network",
        run_created_at: "2026-06-24T12:00:00.000Z",
        run_updated_at: "2026-06-24T12:05:00.000Z",
      },
    });

    expect(
      workspaceIdForTasksForTest([raNeoFirst, raNeoSecond, accidentalCurrentWorkspace], [
        { id: "scratch", pinnedPaths: ["/Users/lizhi/Downloads/local_projects/scratch"] },
        { id: "ra-neo", pinnedPaths: ["/Users/lizhi/Downloads/local_projects/ra-neo"] },
        {
          id: "deep-agent-network",
          pinnedPaths: ["/Users/lizhi/Downloads/local_projects/deep-agent-network"],
        },
      ]),
    ).toBe("ra-neo");
    expect(
      workspaceRootForTasksForTest([raNeoFirst, raNeoSecond, accidentalCurrentWorkspace]),
    ).toBe("/Users/lizhi/Downloads/local_projects/ra-neo");
  });

  it("lets task workspace evidence override a stale local thread binding", () => {
    const groups = buildSessionGroupsForTest({
      threads: [
        thread({
          id: "thread-ra",
          title: "Understanding this project",
          workflow_id: "_scratch",
        }),
      ],
      workspaces: [
        {
          id: "deep-agent-network",
          name: "deep-agent-network",
          pinnedPaths: ["/Users/lizhi/Downloads/local_projects/deep-agent-network"],
          activeThreadId: null,
          openThreadIds: ["thread-ra"],
        },
      ],
      threadQuery: "",
      threadWorkspaces: {
        "_scratch:thread-ra": "deep-agent-network",
      },
      taskWorkspaceByThreadId: new Map(),
      taskWorkspaceRootByThreadId: new Map([
        ["thread-ra", "/Users/lizhi/Downloads/local_projects/ra-neo"],
      ]),
    });

    const raNeoGroup = groups.find(
      (group) => group.id === "project-root:/Users/lizhi/Downloads/local_projects/ra-neo",
    );
    expect(raNeoGroup).toMatchObject({
      id: "project-root:/Users/lizhi/Downloads/local_projects/ra-neo",
      name: "ra-neo",
      root: "/Users/lizhi/Downloads/local_projects/ra-neo",
    });
    expect(raNeoGroup?.threads.map((item) => item.id)).toEqual(["thread-ra"]);
    expect(
      groups.find((group) => group.id === "workspace:deep-agent-network")?.threads ?? [],
    ).toEqual([]);
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

  it("keeps newer unmatched user turns visible after the latest paired request", () => {
    const chunks = conversationUserChunksForTest([
      {
        id: "message:user-old",
        kind: "chat",
        title: "You · Request",
        body: "old request",
        status: "clean",
        meta: "user",
        role: "user",
      },
      {
        id: "message:user-try-again",
        kind: "chat",
        title: "You · Request",
        body: "try again",
        status: "clean",
        meta: "user",
        role: "user",
      },
    ]);
    const nodes = [
      {
        id: "blueprint:old-request",
        title: "Operator request",
        detail: "Request captured",
        meta: "input",
        body: "Ready for request understanding.",
        rawRequest: "old request",
        status: "done" as const,
        kind: "request" as const,
      },
    ];

    expect(blueprintTimelineItemsForTest(nodes, chunks)).toEqual([
      {
        kind: "conversation",
        id: "conversation:message:user-old:before:blueprint:old-request",
        chunkId: "message:user-old",
        body: "old request",
      },
      {
        kind: "node",
        id: "node:blueprint:old-request",
        nodeId: "blueprint:old-request",
        title: "Operator request",
      },
      {
        kind: "conversation",
        id: "conversation:message:user-try-again:unmatched",
        chunkId: "message:user-try-again",
        body: "try again",
      },
    ]);
  });

  it("only treats an actually active blueprint node as the scroll anchor", () => {
    const readyNode = {
      id: "blueprint:ready",
      title: "Understand request",
      detail: "Waiting",
      meta: "request",
      body: "Waiting for work.",
      status: "ready" as const,
      kind: "understanding" as const,
    };
    const doneNode = {
      id: "blueprint:done",
      title: "Final response",
      detail: "Done",
      meta: "answer",
      body: "Finished.",
      status: "done" as const,
      kind: "answer" as const,
    };
    const activeNode = {
      id: "blueprint:active",
      title: "Execute workspace change",
      detail: "Working",
      meta: "workspace lane",
      body: "Working.",
      status: "active" as const,
      kind: "build" as const,
    };

    expect(blueprintAnchorNodeForTest([doneNode, readyNode])).toBeNull();
    expect(blueprintAnchorNodeForTest([doneNode, activeNode, readyNode])?.id).toBe(
      "blueprint:active",
    );
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

  it("merges selected-thread background tasks into the Work pane task list", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-25T12:06:30.000Z"));
    try {
      const selectedCompleted = task({
        task_id: "done-task",
        thread_id: "thread-1",
        status: "completed",
        metadata: {
          active_run_id: "done-run",
          run_updated_at: "2026-06-25T12:04:00.000Z",
        },
      });
      const backgroundRunning = task({
        task_id: "running-task",
        thread_id: "thread-1",
        status: "running",
        metadata: {
          active_run_id: "running-run",
          run_updated_at: "2026-06-25T12:05:00.000Z",
        },
      });
      const otherThreadRunning = task({
        task_id: "other-running-task",
        thread_id: "thread-2",
        status: "running",
        metadata: {
          active_run_id: "other-running-run",
          run_updated_at: "2026-06-25T12:06:00.000Z",
        },
      });

      const visibleTasks = workPanelTasksForTest(
        [selectedCompleted],
        [backgroundRunning, otherThreadRunning],
        "thread-1",
      );

      expect(visibleTasks.map((item) => item.task_id).sort()).toEqual([
        "done-task",
        "running-task",
      ]);
      expect(selectActiveRunningTaskForTest(visibleTasks)?.task_id).toBe("running-task");
    } finally {
      vi.useRealTimers();
    }
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

  it("keeps the future workflow tail visible without forcing a planning card while DAN is still understanding the request", () => {
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
    expect(nodes.find((node) => node.kind === "build")).toMatchObject({
      status: "future",
    });
    expect(nodes.find((node) => node.kind === "validation")).toMatchObject({
      status: "future",
    });
    expect(nodes.find((node) => node.kind === "answer")).toMatchObject({
      status: "future",
    });

    expect(blueprintTimelineItemsForTest(nodes, []).map((item) => item.id)).toEqual([
      "node:blueprint:message:user-understanding-first",
      "node:blueprint:understanding",
    ]);
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

  it("does not repeat the same live status line as now and latest update", () => {
    const activeTask = task({
      task_id: "dup-task",
      status: "running",
      latest_progress: "Inspecting files before deciding the next patch.",
      metadata: { active_run_id: "dup-run" },
    });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_started",
        source_event_type: "live.generic_build.started",
        run_id: "dup-run",
        task_id: "dup-task",
        summary: "Working in this workspace.",
      },
      {
        type: "worker_progress",
        source_event_type: "chat_v2.backend.codex.item.completed",
        run_id: "dup-run",
        task_id: "dup-task",
        summary: "Inspecting files before deciding the next patch.",
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "dup-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
    });

    const build = nodes.find((node) => node.kind === "build");
    expect(build).toBeTruthy();
    const status = blueprintLiveStatusForTest(build!, [activeTask], events, activeTask);
    expect(status.now).toContain("Inspecting files before deciding the next patch.");
    expect(status.latestUpdate).toBe("");
    expect(status.recentUpdates).not.toContain("Inspecting files before deciding the next patch.");
  });

  it("summarizes raw Codex shell-command progress before showing it in live status", () => {
    const activeTask = task({
      task_id: "codex-preview-task",
      status: "running",
      latest_progress: "Codex ran `/bin/zsh -lc 'export CODEX_HOME=\"${CODEX_HOME:-$HOME/.codex}\"; export PWCLI=\"$CODEX_HOME/skills/playwright/scripts/playwrightcli.sh\"; \"$PWCLI\" screenshot output/playwright/dan-website.png'`.",
      metadata: { active_run_id: "codex-preview-run", selected_backend: "codex" },
    });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "tool_used",
        source_event_type: "item.completed",
        run_id: "codex-preview-run",
        task_id: "codex-preview-task",
        summary: "Codex ran `/bin/zsh -lc 'export CODEX_HOME=\"${CODEX_HOME:-$HOME/.codex}\"; export PWCLI=\"$CODEX_HOME/skills/playwright/scripts/playwrightcli.sh\"; \"$PWCLI\" open \"file:///tmp/website/index.html\"'`.",
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "codex-preview-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
    });

    const build = nodes.find((node) => node.kind === "build");
    expect(build).toBeTruthy();
    const status = blueprintLiveStatusForTest(build!, [activeTask], events, activeTask);
    const allStatusText = [status.now, status.latestUpdate, ...status.recentUpdates].join(" ");
    expect(allStatusText).toContain("Codex checked the local preview.");
    expect(allStatusText).not.toContain("/bin/zsh");
    expect(allStatusText).not.toContain("playwrightcli.sh");
  });

  it("keeps Codex observable graph telemetry out of live preview updates", () => {
    const activeTask = task({
      task_id: "trace-task",
      status: "running",
      latest_progress: "Checking the current workspace.",
      metadata: { active_run_id: "trace-run", selected_backend: "codex" },
    });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_progress",
        source_event_type: "live.task_graph.updated",
        run_id: "trace-run",
        task_id: "trace-task",
        payload: {
          task_graph_state: {
            source: "codex",
            update_scope: "observable_event",
            version_id: "codex.r35",
            tasks: [
              {
                task_id: "codex-item-1",
                goal: "Run a workspace command",
                status: "active",
              },
            ],
          },
        },
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "trace-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
    });

    const build = nodes.find((node) => node.kind === "build");
    expect(build).toBeTruthy();
    const status = blueprintLiveStatusForTest(build!, [activeTask], events, activeTask);
    expect(`${status.latestUpdate} ${status.recentUpdates.join(" ")}`).not.toContain(
      "Task graph updated",
    );
    expect(status.now).toContain("Checking the current workspace.");
  });

  it("surfaces context composer and worker evidence in preview evidence items", () => {
    const activeTask = task({
      task_id: "evidence-task",
      status: "running",
      latest_progress: "Checking the selected plan.",
      metadata: {
        active_run_id: "evidence-run",
        context_composer: {
          shared_evidence: {
            items: [
              {
                id: "ev-1",
                kind: "selected_card",
                status: "active",
                title: "Selected card: Plan 3",
                summary: "Dependent plan is generating.",
                source: "gui_selection",
                ref: "plan-3",
                created_at: "2026-07-04T09:30:00Z",
              },
            ],
          },
        },
      },
    });
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_started",
        source_event_type: "live.generic_build.started",
        run_id: "evidence-run",
        task_id: "evidence-task",
        summary: "Checking the selected plan.",
      },
      {
        type: "worker_progress",
        source_event_type: "context.capsule.emitted",
        run_id: "evidence-run",
        task_id: "evidence-task",
        payload: {
          capsules: [
            {
              capsule_id: "capsule-1",
              kind: "file_scan",
              summary: "README confirms the current milestone.",
              raw_refs: [{ path: "README.md" }],
              created_at: "2026-07-04T09:31:00Z",
            },
          ],
        },
      },
    ];
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "evidence-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: events,
      chunks: [
        {
          id: "message:evidence-request",
          kind: "chat",
          title: "You · Request",
          body: "continue the selected plan",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "evidence-run",
          taskId: "evidence-task",
        },
      ],
    });

    const build = nodes.find((node) => node.kind === "build");
    expect(build).toBeTruthy();
    const evidence = sharedEvidenceItemsForTest(build!, [activeTask], events, activeTask);
    expect(evidence.map((item) => item.title)).toContain("Selected card: Plan 3");
    expect(evidence.map((item) => item.summary)).toContain("README confirms the current milestone.");
    expect(evidence.find((item) => item.title === "Selected card: Plan 3")?.timestamp).toBe(
      "2026-07-04T09:30:00Z",
    );
    expect(evidence.find((item) => item.summary === "README confirms the current milestone.")?.timestamp).toBe(
      "2026-07-04T09:31:00Z",
    );
  });

  it("keeps graph revision telemetry out of task progress card text", () => {
    const activeTask = task({
      task_id: "trace-progress-task",
      status: "running",
      latest_progress: "Task graph updated to codex.r42 by codex.",
      metadata: { active_run_id: "trace-progress-run", selected_backend: "codex" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "trace-progress-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          run_id: "trace-progress-run",
          task_id: "trace-progress-task",
          summary: "Working in this workspace.",
        },
      ],
    });

    const build = nodes.find((node) => node.kind === "build");
    expect(build).toBeTruthy();
    const events: ChatV2AgentRunEvent[] = [
      {
        type: "worker_started",
        source_event_type: "live.generic_build.started",
        run_id: "trace-progress-run",
        task_id: "trace-progress-task",
        summary: "Working in this workspace.",
      },
    ];
    const status = blueprintLiveStatusForTest(build!, [activeTask], events, activeTask);
    const card = blueprintCardContentForTest(build!, [activeTask], events, activeTask);
    expect(status.now).toBe("Codex is working");
    expect(card).not.toContain("Task graph updated");
    expect(card).toContain("`Codex` is working");
  });

  it("does not show raw output chunk parser errors as plan status", () => {
    const blockedTask = task({
      task_id: "parser-task",
      status: "failed",
      latest_progress: "Separator is not found, and chunk exceed the limit",
      metadata: { active_run_id: "parser-run", selected_backend: "codex" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "parser-run",
      tasks: [blockedTask],
      agentEvents: [],
    });

    const build = nodes.find((node) => node.id === "blueprint:build");
    expect(build).toBeTruthy();
    const status = blueprintLiveStatusForTest(build!, [blockedTask], [], blockedTask);
    const rendered = [status.now, status.latestUpdate, build?.body, build?.previewBody].join("\n");
    expect(status.now).toBe("Codex needs attention");
    expect(rendered).toContain("Codex hit an output-size parsing limit while reading command output.");
    expect(rendered).not.toContain("Separator is not found");
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

  it("routes flattened markdown status evidence through rich markdown", () => {
    const flattened =
      "### Summary - **First:** nothing was changed. ## What I inspected - **`website/styles.css`** defines `--font-display`. ```css :root { --font-display: \"Avenir Next\"; } ``` ### Why this matters - **Body:** Avenir Next is too polished.";

    expect(statusLineUsesMarkdownForTest(flattened)).toBe(true);

    const normalized = statusMarkdownContentForTest(flattened);
    expect(normalized).toContain("### Summary\n\n- **First:** nothing was changed.");
    expect(normalized).toContain("## What I inspected\n\n- **`website/styles.css`**");
    expect(normalized).toContain("```css\n:root { --font-display");
    expect(normalized).toContain("```\n\n### Why this matters");
  });

  it("keeps copied internal context dumps out of the full preview body", () => {
    const leaked =
      "### Summary\n\nReady-to-paste implementation.\n\n" +
      "Selected plan/card context:\n" +
      '- selected_card: {"detail":"run.log.completed","id":"agent-answer:run-1"}\n' +
      '- selected_chunk: {"preview":"### Summary - copied answer"}\n' +
      '- active_note: {"dirty":false,"path":"notes/index.md"}';

    const preview = previewMarkdownContentForTest(leaked);

    expect(preview).toContain("Ready-to-paste implementation.");
    expect(preview).not.toContain("selected_card");
    expect(preview).not.toContain("selected_chunk");
    expect(preview).not.toContain("active_note");
  });

  it("renders Hugo callout shortcodes as readable Notes callouts", () => {
    const normalized = formatHugoPreviewBodyForTest(
      '{{< callout warning "Critical Caveat" >}}\n' +
        'Failure rates are extreme for "AI wrapper" products.\n' +
        "{{< /callout >}}",
    );
    const html = renderMarkdownToHtml(normalized);

    expect(normalized).toContain("dan-markdown-callout");
    expect(normalized).not.toContain("callout warning");
    expect(normalized).not.toContain("/callout");
    expect(html).toContain("<aside");
    expect(html).toContain("data-callout-kind=\"warning\"");
    expect(html).toContain("dan-markdown-callout-marker");
    expect(html).toContain("Warning: Critical Caveat</span>");
    expect(html).toContain("Failure rates are extreme for &quot;AI wrapper&quot; products.");
    expect(html).not.toContain("dan-markdown-inline-code");
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

  it("defaults learn-and-suggest-improvements wording to read-only without edit authorization", () => {
    const prompt = "please learn this website, then let me know what to improve";
    const classification = workspaceIntentClassificationForTest(prompt);
    expect(classification).toMatchObject({
      assessmentOnly: false,
      requestsMutation: false,
      hasRequestPlan: false,
    });
    expect(workspaceIntentClassificationForTest("please improve the website")).toMatchObject({
      assessmentOnly: false,
      requestsMutation: false,
      hasRequestPlan: false,
    });
    expect(workspaceIntentClassificationForTest("please learn this website and improve it")).toMatchObject({
      assessmentOnly: false,
      requestsMutation: false,
      hasRequestPlan: false,
    });
    expect(workspaceIntentClassificationForTest("please edit the website")).toMatchObject({
      assessmentOnly: false,
      requestsMutation: true,
    });
    expect(workspaceIntentClassificationForTest("please learn this website and then update styles.css")).toMatchObject({
      assessmentOnly: false,
      requestsMutation: true,
    });

    const activeTask = task({
      task_id: "website-learn-task",
      status: "running",
      latest_progress: "Inspecting the website.",
      metadata: { active_run_id: "website-learn-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "website-learn-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      chunks: [
        {
          id: "message:user-website-learn",
          kind: "chat",
          title: "You · Request",
          body: prompt,
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.generic_build.started",
          run_id: "website-learn-run",
          task_id: "website-learn-task",
          summary: "Working in this workspace.",
        },
      ],
    });

    expect(nodes.find((node) => node.id === "blueprint:planning")).toBeUndefined();
    expect(nodes.find((node) => node.id === "blueprint:build")).toMatchObject({
      title: "Prepare direct response",
      detail: "Read-only response; no file edits expected",
      body: "Read-only response; no file edits expected",
    });
    expect(nodes.find((node) => node.kind === "request")?.previewBody).toContain(
      "Workspace file mutation is not expected for this request.",
    );
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

  it("does not start duplicate auto-restore while session history is loading", () => {
    expect(
      shouldAutoRestoreSessionForTest({
        activeThreadPresent: false,
        creatingSession: false,
        loadingThreadId: "previous-thread",
        targetThreadId: "previous-thread",
        threadCount: 3,
      }),
    ).toBe(false);
    expect(
      shouldAutoRestoreSessionForTest({
        activeThreadPresent: false,
        creatingSession: false,
        restoringThreadId: "previous-thread",
        targetThreadId: "previous-thread",
        threadCount: 3,
      }),
    ).toBe(false);
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
      taskWorkspaceRootByThreadId: new Map(),
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

  it("lets a fresher completed background snapshot clear stale selected running state", () => {
    const background = task({
      task_id: "same-task",
      thread_id: "selected-thread",
      status: "completed",
      latest_progress: "Finished with a useful answer",
      metadata: {
        active_run_id: "selected-run",
        run_updated_at: "2026-06-29T12:10:00.000Z",
      },
    });
    const selected = task({
      task_id: "same-task",
      thread_id: "selected-thread",
      status: "running",
      latest_progress: "Older selected running state",
      metadata: {
        active_run_id: "selected-run",
        run_updated_at: "2026-06-29T12:00:00.000Z",
      },
    });

    const merged = sessionStatusTasksForTest([selected], [background]);

    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({
      status: "completed",
      latest_progress: "Finished with a useful answer",
    });
    expect(sessionProgressTaskForTest(merged, "selected-thread")).toBeNull();
    expect(sessionCardDisplayForTest(thread({}), merged).detail).toContain("1 run · done");
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
    const stale = task({
      task_id: "stale-task",
      thread_id: "stale-thread",
      status: "running",
      latest_progress: "Old saved work",
      metadata: {
        active_run_id: "stale-run",
        run_updated_at: "2020-01-01T00:00:00.000Z",
      },
    });

    expect(sessionProgressTaskForTest([queued, running, completed, stale], "running-thread")).toMatchObject({
      task_id: "running-task",
    });
    expect(sessionProgressTaskForTest([queued, running, completed, stale], "queued-thread")).toBeNull();
    expect(sessionProgressTaskForTest([queued, running, completed, stale], "completed-thread")).toBeNull();
    expect(sessionProgressTaskForTest([queued, running, completed, stale], "stale-thread")).toBeNull();
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

    const planNode = nodes.find((node) => node.id === "blueprint:planning");
    expect(nodes.map((node) => node.title)).toContain("Blueprint planning");
    expect(nodes.find((node) => node.id === "blueprint:task:1-1")).toBeUndefined();
    expect(nodes.find((node) => node.id === "blueprint:task:2-1")).toBeUndefined();
    expect(planTaskChecklistItemsForTest(planNode!)).toEqual([
      {
        taskId: "1-1",
        title: "Create app shell",
        status: "ready",
        branchId: "",
      },
      {
        taskId: "2-1",
        title: "Add persistence",
        status: "future",
        branchId: "",
      },
    ]);
    expect(planCardChecklistItemsForTest(planNode!)).toEqual([]);
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
    expect(planNode?.previewBody).toContain("Group 1: 2 tasks can run together");
    expect(planNode?.previewBody).toContain("Includes Create app shell; Add trip data.");
    expect(planNode?.previewBody).not.toContain("`1-1` + `1-2`");
    expect(planNode?.previewBody).toContain("Branch Refs");
    expect(planNode?.previewBody).toContain("1: 2 tasks, 1 ready, 1 done");
    expect(nodes.find((node) => node.id === "blueprint:task:1-1")).toBeUndefined();
    expect(nodes.find((node) => node.id === "blueprint:task:1-2")).toBeUndefined();
    expect(nodes.find((node) => node.id === "blueprint:task:2-1")).toBeUndefined();
    expect(planTaskChecklistItemsForTest(planNode!)).toEqual([
      {
        taskId: "1-1",
        title: "Create app shell",
        status: "done",
        branchId: "1",
      },
      {
        taskId: "1-2",
        title: "Add trip data",
        status: "ready",
        branchId: "1",
      },
      {
        taskId: "2-1",
        title: "Add persistence",
        status: "future",
        branchId: "2",
      },
    ]);
    expect(planCardChecklistItemsForTest(planNode!)).toEqual([]);

    const tree = liveTaskTreeForTest(nodes);
    expect(tree).toEqual([]);

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

  it("keeps observable Codex execution items out of the semantic task graph", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "codex-run",
      chunks: [
        {
          id: "message:user-codex-graph",
          kind: "chat",
          title: "You · Request",
          body: "fix the failing test",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "codex-run",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.task_graph.updated",
          summary: "Codex observable graph updated.",
          run_id: "codex-run",
          task_id: "codex-task",
          payload: {
            task_graph_state: {
              schema: "super_dan_task_graph_v1",
              revision: 2,
              version_id: "codex.r2",
              root_version_id: "codex",
              source: "codex",
              update_scope: "observable_event",
              update_reason: "Codex started an observable work item.",
              changed_task_ids: ["codex-shell-1"],
              changed_branch_ids: ["workspace"],
              tasks: [
                {
                  task_id: "codex-request",
                  branch_id: "request",
                  goal: "Receive request: fix the failing test",
                  state: "done",
                  depends_on: [],
                },
                {
                  task_id: "codex-shell-1",
                  branch_id: "workspace",
                  goal: "Run `npm test`",
                  state: "active",
                  depends_on: ["codex-request"],
                },
                {
                  task_id: "codex-final",
                  branch_id: "answer",
                  goal: "Return the final user-facing response",
                  state: "deferred",
                  depends_on: ["codex-shell-1"],
                },
              ],
              active_task_ids: ["codex-shell-1"],
              completed_task_ids: ["codex-request"],
              deferred_task_ids: ["codex-final"],
              parallel_groups: [["codex-request", "codex-shell-1", "codex-final"]],
              branches: [
                {
                  branch_id: "request",
                  task_ids: ["codex-request"],
                  completed_task_ids: ["codex-request"],
                },
                {
                  branch_id: "workspace",
                  task_ids: ["codex-shell-1"],
                  active_task_ids: ["codex-shell-1"],
                },
                {
                  branch_id: "answer",
                  task_ids: ["codex-final"],
                  deferred_task_ids: ["codex-final"],
                },
              ],
            },
          },
        },
      ],
    });

    const graphRevisions = liveTaskGraphRevisionsForTest(nodes);
    expect(graphRevisions).toEqual([]);
    expect(nodes.find((node) => node.id === "blueprint:planning")).toBeUndefined();
    expect(nodes.find((node) => node.graphTaskId === "codex-shell-1")).toBeUndefined();
  });

  it("settles stale active live graph nodes after a final response", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "codex-run",
      chunks: [
        {
          id: "message:user-codex-graph-done",
          kind: "chat",
          title: "You · Request",
          body: "fix the failing test",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "codex-run",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.task_graph.updated",
          summary: "Codex observable graph updated.",
          run_id: "codex-run",
          task_id: "codex-task",
          payload: {
            task_graph_state: {
              schema: "super_dan_task_graph_v1",
              revision: 2,
              version_id: "plan.r2",
              source: "planner",
              update_scope: "plan_execution",
              update_reason: "DAN started the current plan item.",
              changed_task_ids: ["plan-item-1"],
              changed_branch_ids: ["b1"],
              tasks: [
                {
                  task_id: "plan-item-0",
                  branch_id: "b1",
                  goal: "Understand the failing test",
                  state: "done",
                  depends_on: [],
                },
                {
                  task_id: "plan-item-1",
                  branch_id: "b1",
                  goal: "Fix the failing behavior",
                  state: "active",
                  depends_on: ["plan-item-0"],
                },
                {
                  task_id: "plan-item-2",
                  branch_id: "b1",
                  goal: "Summarize the result",
                  state: "deferred",
                  depends_on: ["plan-item-1"],
                },
              ],
              active_task_ids: ["plan-item-1"],
              completed_task_ids: ["plan-item-0"],
              deferred_task_ids: ["plan-item-2"],
            },
          },
        },
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "Codex completed",
          run_id: "codex-run",
          task_id: "codex-task",
          payload: {
            final_text: "The failing test is fixed and the run is complete.",
          },
        },
      ],
    });

    const graphRevisions = liveTaskGraphRevisionsForTest(nodes);
    expect(graphRevisions).toHaveLength(1);
    expect(graphRevisions[0]?.branches[0]?.nodes[1]).toMatchObject({
      title: "plan-item-1. Fix the failing behavior",
      status: "done",
    });
    expect(planTaskChecklistItemsForTest(nodes.find((node) => node.id === "blueprint:planning")!)).toEqual([
      {
        taskId: "plan-item-0",
        title: "Understand the failing test",
        status: "done",
        branchId: "b1",
      },
      {
        taskId: "plan-item-1",
        title: "Fix the failing behavior",
        status: "done",
        branchId: "b1",
      },
      {
        taskId: "plan-item-2",
        title: "Summarize the result",
        status: "future",
        branchId: "b1",
      },
    ]);
  });

  it("settles active plan checklist rows when a final response appears before the task snapshot catches up", () => {
    const runningTask = task({
      task_id: "plan-task",
      thread_id: "thread-1",
      status: "running",
      latest_progress: "DAN is still marked as running by the task snapshot.",
      metadata: { active_run_id: "plan-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      tasks: [runningTask],
      activeRunId: "plan-run",
      activeRunningTask: runningTask,
      chunks: [
        {
          id: "message:user-plan-final",
          kind: "chat",
          title: "You · Request",
          body: "locate the right old commits",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "plan-run",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.task_graph.updated",
          summary: "Plan graph updated.",
          run_id: "plan-run",
          task_id: "plan-task",
          payload: {
            task_graph_state: {
              schema: "super_dan_task_graph_v1",
              revision: 3,
              version_id: "plan.r3",
              source: "planner",
              update_scope: "plan_execution",
              update_reason: "DAN started locating old commits.",
              tasks: [
                {
                  task_id: "b1",
                  branch_id: "main",
                  goal: "Scan the git reflog",
                  state: "active",
                  depends_on: [],
                },
                {
                  task_id: "b2",
                  branch_id: "main",
                  goal: "Scan backup files",
                  state: "running",
                  depends_on: [],
                },
              ],
              active_task_ids: ["b1", "b2"],
              completed_task_ids: [],
            },
          },
        },
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "workspace check failed for website: FileNotFoundError: File not found: 'website'.",
          run_id: "plan-run",
          task_id: "plan-task",
          payload: {
            final_text: "workspace check failed for website: FileNotFoundError: File not found: 'website'.",
          },
        },
      ],
    });

    const planning = nodes.find((node) => node.id === "blueprint:planning");
    const final = nodes.find((node) => node.id === "blueprint:answer");

    expect(planning?.status).toBe("done");
    expect(final?.status).toBe("done");
    expect(planTaskChecklistItemsForTest(planning!)).toEqual([
      {
        taskId: "b1",
        title: "Scan the git reflog",
        status: "done",
        branchId: "main",
      },
      {
        taskId: "b2",
        title: "Scan backup files",
        status: "done",
        branchId: "main",
      },
    ]);
    expect(planCardChecklistItemsForTest(planning!)).toEqual([]);
  });

  it("renders explicit plan DAG nodes as Work cards with child task checklists", () => {
    const planGraphTasks = [
      {
        task_id: "plan-1",
        node_type: "plan",
        goal: "Plan 1",
        state: "executing",
        depends_on: [],
      },
      {
        task_id: "plan-2",
        node_type: "plan",
        goal: "Plan 2",
        state: "executing",
        depends_on: [],
      },
      {
        task_id: "plan-3",
        node_type: "plan",
        goal: "Plan 3",
        state: "generated",
        depends_on: ["plan-1", "plan-2"],
      },
      {
        task_id: "plan-4",
        node_type: "plan",
        goal: "Plan 4",
        state: "generated",
        depends_on: ["plan-3"],
      },
      {
        task_id: "plan-5",
        node_type: "plan",
        goal: "Plan 5",
        state: "generated",
        depends_on: ["plan-3"],
      },
      {
        task_id: "plan-6",
        node_type: "plan",
        goal: "Plan 6",
        state: "generated",
        depends_on: ["plan-4", "plan-5"],
      },
      {
        task_id: "plan-1-task-a",
        plan_id: "plan-1",
        branch_id: "implementation",
        goal: "Execute Plan 1 branch A",
        state: "active",
        depends_on: [],
      },
      {
        task_id: "plan-1-task-b",
        plan_id: "plan-1",
        branch_id: "validation",
        goal: "Validate Plan 1 branch B",
        state: "ready",
        depends_on: [],
      },
      {
        task_id: "plan-2-task-a",
        plan_id: "plan-2",
        branch_id: "implementation",
        goal: "Execute Plan 2 branch A",
        state: "active",
        depends_on: [],
      },
    ];

    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "plan-dag-run",
      activeThreadTitle: "Plan graph run",
      chunks: [
        {
          id: "message:user-plan-dag",
          kind: "chat",
          title: "You · Request",
          body: "Run the DAN Super plan graph",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "plan-dag-run",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.task_graph.updated",
          summary: "Plan graph generated.",
          run_id: "plan-dag-run",
          task_id: "plan-dag-task",
          payload: {
            task_graph_state: {
              schema: "super_dan_task_graph_v1",
              revision: 1,
              version_id: "plans.r1",
              root_version_id: "plans",
              source: "planner",
              update_scope: "plan_graph",
              update_reason: "Generated top-level plans with execution dependencies.",
              tasks: planGraphTasks,
              active_task_ids: ["plan-1", "plan-2", "plan-1-task-a", "plan-2-task-a"],
              ready_task_ids: ["plan-1-task-b"],
              deferred_task_ids: ["plan-3", "plan-4", "plan-5", "plan-6"],
              completed_task_ids: [],
              plan_generation_queue_length: 4,
              plan_execution_queue_length: 4,
              task_execution_queue_length: 4,
            },
          },
        },
      ],
    });

    const graphCards = nodes.filter((node) => node.graphTaskId);
    expect(graphCards.map((node) => node.graphTaskId)).toEqual([
      "plan-1",
      "plan-2",
      "plan-3",
      "plan-4",
      "plan-5",
      "plan-6",
    ]);
    expect(nodes.find((node) => node.graphTaskId === "plan-1-task-a")).toBeUndefined();
    expect(nodes.find((node) => node.graphTaskId === "plan-1")?.status).toBe("active");
    expect(nodes.find((node) => node.graphTaskId === "plan-3")).toMatchObject({
      status: "future",
      compact: true,
      kind: "plan",
    });

    const plan1 = nodes.find((node) => node.graphTaskId === "plan-1");
    expect(planTaskChecklistItemsForTest(plan1!)).toEqual([
      {
        taskId: "plan-1-task-a",
        title: "Execute Plan 1 branch A",
        status: "active",
        branchId: "implementation",
      },
      {
        taskId: "plan-1-task-b",
        title: "Validate Plan 1 branch B",
        status: "ready",
        branchId: "validation",
      },
    ]);
    expect(planCardChecklistItemsForTest(plan1!)).toEqual([
      {
        taskId: "plan-1-task-a",
        title: "Execute Plan 1 branch A",
        status: "active",
        branchId: "implementation",
      },
    ]);

    const graphRevisions = liveTaskGraphRevisionsForTest(nodes);
    expect(graphRevisions[0]?.planEdges).toEqual([
      { from: "blueprint:task:plan-1", to: "blueprint:task:plan-3" },
      { from: "blueprint:task:plan-2", to: "blueprint:task:plan-3" },
      { from: "blueprint:task:plan-3", to: "blueprint:task:plan-4" },
      { from: "blueprint:task:plan-3", to: "blueprint:task:plan-5" },
      { from: "blueprint:task:plan-4", to: "blueprint:task:plan-6" },
      { from: "blueprint:task:plan-5", to: "blueprint:task:plan-6" },
    ]);
    expect(graphRevisions[0]?.branches.flatMap((branch) => branch.nodes)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "blueprint:task:plan-3", kind: "plan", status: "future" }),
      ]),
    );
  });

  it("keeps future validation and final response visible during request understanding", () => {
    const activeTask = task({
      task_id: "understanding-only-task",
      status: "running",
      latest_progress: "Understanding request.",
      metadata: { active_run_id: "understanding-only-run" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "understanding-only-run",
      activeRunningTask: activeTask,
      tasks: [activeTask],
      chunks: [
        {
          id: "message:user-understanding-only",
          kind: "chat",
          title: "You · Request",
          body: "Implement the next Work Panel sequence polish",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "understanding-only-run",
        },
      ],
      agentEvents: [
        {
          type: "worker_started",
          source_event_type: "live.request_understanding.briefed",
          summary: "Request-understanding brief emitted.",
          task_id: "understanding-only-task",
          run_id: "understanding-only-run",
          payload: {
            request_understanding: {
              source: "rule_generation_brief",
              request_kind: "workspace_change",
              rule_generation_brief: ["Generate request-specific acceptance criteria."],
            },
          },
        },
      ],
    });

    expect(nodes.map((node) => node.title)).toEqual([
      "Operator request",
      "Understand request",
      "Execute workspace change",
      "Validate current frontier",
      "Final response",
    ]);
    expect(nodes.find((node) => node.title === "Understand request")?.status).toBe("active");
    expect(nodes.find((node) => node.title === "Blueprint planning")).toBeUndefined();
    expect(nodes.find((node) => node.title === "Validate current frontier")?.status).toBe("future");
    expect(nodes.find((node) => node.title === "Final response")?.status).toBe("future");
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
    expect(nodes.find((node) => node.id === "blueprint:planning")).toBeUndefined();
    expect(nodes.find((node) => node.id === "blueprint:understanding")).toMatchObject({
      status: "done",
      detail: "Request context handed into later work",
    });
    expect(nodes.find((node) => node.id === "blueprint:build")).toMatchObject({
      status: "active",
      title: "Prepare answer",
    });
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
    const planning = nodes.find((node) => node.id === "blueprint:planning");
    expect(nodes.find((node) => node.id === "blueprint:planning")).toMatchObject({
      detail: "3 projected tasks · 1 ready now",
      meta: "request target: /private/tmp/dan-blueprint-smoke/trip-planner",
    });
    expect(taskTitles).toEqual([]);
    expect(planTaskChecklistItemsForTest(planning!)).toEqual([
      {
        taskId: "1",
        title: "Understand explicit target scope",
        status: "ready",
        branchId: "",
      },
      {
        taskId: "2",
        title: "Execute the current target slice",
        status: "future",
        branchId: "",
      },
      {
        taskId: "3",
        title: "Validate and summarize target coverage",
        status: "future",
        branchId: "",
      },
    ]);
    expect(planCardChecklistItemsForTest(planning!)).toEqual([]);
    expect(taskTitles.join(" ")).not.toContain("packing checklist");
    expect(taskTitles.join(" ")).not.toContain("budget table");
    expect(nodes.find((node) => node.id === "blueprint:understanding")).toMatchObject({
      title: "Understand request",
      status: "done",
      detail: "Request context handed into later work",
    });
    expect(nodes.find((node) => node.id === "blueprint:task:1")).toBeUndefined();
    expect(nodes.find((node) => node.id === "blueprint:task:2")).toBeUndefined();
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

  it("settles stale running task state from a terminal final event before queue rendering", () => {
    const running = task({
      task_id: "finalized-task",
      thread_id: "thread-1",
      status: "running",
      latest_progress: "Still marked running in the saved task snapshot.",
      metadata: { active_run_id: "finalized-run" },
    });
    const settled = settleTaskSnapshotsFromEventsForTest([running], [
      {
        type: "completed",
        source_event_type: "turn.completed",
        run_id: "finalized-run",
        task_id: "finalized-task",
        summary: "I opened the site directly in Chrome.",
      },
    ]);

    expect(settled).toHaveLength(1);
    expect(settled[0]).toMatchObject({
      status: "completed",
      latest_progress: "I opened the site directly in Chrome.",
    });
    expect(selectActiveRunningTaskForTest(settled)).toBeNull();
    expect(queueRowsFromTasksForTest(settled)).toEqual([]);
    expect(sessionCardDisplayForTest(thread({}), settled).detail).toContain("1 run · done");
  });

  it("does not settle running task state from non-terminal stage completion events", () => {
    const running = task({
      task_id: "stage-task",
      thread_id: "thread-1",
      status: "running",
      latest_progress: "Working on the current stage.",
      metadata: { active_run_id: "stage-run" },
    });
    const settled = settleTaskSnapshotsFromEventsForTest([running], [
      {
        type: "completed",
        source_event_type: "live.generic_build.completed",
        run_id: "stage-run",
        task_id: "stage-task",
        summary: "Execution stage completed.",
      },
    ]);

    expect(settled[0]).toMatchObject({
      status: "running",
      latest_progress: "Working on the current stage.",
    });
    expect(selectActiveRunningTaskForTest(settled)?.task_id).toBe("stage-task");
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

  it("shows a polished queue intent instead of echoing a rough request verbatim", () => {
    const rough =
      "Oh I think you're right. But can you check the git history, and try to revert back when we had the dan super biology, and when we had the moving dots and networks, just check";

    expect(queueDisplayDetailForTest(rough)).toBe(
      "Check git history for earlier DAN Super Biology and moving dots/network states.",
    );
    expect(queueDisplayDetailForTest(rough)).not.toBe(rough);
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
      detail: "Summarize the project without editing files.",
      rawDetail: "wait, don't edit anything, summarize the project",
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

  it("keeps queued next messages from replacing the active request card", () => {
    const running = task({
      task_id: "running",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: {
        active_run_id: "active-run",
        queue_items: [
          {
            id: "next-1",
            task_id: "running",
            lane: "continue_after_current",
            status: "queued",
            text: "then add screenshot support",
            position: 1,
          },
        ],
      },
    });
    const chunks = [
      {
        id: "message:user-active",
        kind: "chat" as const,
        title: "You · Request",
        body: "build the workspace page",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
        runId: "active-run",
      },
      {
        id: "message:user-next",
        kind: "chat" as const,
        title: "You · Request",
        body: "then add screenshot support",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
      },
    ];
    const rows = queueRowsFromTasksForTest([running]);
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "active-run",
      activeRunningTask: running,
      tasks: [running],
      queueRows: rows,
      chunks,
    });

    expect(nodes.find((node) => node.title === "Operator request")).toMatchObject({
      rawRequest: "build the workspace page",
      runId: "active-run",
    });
    expect(nodes.find((node) => node.rawRequest === "then add screenshot support")).toMatchObject({
      title: "Follow-up request",
      detail: "Next message",
    });

    const timeline = blueprintTimelineItemsForTest(nodes, conversationUserChunksForTest(chunks));
    expect(timeline.map((item) => item.id)).toEqual([
      "conversation:message:user-active:before:blueprint:message:user-active",
      "node:blueprint:message:user-active",
      "node:blueprint:understanding",
      "conversation:message:user-next:before:blueprint:queue:next-1:request",
      "node:blueprint:queue:next-1:request",
    ]);
  });

  it("keeps queued steering messages out of synthetic pending-run groups", () => {
    const oldTask = task({
      task_id: "old-task",
      status: "completed",
      latest_progress: "Old run done.",
      metadata: { active_run_id: "old-run" },
    });
    const running = task({
      task_id: "running",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: {
        active_run_id: "active-run",
        queue_items: [
          {
            id: "steer-1",
            task_id: "running",
            lane: "append",
            status: "queued",
            text: "use the smaller card layout",
            position: 1,
          },
        ],
      },
    });
    const chunks = [
      {
        id: "message:user-old",
        kind: "chat" as const,
        title: "You · Request",
        body: "first request",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
        runId: "old-run",
      },
      {
        id: "agent-answer:old-run",
        kind: "agent" as const,
        title: "DAN · Answer",
        body: "Old run done.",
        status: "clean" as const,
        meta: "run.log.completed",
        runId: "old-run",
      },
      {
        id: "message:user-active",
        kind: "chat" as const,
        title: "You · Request",
        body: "continue the UI fix",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
        runId: "active-run",
      },
      {
        id: "message:user-steer",
        kind: "chat" as const,
        title: "You · Request",
        body: "use the smaller card layout",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
      },
    ];
    const rows = queueRowsFromTasksForTest([running]);
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "active-run",
      activeRunningTask: running,
      tasks: [oldTask, running],
      queueRows: rows,
      chunks,
      agentEvents: [
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "Old run done.",
          run_id: "old-run",
          task_id: "old-task",
        },
      ],
    });

    expect(nodes.some((node) => node.id.includes("pending-chunk:message:user-steer"))).toBe(false);
    expect(nodes.find((node) => node.rawRequest === "use the smaller card layout")).toMatchObject({
      title: "Follow-up request",
      detail: "Steering message",
    });

    const timelineIds = blueprintTimelineItemsForTest(
      nodes,
      conversationUserChunksForTest(chunks),
    ).map((item) => item.id);
    expect(timelineIds).toContain(
      "conversation:message:user-steer:before:blueprint:run:active-run:queue:steer-1:request",
    );
    expect(timelineIds).not.toContain("conversation:message:user-steer:unmatched");
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

  it("keeps a newly sent follow-up visible before the backend assigns a run id", () => {
    const oldTasks = [
      task({
        task_id: "old-task-1",
        status: "completed",
        latest_progress: "First run done.",
        metadata: { active_run_id: "old-run-1" },
      }),
      task({
        task_id: "old-task-2",
        status: "completed",
        latest_progress: "Second run done.",
        metadata: { active_run_id: "old-run-2" },
      }),
    ];
    const chunks = [
      {
        id: "message:user-old-1",
        kind: "chat" as const,
        title: "You · Request",
        body: "first request",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
        runId: "old-run-1",
      },
      {
        id: "message:user-old-2",
        kind: "chat" as const,
        title: "You · Request",
        body: "second request",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
        runId: "old-run-2",
      },
      {
        id: "message:user-pending-followup",
        kind: "chat" as const,
        title: "You · Request",
        body: "actually can you explain why it stopped",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
      },
    ];

    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      tasks: oldTasks,
      chunks,
      agentEvents: [
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "First run done.",
          run_id: "old-run-1",
          task_id: "old-task-1",
        },
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "Second run done.",
          run_id: "old-run-2",
          task_id: "old-task-2",
        },
      ],
    });

    expect(nodes.find((node) => node.rawRequest === "actually can you explain why it stopped")).toMatchObject({
      title: "Operator request",
      status: "done",
    });
    expect(
      blueprintTimelineItemsForTest(nodes, conversationUserChunksForTest(chunks))
        .map((item) => item.id)
        .some((id) => id.includes("message:user-pending-followup")),
    ).toBe(true);
  });

  it("orders pending user turns by chat sequence instead of after task-backed runs", () => {
    const oldTask = task({
      task_id: "old-task",
      status: "completed",
      latest_progress: "Old run done.",
      metadata: {
        active_run_id: "old-run",
        created_at: "2026-06-29T15:10:07.995591+00:00",
        updated_at: "2026-07-01T00:58:21.641539+00:00",
      },
    });
    const activeTask = task({
      task_id: "active-task",
      status: "running",
      latest_progress: "Thinking with kimi-k2.6.",
      metadata: {
        active_run_id: "active-run",
        created_at: "2026-07-01T01:05:55.365717+00:00",
        updated_at: "2026-07-01T01:07:07.025444+00:00",
      },
    });
    const chunks = [
      {
        id: "message:user-pending-old",
        kind: "chat" as const,
        title: "You · Request",
        body: "can you fix the DAn typo",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
      },
      {
        id: "message:user-active",
        kind: "chat" as const,
        title: "You · Request",
        body: "continue?",
        status: "clean" as const,
        meta: "user",
        role: "user" as const,
        runId: "active-run",
      },
    ];

    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      tasks: [activeTask, oldTask],
      chunks,
      activeRunId: "active-run",
      activeRunningTask: activeTask,
    });
    const pendingRequestIndex = nodes.findIndex(
      (node) => node.kind === "request" && node.rawRequest === "can you fix the DAn typo",
    );
    const activeRequestIndex = nodes.findIndex(
      (node) => node.kind === "request" && node.rawRequest === "continue?",
    );

    expect(pendingRequestIndex).toBeGreaterThanOrEqual(0);
    expect(activeRequestIndex).toBeGreaterThanOrEqual(0);
    expect(pendingRequestIndex).toBeLessThan(activeRequestIndex);
  });

  it("recovers old phone-session requests without queueing stop controls", () => {
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
    expect(rows).toEqual([]);
    expect(selectActiveRunningTaskForTest([stopped])).toBeNull();
    expect(nodes.find((node) => node.id === "blueprint:task:phone-task")).toBeUndefined();
    expect(nodes.find((node) => node.kind === "build")).toMatchObject({
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

  it("uses shared blocker evidence when failed task progress is generic", () => {
    const blockedTask = task({
      task_id: "workspace-check-task",
      status: "failed",
      latest_progress: "Super DAN needs attention.",
      metadata: {
        active_run_id: "workspace-check-run",
        last_surface_turn: {
          text: "try again to implement the font changes",
        },
      },
    });
    const blocker =
      "workspace_check failed: ValueError: workspace_check syntax=auto only supports .py, .json, .html, .htm, or files with a registered source-shape profile.";
    const blockedEvent: ChatV2AgentRunEvent = {
      type: "failed",
      source_event_type: "worker.context.capsule",
      summary: "Super DAN needs attention.",
      task_id: "workspace-check-task",
      run_id: "workspace-check-run",
      payload: {
        capsules: [
          {
            kind: "blocker",
            status: "provisional",
            title: "blocker",
            summary: blocker,
          },
        ],
      },
    };
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "workspace-check-run",
      chunks: [
        {
          id: "message:user-workspace-check",
          kind: "chat",
          title: "You · Request",
          body: "try again to implement the font changes",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [blockedTask],
      agentEvents: [blockedEvent],
    });

    const build = nodes.find((node) => node.id === "blueprint:build");
    expect(build).toMatchObject({
      title: "Execution needs attention",
      status: "blocked",
      body: blocker,
    });
    const answer = nodes.find((node) => node.kind === "answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "blocked",
      body: blocker,
    });
    const liveStatus = blueprintLiveStatusForTest(build!, [blockedTask], [blockedEvent], null);
    expect(liveStatus.latestUpdate).toContain("workspace_check failed");
  });

  it("does not treat output chunk parser errors as final responses", () => {
    const parserTask = task({
      task_id: "parser-final-task",
      status: "failed",
      latest_progress: "Separator is not found, and chunk exceed the limit",
      metadata: { active_run_id: "parser-final-run", selected_backend: "codex" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "parser-final-run",
      chunks: [
        {
          id: "message:user-parser-final",
          kind: "chat",
          title: "You · Request",
          body: "Finish the current workspace task.",
          status: "clean",
          meta: "user",
          role: "user",
        },
        {
          id: "agent-answer:parser-final-run",
          kind: "agent",
          title: "DAN · Answer",
          body: "Separator is not found, and chunk exceed the limit",
          status: "clean",
          meta: "run.log.completed",
          runId: "parser-final-run",
          taskId: "parser-final-task",
        },
      ],
      tasks: [parserTask],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "run.log.completed",
          run_id: "parser-final-run",
          task_id: "parser-final-task",
          summary: "Separator is not found, and chunk exceed the limit",
        },
      ],
    });

    const answer = nodes.find((node) => node.kind === "answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "blocked",
    });
    expect(answer?.body).toContain("Codex hit an output-size parsing limit while reading command output.");
    expect(answer?.body).not.toContain("Separator is not found");
  });

  it("keeps progress-like answer chunks blocked when execution needs attention", () => {
    const parserTask = task({
      task_id: "website-revert-task",
      status: "failed",
      latest_progress: "Separator is not found, and chunk exceed the limit",
      metadata: { active_run_id: "website-revert-run", selected_backend: "codex" },
    });
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      activeRunId: "website-revert-run",
      chunks: [
        {
          id: "message:user-website-revert",
          kind: "chat",
          title: "You · Request",
          body: "can you help me revert the website",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "website-revert-run",
          taskId: "website-revert-task",
        },
        {
          id: "agent-answer:website-revert-run",
          kind: "agent",
          title: "Codex · Answer",
          body: "I'll trace the website history first, then restore only the website files needed for the biology/network version. I'll also follow the repo tracking docs before editing so the project log stays consistent.",
          status: "clean",
          meta: "run.log.completed",
          runId: "website-revert-run",
          taskId: "website-revert-task",
        },
      ],
      tasks: [parserTask],
      agentEvents: [
        {
          type: "failed",
          source_event_type: "tool.failed",
          run_id: "website-revert-run",
          task_id: "website-revert-task",
          summary: "Separator is not found, and chunk exceed the limit",
        },
      ],
    });

    const answer = nodes.find((node) => node.kind === "answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "blocked",
      detail: "Stopped before final response",
    });
    expect(answer?.body).toContain("Codex hit an output-size parsing limit while reading command output.");
    expect(answer?.body).not.toContain("I'll trace");
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

  it("renames optional final follow-ups instead of showing them as remaining attention", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-button-polish",
          kind: "chat",
          title: "You · Request",
          body: "Fix the button inconsistency.",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "button-run",
          taskId: "button-task",
        },
        {
          id: "agent-answer:button-run",
          kind: "agent",
          title: "DAN · Answer",
          body:
            "### Remaining Attention\n\n" +
            "- I found the website files and fixed the button inconsistency. " +
            "If you want me to align the remaining app pages or polish other details, just say the word.",
          status: "clean",
          meta: "final",
          runId: "button-run",
          taskId: "button-task",
        },
      ],
      tasks: [
        task({
          task_id: "button-task",
          status: "completed",
          latest_progress: "Completed.",
          metadata: { active_run_id: "button-run" },
        }),
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:agent-answer:button-run");

    expect(answer).toMatchObject({
      title: "Final response",
      status: "done",
    });
    expect(answer?.previewBody).toContain("### Optional next steps");
    expect(answer?.previewBody).not.toContain("### Remaining Attention");
    expect(answer?.previewBody).not.toContain("### Needs Attention");
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

  it("uses the latest Codex agent message when the terminal event is generic", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-codex-answer",
          kind: "chat",
          title: "You · Request",
          body: "What is this project?",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "codex-run",
        },
      ],
      tasks: [
        task({
          task_id: "codex-task",
          status: "completed",
          latest_progress: "Codex completed.",
          metadata: { active_run_id: "codex-run" },
        }),
      ],
      agentEvents: [
        {
          type: "model_text_delta",
          source_event_type: "item.completed",
          summary: "ra-neo is a tactical command game about large-scale swarm combat.",
          task_id: "codex-task",
          run_id: "codex-run",
          payload: {
            text: "ra-neo is a tactical command game about large-scale swarm combat.",
          },
        },
        {
          type: "completed",
          source_event_type: "turn.completed",
          summary: "Codex completed.",
          task_id: "codex-task",
          run_id: "codex-run",
        },
      ],
    });

    const answer = nodes.find((node) => node.kind === "answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "done",
      body: "ra-neo is a tactical command game about large-scale swarm combat.",
    });
    expect(answer?.body).not.toBe("Codex completed.");
    expect(answer?.previewBody).toContain("ra-neo is a tactical command game");
  });

  it("uses terminal payload final_text when the terminal summary is generic", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-codex-payload",
          kind: "chat",
          title: "You · Request",
          body: "Please summarize this project.",
          status: "clean",
          meta: "user",
          role: "user",
          runId: "codex-payload-run",
        },
      ],
      tasks: [
        task({
          task_id: "codex-payload-task",
          status: "completed",
          metadata: { active_run_id: "codex-payload-run" },
        }),
      ],
      agentEvents: [
        {
          type: "completed",
          source_event_type: "turn.completed",
          summary: "Codex completed.",
          task_id: "codex-payload-task",
          run_id: "codex-payload-run",
          payload: {
            final_text: "The project is a browser and Godot prototype for large-scale tactical simulation.",
          },
        },
      ],
    });

    const answer = nodes.find((node) => node.kind === "answer");
    expect(answer).toMatchObject({
      status: "done",
      body: "The project is a browser and Godot prototype for large-scale tactical simulation.",
    });
    expect(answer?.body).not.toBe("Codex completed.");
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

  it("uses a structured Super DAN model response before a generic completed event", () => {
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-website-folder",
          kind: "chat",
          title: "You · Request",
          body: "Do you see a website folder here for this project?",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [
        task({
          task_id: "website-folder-task",
          status: "completed",
          latest_progress: "",
          metadata: { active_run_id: "website-folder-run" },
        }),
      ],
      agentEvents: [
        {
          type: "token_usage_recorded",
          source_event_type: "model.responded",
          summary:
            "Token usage for round 2: prompt=12404, completion=656, total=13060.",
          task_id: "website-folder-task",
          run_id: "website-folder-run",
          payload: {
            text: JSON.stringify({
              answer:
                "No - there is no website folder in this project. I checked the workspace root and common alternatives.",
            }),
          },
        },
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "completed",
          task_id: "website-folder-task",
          run_id: "website-folder-run",
        },
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "done",
      detail: "model.responded",
      body:
        "No - there is no website folder in this project. I checked the workspace root and common alternatives.",
      runId: "website-folder-run",
      taskId: "website-folder-task",
    });
    expect(answer?.body).not.toContain("did not return");
  });

  it("ignores truncated structured Super DAN answer payloads and uses complete task progress", () => {
    const completeAnswer =
      "This is the Super DAN Website Artifact, a static dependency-free product site with a landing page, organism visualization, and eight app demos.";
    const nodes = buildBlueprintNodesForTest({
      ...baseArgs,
      chunks: [
        {
          id: "message:user-website-summary",
          kind: "chat",
          title: "You · Request",
          body: "Can you tell me what this website project is?",
          status: "clean",
          meta: "user",
          role: "user",
        },
      ],
      tasks: [
        task({
          task_id: "website-summary-task",
          status: "completed",
          latest_progress: JSON.stringify({
            answer: completeAnswer,
            summary: ["Static website artifact inspected."],
          }),
          metadata: { active_run_id: "website-summary-run" },
        }),
      ],
      agentEvents: [
        {
          type: "token_usage_recorded",
          source_event_type: "model.responded",
          summary:
            "Token usage for round 6: prompt=25386, completion=2104, total=27490.",
          task_id: "website-summary-task",
          run_id: "website-summary-run",
          payload: {
            text: `{"answer": "${completeAnswer.slice(0, 70)}`,
          },
        },
        {
          type: "completed",
          source_event_type: "run.log.completed",
          summary: "completed",
          task_id: "website-summary-task",
          run_id: "website-summary-run",
        },
      ],
    });

    const answer = nodes.find((node) => node.id === "blueprint:answer");
    expect(answer).toMatchObject({
      title: "Final response",
      status: "done",
      body: completeAnswer,
      runId: "website-summary-run",
      taskId: "website-summary-task",
    });
    expect(answer?.body).not.toContain('"answer"');
    expect(answer?.previewBody).not.toContain('"answer"');
    expect(answer?.previewBody).toContain(completeAnswer);
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
