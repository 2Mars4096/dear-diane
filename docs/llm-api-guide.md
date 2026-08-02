# LLM-Facing API Guide

Use this guide when constructing DAN work programmatically. The old workflow graph builder, node taxonomy, edge types, and engine API no longer exist in the active tree.

## 1. Build a Universal Cell

```python
from dan import build_cell

cell = build_cell(
    model="gpt-5.4",
    sampling_policy="deterministic",
    role_label="reviewer",
)
```

The cell contains invariant infrastructure only. Do not encode the task in `instruction`, define a product-specific worker subclass, or fork the cell prompt.

## 2. Describe the role and brief

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

## 3. Compose an organism plan

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

## 4. Execute

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

## 5. Super DAN entry points

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
