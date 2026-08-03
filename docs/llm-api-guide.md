# LLM-Facing API Guide

Use this guide when constructing DAN work programmatically. The old workflow graph builder, node taxonomy, edge types, and engine API no longer exist in the active tree.

## 1. Author and bind a Universal Cell

```python
from dan.worker import (
    CellAcceptance,
    CellContract,
    RecordRequirement,
    bind_cell,
    build_cell_spec,
)
from dan.worker.core.contracts import OutputContract

cell = build_cell_spec(
    {
        "task": "Review the implementation.",
        "scope": "src/dan/worker",
        "role": "reviewer",
    },
    CellContract(
        allowed_tools={"file_read"},
        dos=("ground every finding",),
        donts=("modify files",),
        preferences=("inspect focused files first",),
        limits={"max_tool_calls": 4, "max_tokens": 2000},
        acceptance=CellAcceptance(
            output=OutputContract(
                definition_of_done="Return a grounded verdict.",
                expected_return_shape="verdict plus findings",
            ),
            checks={"minimum_findings": 1},
            required_records=(
                RecordRequirement(kind="artifact", role="deliverable"),
            ),
        ),
    ),
)

invocation = bind_cell(cell, "gpt-5.4", cell_id="review")
```

The canonical definition is exactly `CellSpec(context, contract)`:

```text
CellSpec
├── context
└── contract
    ├── allowed_tools
    ├── dos
    ├── donts
    ├── preferences
    ├── limits
    └── acceptance
```

`allowed_tools` is a closed allowlist; tools absent from it are unavailable at cell level. Platform/runtime denials are external. Use `dos` and `donts` for binding behavior, `preferences` for non-binding strategy, `limits` for resource ceilings, and `acceptance` for output/check/evidence requirements.

The cell contains no identity, model, relationship, position, status, or execution history. `bind_cell(...)` places `cell_id` and `ExecutorRef` in `CellInvocation`. `build_structured_cell(executor, context, contract, cell_id=...)` remains a convenience that builds and binds in one call.

Compile or execute one cell through the existing core:

```python
from dan.worker import compile_cell, execute_structured_cell

compiled = compile_cell(invocation)
report = await execute_structured_cell(configured_executor, invocation)
```

The compiler path is:

```text
CellSpec + ExecutorRef → CellInvocation → WorkerBrief → ExecutionRequest → WorkerCoreExecutor
```

The `WorkerBrief`, fixed system prompt, provider adapters, tools, memory, acquisition, workspace instructions, and structured-output validation remain unchanged internal machinery.

### Runtime records and deliverable acceptance

`CellReport` contains `outcome`, `result`, `context_delta`, `records`, and `error`. Records have an open string `kind` plus stable common fields (`resource`, `role`, `digest`, `location`, `provenance`, and `metadata`). Adding a new record kind does not change the cell or report schema.

The execution adapter currently recognizes:

- `input_sources` and `source_refs` as source records;
- `modifications` and `modified_files` as modification records, including `line_start`/`line_end` location data;
- `output_files` and `artifact_refs` as output artifact records;
- `deliverables` as deliverable artifact records;
- explicit `records` from context, outputs, or runtime metadata.

Example runtime result data:

```python
{
    "modifications": [
        {
            "path": "src/example.py",
            "line_start": 10,
            "line_end": 14,
            "before_digest": "sha256:before",
            "after_digest": "sha256:after",
        }
    ],
    "deliverables": [
        {
            "path": "output/report.pdf",
            "media_type": "application/pdf",
            "digest": "sha256:pdf",
        }
    ],
}
```

`RecordRequirement` can match kind, role, media type, resource glob, metadata, and minimum count. Missing required records turn an otherwise completed report into a failed report.

## 2. Define external topology and runtime

```python
from dan.worker import (
    CellRuntimeConfig,
    CellTopology,
    ContextView,
    ForkJoinRelation,
    JoinPolicy,
    SequenceRelation,
    UniversalCellConfig,
)

config = UniversalCellConfig(
    cells={
        "parent": parent_cell,
        "reader": reader_cell,
        "next": next_cell,
    },
    executors={
        "parent": "gpt-5.4",
        "reader": "gpt-5.4-mini",
        "next": "gpt-5.4",
    },
    topology=CellTopology(
        sequences=(
            SequenceRelation(source_cell_id="parent", target_cell_id="next"),
        ),
        fork_joins=(
            ForkJoinRelation(
                parent_cell_id="parent",
                child_cell_ids=("reader",),
                context_views={
                    "reader": ContextView(
                        include_paths=("sources", "requirements.public"),
                    ),
                },
                join_policy=JoinPolicy(mode="all_success"),
                max_concurrency=2,
            ),
        ),
    ),
    runtime=CellRuntimeConfig(
        max_concurrency=2,
        max_retries=1,
        model_context_view=ContextView(max_rendered_chars=32_000),
    ),
)
```

- `UniversalCellConfig.cells` stores position-independent `CellSpec` values; `executors` binds each structural id separately.
- `link_linear(...)` is shorthand for external sequence edges; it never mutates cells.
- `inherit_previous_report(...)` preserves full logical context and lets target-local context win key collisions while embedding only the compact prior report.
- `prepare_children(...)` applies each child's exact nested `ContextView` and authority-monotone contract.
- `fan_out_and_collapse(...)` settles the declared children under bounded concurrency and stores compact reports.
- `handoff_to_next(...)` advances only after the configured join accepts.
- `all_success`, `all_settled`, `quorum`, and `at_least_one` are supported join modes.

This configuration is still a cell-level boundary, not an organism scheduler.

## 3. Advanced direct WorkerBrief construction

```python
from dan.worker.brief import RoleSpec
from dan.worker.contracts.templates import coding_brief

brief = coding_brief(
    role=RoleSpec(
        role_label="focused implementer",
        responsibility="Apply the smallest correct patch and report evidence.",
        success_criteria=["requested behavior works", "focused tests pass"],
        artifact_targets=["src/example.py"],
        trace_role="implementation",
    ),
    task="Implement the requested behavior in src/example.py.",
    scope="Only src/example.py and its focused test.",
    hard_constraints=["Do not change public behavior outside the request."],
    allowed_tool_ids=["file_read", "file_edit", "workspace_check"],
    input_payload={"workspace_root": "/absolute/workspace"},
)
```

Available template helpers are `role_brief`, `coding_brief`, `review_brief`, `research_brief`, and `scheduler_brief`. Templates return `WorkerBrief`; they do not create a new runtime.

Important `WorkerBrief` fields:

- `role`, `task`, `scope`;
- `hard_constraints`, `soft_constraints`;
- `tool_policy`, `runtime_policy`, `validation_policy`;
- `output_contract`, `sampling_policy`;
- `evidence`, `context_packet`, `input_payload`;
- `fail_predicates`, `recovery_hints`;
- lifecycle, mailbox, heartbeat, and semantic-status policies.

Convert a brief to the cell-core request with:

```python
from dan.worker.brief import request_from_brief

request = request_from_brief(brief)
```

## 4. Compose an organism plan

```python
from dan.worker.contracts.templates import review_brief
from dan.worker.organisms.universal_organism import (
    OrganismDependency,
    OrganismPlan,
    OrganismPolicy,
    OrganismTask,
)

review = review_brief(
    role={
        "role_label": "validator",
        "responsibility": "Check the candidate against the acceptance criteria.",
        "trace_role": "validation",
    },
    task="Validate the implementation and return evidence.",
    allowed_tool_ids=["file_read", "workspace_check"],
)

plan = OrganismPlan(
    plan_id="example-change",
    objective="Implement and validate the requested change.",
    tasks=[
        OrganismTask(task_id="implement", brief=brief, model="gpt-5.4"),
        OrganismTask(
            task_id="validate",
            brief=review,
            model="gpt-5.4",
            dependencies=[OrganismDependency(upstream_task_id="implement")],
        ),
    ],
    policy=OrganismPolicy(max_concurrency=2, retry_budget=1),
)
```

Use dependency edges and readiness predicates to express execution order. Do not create a coding/research/review organism class.

## 5. Execute

```python
from dan.worker.organisms.universal_organism import execute_universal_organism

result = await execute_universal_organism(plan, executor=configured_executor)
```

`configured_executor` is a `WorkerCoreExecutor` with the completion/tool providers required by the briefs. The Super DAN local runtime assembles this for the product surface.

The result contains:

- terminal run status;
- per-task results;
- ordered organism events;
- deterministic run-state, decision, command, capsule, and diagnostic metadata.

Pass `event_callback` for live projections and `OrganismLogWriter` for replayable JSONL persistence.

## 6. Super DAN entry points

For product work, prefer the CLI/TUI or Agent V2 HTTP API instead of hand-building a plan:

```bash
dan super-organism --model gpt-5.4 "Implement and validate the request"
dan super-tui
```

HTTP ingress:

```http
POST /api/v2/agent-runs
Content-Type: application/json

{
  "workflow_id": "_scratch",
  "thread_id": "session-id",
  "message": "Implement and validate the request",
  "mode": "agent",
  "surface_context": {"workspace_root": "/absolute/workspace"}
}
```

Then read `/api/v2/tasks`, `/api/v2/agent-runs/{run_id}`, and `/api/v2/agent-runs/{run_id}/events`, or post bounded commands to `/api/v2/agent-runs/{run_id}/commands`.

## Rules for callers

- Keep the cell invariant; put specialization in the brief.
- Keep the protected goal/permissions/acceptance criteria in a task blueprint, and execution choices in an attempt.
- Use absolute workspace roots at product boundaries.
- Request only the tools needed by a brief.
- Treat model-authored scheduling or mutation suggestions as proposals until deterministic policy admits them.
- Never infer mutation, destructive, publication, purchasing, or investment authority from the task family.
- Require evidence and validation before claiming completion.
