# LLM-Facing API Guide

Document editing: `GET /api/workspace-files/document?path=...&root_path=...` returns strict UTF-8 `content` and a SHA-256 `revision` (2 MB limit). `PUT` to the same endpoint accepts `{path, root_path, content, revision}` and returns the new revision; HTTP 409 retains externally changed content. Browser-selected local documents are not implicitly uploaded or available to agents beyond supplied excerpts.

Use this guide when constructing DAN work programmatically. The old workflow graph builder, node taxonomy, edge types, and engine API no longer exist in the active tree.

Remote execution uses the same API on a machine-specific origin. Remote clients authenticate with a server-side access key as a Bearer credential or through `/remote/login` (expiring HttpOnly cookie); browser mutations and WebSockets must use that origin. Never put provider keys in client payloads. Local-only `/api/remote/connections` lists/saves SSH profiles; `/{id}/inspect`, `/{id}/install`, and `/{id}/access-key` are explicit POST actions. Install returns a job polled at `/api/remote/jobs/{id}`. On remote servers, `/api/remote/registry` GET/PATCH shares project metadata, while chat/run endpoints remain unchanged. The relay does not submit or replay agent commands.

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

## Workbench native workers

Enabled mutation-capable GUI manager stages receive `native_worker(action, backend?, prompt?, worker_id?)`. Backends: `codex`, `claude`, `antigravity`. `start` returns immediately; start multiple independent tasks before polling `status`. Status waits up to ten seconds and returns saved output/status. `stop` affects only its named child. `resume` sends a follow-up to a settled child within the same active parent; it cannot modify another parent's worker. Inspect child results before ending the manager run, which stops remaining children.

Runtime choices are in the execute request's `profile_policy.native_workers`, keyed by backend, with `enabled`, `account`, `provider`, `model`, `effort`, and `fast`. `provider` is `native` (default/current configuration) or `openrouter`, independently of the harness. Account names resolve server-side; never send credentials in this payload. Imported native context always forks before native continuation.

Read-only discovery: `GET /api/native-workers/catalog`, `GET /api/native-sessions?workspace=...`. Import only after user selection: `POST /api/native-sessions/import` with `{source_id, workspace, workspace_id, fork:true}`. Native worker history: `GET /api/native-workers/{parent_id}` and `/{worker_id}/events`; scoped stop: `POST /api/native-workers/{parent_id}/{worker_id}/stop`.

### Native leads and team delegation
- Agent-run execute backends: `native_codex`, `claude`, `antigravity`, `cursor`; `super_dan` remains the DAN lead. Existing `codex` backend is retained for compatibility; use `native_codex` for the new source-aware Codex adapter.
- `profile_policy.lead_profile`: account, provider, model, effort, fast; also accepted for `super_dan`. Continuation IDs are server-owned and scoped to thread/folder/runtime/account/source.
- `profile_policy.native_workers`: enabled profiles keyed by `dan`, `codex`, `claude`, `antigravity`, `cursor`; DAN profiles optionally set `base_url` for legacy callers.
- `native_worker` accepts start/status/stop/resume. Native leads receive an equivalent run-scoped shell bridge. Team members cannot recursively delegate.

### Independent model sources

Example execute payload: Codex orchestrates and DeepSeek powers Codex itself.

```json
{
  "backend": "native_codex",
  "background": true,
  "profile_policy": {
    "permission_mode": "auto",
    "lead_profile": {
      "provider": "openrouter",
      "model": "deepseek/deepseek-v4.1-flash",
      "effort": "medium",
      "fast": false,
      "account": "default"
    }
  }
}
```

- OpenRouter is implemented for `dan`, `codex`, and `claude`; Cursor/Antigravity reject it. It requires a full provider/model ID and a server credential. Read catalog `runtimes[].sources` for capabilities and configured status.
- Credentials: `DAN_OPENROUTER_API_KEY` or `OPENROUTER_API_KEY`; otherwise reuse `DAN_LLM_API_KEY` only when the configured gateway is `https://openrouter.ai/api/v1`. The server loads `.env` at startup without overriding exported variables.
- DAN/Codex OpenRouter reasoning accepts default (empty), low, medium, or high. Claude forwards effort only for `anthropic/` models; other gateway models manage their own reasoning and remain experimental in that harness. Fast mode is unavailable for OpenRouter selections.
- Native Codex/Claude subscription accounts are not model credentials for the DAN harness. DAN configuration uses its API provider; native Codex with explicit `provider: native` selects the built-in OpenAI provider.
- Provider changes preserve DAN history but use separate provider-scoped native continuations. Keys never appear in browser profiles, API catalog responses, or CLI arguments.
