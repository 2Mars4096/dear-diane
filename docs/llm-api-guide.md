# LLM-Facing API Guide

Product naming: **Dear Diane**; assistant: **Diane**. Python imports, API identifiers, backend IDs, and `dan` CLI commands below remain compatibility contracts. `dear-diane` is an equivalent top-level command.

Document editing: `GET /api/workspace-files/document?path=...&root_path=...` returns strict UTF-8 `content` and a SHA-256 `revision` (2 MB limit). `PUT` to the same endpoint accepts `{path, root_path, content, revision}` and returns the new revision; HTTP 409 retains externally changed content. Browser-selected local documents are not implicitly uploaded or available to agents beyond supplied excerpts.

Use this guide when constructing DAN work programmatically. The old workflow graph builder, node taxonomy, edge types, and engine API no longer exist in the active tree.

Remote execution uses the same API on a machine-specific origin. Remote clients authenticate with a server-side access key as a Bearer credential or through `/remote/login` (expiring HttpOnly cookie); browser mutations and WebSockets must use that origin. Never put provider keys in client payloads. Local-only `/api/remote/connections` lists/saves SSH profiles; `/{id}/inspect`, `/{id}/install`, and `/{id}/access-key` are explicit POST actions. Install returns a job polled at `/api/remote/jobs/{id}`. On remote servers, `/api/remote/registry` GET/PATCH shares project metadata, while chat/run endpoints remain unchanged. The relay does not submit or replay agent commands.

SSH setup: local-only `GET /api/remote/ssh-hosts` returns `{hosts: string[]}` with literal SSH config aliases. Profiles accept `ssh_alias` as an alias or `user@host`, optional `ssh_port` (1–65535), and optional absolute/`~` `identity_file` path. Set `relay_enabled: false` and `ssh_via_relay: false` to save/check a host without private addresses; its URL is null and installation is unavailable until relay setup is enabled. Missing flags preserve old relay behavior. Use `If-None-Match: *` on profile PUT when creating to reject an existing ID with 409. No SSH config or key files are written by saving.

## Session plan review

- `GET /api/chats/{workflow_id}/{thread_id}/plan` returns `version`, `revision`, `mode` (`plan` or `work`), `document`, `history`, `question`, `questions_asked`, `proposal`, and `execution`. Execution status/progress comes from its recorded V2 run.
- `PUT` to the same endpoint requires the last `version` and an `action`: `mode` with `mode`, `edit` with `document`, `accept_proposal`, or `dismiss_proposal`. Stale writes return HTTP 409. Document fields are strings: `title`, `outcome`, `scope`, `specifications`, `steps`, `assumptions`, `blockers`; no blockers is an empty string.
- Workbench planning uses normal V2 run creation with `surface_context.plan_review = {action: "refine", workflow_id, revision}`. Backend normalization forces native read-only permissions independently of `selected_skills`; the API planner uses no tools. The model returns one fenced `diane-plan` JSON object with `document`, concise `summary`, and optional `question` (`question`, `reason`, `recommended`, up to two `alternatives`). The server persists at most three questions per session.
- Start uses `{action: "start", workflow_id, revision}` in the same context and the caller's unchanged execution policy. The backend rejects stale/already-started revisions, unresolved blockers, and Read only execution permissions; freezes the reviewed document against the run; and passes it to the existing selected runtime. GUI plan runs disable automatic promoted continuations. Planning does not authorize implementation until this explicit start.
- Transcript saves do not overwrite plan state. A late model revision becomes a reviewable proposal when the document changed during generation. Skills remain optional expertise, not a mode switch.

## Browser research tools

Ordinary GUI Super DAN requests expose `browser_open`, `browser_inspect`, `browser_click`, `browser_fill`, `browser_type`, `browser_select`, `browser_wait`, `browser_extract`, `browser_screenshot`, `browser_tabs`, and `browser_download`. Explicit `surface_policy.capability_packs` still controls availability. Native leads receive a run-scoped `browser.py --queue ... ACTION --args JSON` command; its `help` action returns tool schemas.

Inspect before choosing selectors. `browser_tabs(index=1)` activates an inspected popup tab. Retrieve citation text from its actual source and verify title/authors/year before saving it with file tools. Scoped `browser_download` requires `destination_path` inside the workspace and refuses existing files. Navigation takes HTTP(S) URLs. Browser contexts are fresh per run, close when it ends, and open on the backend host (visible on desktop by default). `DAN_BROWSER_HEADLESS=0|1|auto` overrides visibility. Search interactions do not authorize posting or account changes; report CAPTCHA/login blocks instead of bypassing them. Native Plan mode allows observation/navigation only.

## 1. Build a Universal Cell

```python
from diane import build_cell

cell = build_cell(
    model="gpt-5.4",
    sampling_policy="deterministic",
    role_label="reviewer",
)
```

The cell contains invariant infrastructure only. Do not encode the task in `instruction`, define a product-specific worker subclass, or fork the cell prompt.

## 2. Describe the role and brief

```python
from diane.worker.brief import RoleSpec
from diane.worker.contracts.templates import coding_brief

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
from diane.worker.brief import request_from_brief

request = request_from_brief(brief)
```

## 3. Compose an organism plan

```python
from diane.worker.contracts.templates import review_brief
from diane.worker.organisms.universal_organism import (
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
from diane.worker.organisms.universal_organism import execute_universal_organism

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

Enabled mutation-capable GUI manager stages receive `native_worker(action, backend?, prompt?, worker_id?, isolate=true)`. Backends: `codex`, `claude`, `antigravity`. `start` returns immediately; start multiple independent tasks before polling `status`. Status waits up to ten seconds and returns saved output/status. `stop` affects only its named child. `resume` sends a follow-up to a settled child within the same active parent; it cannot modify another parent's worker. Inspect child results before ending the manager run, which stops remaining children.

Runtime choices are in the execute request's `profile_policy.native_workers`, keyed by backend, with `enabled`, `account`, `provider`, `model`, `effort`, and `fast`. `provider` is `native` (default/current configuration) or `openrouter`, independently of the harness. Account names resolve server-side; never send credentials in this payload. Imported native context always forks before native continuation.

Read-only discovery: `GET /api/native-workers/catalog`, `GET /api/native-sessions?workspace=...`. Import only after user selection: `POST /api/native-sessions/import` with `{source_id, workspace, workspace_id, fork:true}`. Native worker history: `GET /api/native-workers/{parent_id}` and `/{worker_id}/events`; scoped stop: `POST /api/native-workers/{parent_id}/{worker_id}/stop`.

### Native leads and team delegation
- Agent-run execute backends: `native_codex`, `claude`, `antigravity`, `cursor`; `super_dan` remains the DAN lead. Existing `codex` backend is retained for compatibility; use `native_codex` for the new source-aware Codex adapter.
- `profile_policy.lead_profile`: account, provider, model, effort, fast; also accepted for `super_dan`. Continuation IDs are server-owned and scoped to thread/folder/runtime/account/source.
- `profile_policy.native_workers`: enabled profiles keyed by `dan`, `codex`, `claude`, `antigravity`, `cursor`; DAN profiles optionally set `base_url` for legacy callers.
- `native_worker` accepts start/status/stop/resume/apply/review. Native leads receive an equivalent run-scoped shell bridge. Team members cannot recursively delegate.
- `start` optionally accepts `contract={"scope":"Parser only", "acceptance":["Malformed input is rejected"]}`. These are task instructions, not additional permissions. The contract persists on resume; a new scope requires a new worker.
- Team-wide `status` returns bounded previews and explicit truncation flags. Fetch `status(worker_id=...)` for the full result, contract and current `attempt`; do not accept work based solely on a truncated preview.
- Record an assessment using `review(worker_id=..., attempt=N, decision="accepted"|"revise"|"blocked", evidence=["test outcome or inspected artifact reference"])`. Reviews require a settled managed worker and current attempt; only completed attempts can be accepted. Evidence records the lead's assessment, not independent verification. Resume only the worker needing correction; it increments the attempt and clears its current review while preserving prior reviews in events. Review does not automatically retry or apply edits.
- CLI equivalents: `start --scope 'Parser only' --acceptance 'Malformed input is rejected'` (alongside backend/prompt); `review --worker-id ID --attempt N --decision accepted --evidence 'tests/parser.py passed'`. Repeat `--acceptance`/`--evidence` for multiple entries. Existing required queue/client arguments still apply.

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


#### API providers and local credentials

- Profile `provider`: `native`, `openrouter`, `openai`, `deepseek`, or `moonshot`. Use the runtime catalog's `sources[].models`, `model_efforts`, `supported`, and `configured` fields; do not infer compatibility from provider names.
- Codex supports all API sources. Claude supports OpenRouter, DeepSeek, and Moonshot. Default (`dan`) uses Chat Completions; official OpenAI selections currently allow GPT-4.1/GPT-4.1-mini. Cursor/Antigravity remain native-only. API fast mode is rejected.
- `GET /api/model-providers` returns `providers[]` with `id`, `label`, `configured`, `saved`, and `key_env`; never returns keys.
- `PUT /api/model-providers/{provider}/key` accepts `{ "api_key": "..." }`; an empty string removes the saved key. Returns the same status-only catalog. These credential routes require a local host/client/origin and are disabled on remote execution servers.
- Saved host-local credentials override the matching environment key. Provider endpoints are fixed; credentials never enter execution profiles or command arguments. Configure keys separately on each execution host.


### Worker recovery, isolation, and review

- `native_worker(start, ..., isolate=true)` uses an owned Git worktree. Set `isolate=false` for shared read-only work or non-Git folders. The native CLI bridge uses `--shared-workspace` for the same choice. Source changes and untracked files form the baseline; ignored dependencies/secrets are excluded. `apply` integrates a settled worker after source conflict checks; it does not commit the source or delete the worktree.
- `POST /api/native-workers/{parent}/{worker}/reply` with `{prompt}` steers a live supported worker or resumes the persisted original runtime/profile. Missing native sessions retry with saved task/output context. Observed lead-owned subagents reject independent resume.
- `GET .../{worker}/events?before=N&limit=100` pages older log entries; `after=N` polls newer entries. `entries` carries `{cursor,event}` and `before` is null at the beginning. The legacy `events` array remains available.
- `POST .../{worker}/answer` accepts `{request_id, decision:"accept"|"decline"}` for one-time approvals or `{request_id, answers:{question_id:"answer"}}` for supported questions. Stale requests reject; no session-wide approval escalation.
- `GET .../{worker}/changes` returns the owned worktree diff and tree ID. `POST .../{worker}/apply` with `{tree}` applies the isolated worker delta; source HEAD or touched-file changes reject with 409. Event pages also return `has_more` for forward catch-up after completion. Public worker records advertise `can_reply`, `can_stop`, `can_apply`, and live `requests`.
- `GET /api/workspace-changes?root=...&thread=...&snapshot=...` returns boundaries and text diffs; `POST` with `{root,thread}` marks the current state. Automatic boundaries precede V2 execution. This is review of project edits, not attribution to a particular agent and not rollback.

### Codex conversation continuity
Codex-backed native leads and workers run in Diane-owned session storage. Existing Diane chat history seeds a new private native thread when migrating from shared storage. `/api/native-sessions` labels Codex imports with `continuation: "history"`; importing copies conversation history and does not resume the external native thread. Subsequent requests resume the private Diane thread.

## Local Sticky Todo handoff

`/api/sticky-todo` is local-only. The focused desktop Work window publishes `PUT /defaults` with `{projects:[{id,name,root}],active_project_id,execute}` using its existing execution payload. Credentials stay server-side. `GET /projects` exposes project metadata, not the execution profile.

`POST /requests` accepts `{request_id: UUID,task_key,text,parent?,steps?:string[],project_id?}`. The request UUID and body are immutable: identical retries return the same session; conflicting content returns 409. Diane snapshots the existing absolute project folder and execution defaults, creates a durable project session, and starts V2 background work.

`GET /requests/{UUID}` returns `{request_id,task_key,session_id,workflow_id,project_name,run_id,status,summary,artifacts,updated_at,stopping}`. Status is `queued|running|attention|ready|stopped|failed`, following the latest run in the linked session. `POST /requests/{UUID}/stop` requests a checkpoint stop; `stopping` remains true until confirmed.

Desktop only: `POST /api/sticky-todo/requests/{UUID}/open` on loopback port 45173 resolves and opens that session in Diane. It rejects Origin-bearing browser requests. Do not treat loss of connectivity as failure or retry by generating another UUID.

### Native history import boundaries
Claude discovery includes existing Diane API-provider transcript stores for configured accounts. Source metadata includes `provider`; provider histories use `continuation: "history"` and import as independent conversation copies rather than native-session resumes. Native subscription histories retain their existing continuation behavior. Cursor imports reject transcripts above 20,000 bubbles with an explicit error; no partial import or source mutation occurs.

### Account usage
`GET /api/usage` returns all discovered Codex/Claude logins and configured API providers, ordered by recent use. Optional `limit` truncates explicitly. Rows contain `backend`, `account`, `label`, `aliases`, `windows`, optional `plan`/`observed_at`/`error`; OpenRouter additionally returns `spend` (`usage`, `usage_daily`, `usage_monthly`, `limit`, `limit_remaining`, `limit_reset`). Key budget is not account balance. `fetched_at` timestamps report assembly; `observed_at` timestamps the account reading. Credentials and remote key labels are never returned.

### Planning question choices
`PlanQuestion.selection_mode` accepts `single` (default) or `multiple`. The recommended string and up to five alternatives are selectable answers; both use square checkboxes; single permits one selected choice and multiple permits several. Keep alternatives exclusive for single and independently compatible for multiple. Questions and final plans appear directly in the assistant conversation.

- Planning follow-up: show up to three MCQs together within the existing session question budget. Every question has Other with a custom answer and optional comments. Submit all answers together. Empty Plan mode adds no greeting to the transcript. `questions` is the batch field; legacy `question` remains readable.
