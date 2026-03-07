# 25-7: Behavior Policy — Autonomy, Queue, Progress, Promotion

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Define the shared execution-policy substrate that sits on top of the concierge foundation from `25-6`: when to act vs. ask, how to queue messages during active tasks, how to report progress on long-running work, and when to propose saving reusable workflows. This policy layer should remain valid when the solver-first runtime in `25-8+` becomes the top-level brain.

## Problem

Even with the transitional concierge foundation from `25-6`, the system still needs policy decisions:

1. **When to act immediately vs. ask for confirmation.** Currently `auto_approve` is a single boolean toggle. Real behavior should vary by action type: file sends are always auto, workflow deletes need confirmation, ambiguous matches need one question.
2. **How to handle queued messages.** `dan-chat` has basic stdin queuing, but there's no project-aware queue. Messages during a running project/task should either queue for that project/task, answer immediately (status checks), or start a parallel project.
3. **How to report progress.** Adapters throttle progress events at 5s intervals, but there's no unified model. Users should be able to ask "what's happening?" mid-run and get a useful answer without waiting for the next event.
4. **When to save reusable workflows.** Successful complex tasks should trigger a proposal to save the workflow, but not every file send or status check.

## Architecture

```
┌─────────────────────┐
│  SolverDecision or   │
│  fast-path result    │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  BehaviorPolicy      │  ← decides how execution proceeds once
│  (shared rules +     │     DAN has chosen a likely path to done
│   user overrides)    │
└──────────┬──────────┘
           │
     ┌─────┼─────┬──────────┐
     ▼     ▼     ▼          ▼
    auto  confirm  clarify  queue
     │     │       │         │
     ▼     ▼       ▼         ▼
  execute  show   ask one   hold msg
  + report plan   question  until task
  result   wait   re-dispatch  idle
           y/n
```

## Existing Infrastructure Used

| Component | How It's Used |
|---|---|
| `AdapterConfig.auto_approve` | Legacy compatibility alias that maps into `BehaviorPolicy`; messaging default stays `confirm` for mutations |
| `UserProfile` | `action_policy_overrides` for per-user customization |
| `RunManager.subscribe()` | Progress streaming for active runs |
| `ActivityTracker` | Quick status answers without LLM |
| `RunProgressTracker` (CLI) | Existing node-by-node progress display — extended to messaging surfaces |
| `GlobalEventBus` | Progress/completion events for notification routing |
| `ConversationMemoryStore` | Project summaries persisted after completion |
| `ExperienceStore` | Successful workflow runs saved as experience |
| Message queue (`cli/chat.py`) | Existing stdin queue generalized to project-aware queue |

## Key Decisions

- **Default is `auto`.** DAN does things without asking. Confirmation only for: destructive actions (delete workflow, cancel run), multiple ambiguous matches (2+ files), high-cost actions (>$1 estimated LLM spend), or mutation proposals on messaging surfaces.
- **Max one clarification round.** If a handler needs user input, it returns a structured `ClarificationRequest` (question + options). The concierge asks once, waits for one reply, then re-dispatches. No multi-turn interrogation. If still ambiguous after one round, pick the most likely option and proceed.
- **Queue is project/task-aware.** Messages during an active project task are sorted: status/progress queries answered immediately, messages for the same task are held until the current action completes, messages for a clearly different topic start a parallel project session. The queue understands the 2-layer scope from 25-6.
- **Progress is pull + push.** Long-running tasks push compact updates at configurable intervals. Users can also pull ("what's happening?") at any time via `StatusHandler` — this is answered immediately from `RunManager`/`ActivityTracker` state, no LLM needed. Progress labels use the `[DAN - <Project>]` format.
- **Workflow promotion after success.** After a successful task that produced a substantive workflow (>3 nodes), propose saving it. The proposal includes a one-line summary and a `/save` action. User accepts or ignores. Not automatic — user must approve.
- **Never just stop.** Policy should push execution toward alternate paths, useful subsets, capability-building, or human-action scaffolding before it asks the user to unblock something. The full terminal-outcome contract is expanded in `25-11`.

## Tasks

### 1. ActionPolicy model

- [x] 1-1. Create `src/dan/server/concierge/policy.py`:
  - `ActionPolicy` enum: `auto`, `confirm`, `clarify`.
  - `ExecutionPolicy` enum: `direct` (handler does it), `llm_conversation` (delegate to ChatManager), `meta_delegate` (delegate to MetaController).
  - `BehaviorPolicy(handler_policies: dict[IntentCategory, ActionPolicy], execution_policies: dict[IntentCategory, ExecutionPolicy], cost_confirm_threshold: float)`.
  - `DEFAULT_HANDLER_POLICIES`:
    - `file_request: auto`
  - `direct_task: auto`
    - `run_control: auto`
    - `status_check: auto`
    - `experience_query: auto`
    - `publish_share: confirm` (publishing is semi-destructive)
    - `workflow_build: auto` for simple, `confirm` for large mutations (>5 ops)
    - `workflow_query: auto`
    - `meta_goal: auto` (meta-orchestrator handles confirmation internally)
    - `conversation: auto`
  - `DEFAULT_EXECUTION_POLICIES`:
    - `file_request: direct`
  - `direct_task: direct` by default, `llm_conversation` when tool-backed direct execution still needs a lightweight answer synthesis
    - `run_control: direct`
    - `status_check: direct`
    - `experience_query: direct`
    - `publish_share: direct`
    - `workflow_build: llm_conversation` (simple) or `meta_delegate` (complex)
    - `workflow_query: direct`
    - `meta_goal: meta_delegate`
    - `conversation: llm_conversation`
- [x] 1-2. `resolve_policy(intent, context, user_profile) → (ActionPolicy, ExecutionPolicy)`:
  - Check `UserProfile.action_policy_overrides` first.
  - Then `DEFAULT_HANDLER_POLICIES` / `DEFAULT_EXECUTION_POLICIES`.
  - Override to `confirm` if: destructive action detected (delete/cancel keywords), estimated cost > `cost_confirm_threshold` (default $1), or messaging surface + mutation.
  - Override to `clarify` if: multiple ambiguous matches (2+ files, 2+ workflows).
- [x] 1-3. Unit tests: default policies, user overrides, destructive detection, cost threshold, messaging surface override. 15+ tests.

### 2. Clarification protocol

- [x] 2-1. Add to `src/dan/server/concierge/policy.py`:
  - `ClarificationRequest(question: str, options: list[str] | None, max_rounds: int = 1)`.
  - `ClarificationResponse(text: str, selected_option: int | None)`.
- [x] 2-2. In `Concierge.process()` (from 25-6): when a handler returns a `ClarificationRequest`:
  1. Send question to user.
  2. Wait for one reply (next message from same surface + task).
  3. Re-classify the reply. If it's a direct answer (number, "yes"/"no", text matching an option), feed it back to the handler.
  4. If still ambiguous, pick the highest-confidence option and proceed with a note: `"Proceeding with X — say 'undo' to revert."`.
- [x] 2-3. Handlers that may clarify:
  - `FileHandler`: multiple matches → list + "Which one?" (options = numbered list).
  - `DirectTaskHandler`: ambiguous file/fact target or missing scope → ask one focused question, then proceed.
  - `RunHandler`: multiple active runs when user says "cancel" → "Which run?" (options = run IDs with labels).
  - `WorkflowBuildHandler`: ambiguous intent → "Do you mean modify the current workflow inside this project, or create a new workflow/project?" (2 options).
- [x] 2-4. Unit tests: single-round clarification, option selection by number, fallback to highest-confidence, max 1 round enforced. 10+ tests.

### 3. Project-aware message queue

- [x] 3-1. Create `src/dan/server/concierge/queue.py`:
  - `ProjectMessageQueue(concierge)`.
  - `enqueue(msg: SurfaceMessage) → QueueDecision`:
    - `immediate`: message is a status/progress query or targets a different (idle) project → process now.
    - `queued`: message targets the same project/task that has an active action → hold, process after current action completes.
    - `parallel`: message is clearly a new topic → create new project session, process in parallel.
  - `drain(project_id, task_id) → list[SurfaceMessage]` — return queued messages when a task's action completes.
  - `peek(surface_id) → list[QueuedItem]` — show what's queued (for `/queue` command).
- [x] 3-2. Queue decision rules:
  - If `classification.intent == status_check`: always `immediate`.
  - If `context.task.status == "active"` and `classification.intent` targets that same project/task: `queued`.
  - If `context.is_new_project == True`: `parallel` (new project session).
  - If the current task is idle (no active action): `immediate`.
- [x] 3-3. Wire into `Concierge.process()`: before dispatching, check queue decision. If `queued`, hold message and send `"[DAN - {project.label}] Queued — I'll get to this after the current action."`. If `parallel`, resolve a new project and dispatch concurrently.
- [x] 3-4. Bound parallelism: define a small max active-project limit per surface (default 3). If exceeded, new work is queued at the project level instead of spawning unlimited concurrent sessions.
- [x] 3-5. Wire into `dan-chat` CLI: existing `MessageQueue.drain()` feeds into `ProjectMessageQueue.enqueue()` instead of going directly to the chat API.
- [x] 3-6. Unit tests: immediate for status, queued for active task, parallel for new topic, bounded parallelism, drain after completion. 14+ tests.

### 4. Progress reporting

- [x] 4-1. Create `src/dan/server/concierge/progress.py`:
  - `ProgressReporter(run_manager, activity_tracker)`.
  - `get_project_progress(project: Project, task: Task | None) → ProgressSnapshot`:
    - If project has linked runs: aggregate from `RunManager` (nodes completed/total, elapsed time, current node, token spend).
    - If project has a linked `MetaSession`: get meta-session status (planning/executing/diagnosing/repairing).
    - Format as compact one-liner: `"[DAN - lit-review] Running: 4/7 nodes done, 45s elapsed, $0.12 spent"`.
  - `format_for_surface(snapshot, surface: str) → str` — compact for messaging, detailed for CLI/editor.
- [x] 4-2. **Push model**: when a task has an active run, emit compact progress at configurable intervals (default 10s for CLI, 30s for messaging). Reuse `RunManager.subscribe()` events, filter through `ProgressReporter.format_for_surface()`.
- [x] 4-3. **Pull model**: `StatusHandler` (from 25-6) calls `ProgressReporter.get_project_progress()` for an instant answer. No LLM needed. The concierge routes "what's happening?" here, not to `ChatManager`.
- [x] 4-4. Extend `RunProgressTracker` (CLI) to support multi-project display: show progress for all active projects, not just the current `/run`.
- [x] 4-5. Unit tests: snapshot formatting, push interval, pull-returns-instant, multi-project display. 10+ tests.

### 5. Workflow promotion after success

- [x] 5-1. Add to `src/dan/server/concierge/promotion.py`:
  - `WorkflowPromoter(graph_store, experience_store)`.
  - `should_propose(project: Project, task: Task) → bool`:
    - Task completed successfully.
    - Project produced a workflow with > 3 nodes (not trivial).
    - Workflow is not already saved as a named graph (i.e. it's a scratch/auto-generated graph).
    - No identical workflow already in `ExperienceStore` (avoid duplicate proposals).
  - `build_proposal(project, task) → PromotionProposal(summary: str, workflow_id: str, suggested_name: str, save_command: str)`.
- [x] 5-2. Wire into `Concierge.process()`: after a handler returns a successful result for `workflow_build` or `meta_goal` tasks, check `should_propose()`. If true, append a promotion CTA to the response:
  ```
  [DAN - lit-review] Done! This workflow has 8 nodes and ran successfully.
  Save as reusable workflow? → /save lit-review-pipeline
  ```
- [x] 5-3. Handle `/save <name>` as a follow-up: rename the scratch graph to the given name, write experience summary to `ExperienceStore`, update the project summary and active task state.
- [x] 5-4. Unit tests: propose for >3-node success, skip for trivial, skip for already-saved, skip for failed task. 8+ tests.

### 6. Safety rules + messaging surface defaults

- [x] 6-1. Add destructive-action detection to `resolve_policy()`:
  - Keywords: "delete", "remove", "cancel", "unpublish", "destroy".
  - Force `confirm` regardless of default policy.
- [x] 6-2. Messaging surface override:
  - Telegram / WhatsApp / email: default `confirm` for all mutations (undo is impractical on these surfaces).
  - CLI / editor: default `auto` with `/undo` available.
- [x] 6-3. Cost estimation hook:
  - Before `WorkflowBuildHandler` or `meta_goal` dispatch, estimate LLM cost from `EngineConfig` model pricing + estimated node count.
  - If > `cost_confirm_threshold`: force `confirm` with cost estimate: `"This will cost ~$2.50 in LLM calls. Proceed? [Y/n]"`.
- [x] 6-4. Unit tests: destructive detection, messaging override, cost threshold. 8+ tests.

### 7. Docs + integration

- [x] 7-1. Update `docs/architecture.md` with `concierge/` package description and policy model.
- [x] 7-2. Update `docs/cli.md` with new behavior: queued messages, progress display, `/save` command.
- [x] 7-3. Update `docs/bugs.md` with any discovered limitations during implementation.
- [x] 7-4. End-to-end integration tests:
  - Scenario A: file request → auto-send → done (no confirmation).
  - Scenario B: "delete the workflow" → confirm prompt → user says yes → deleted.
  - Scenario C: mid-run "what's happening?" → instant progress → resume waiting.
  - Scenario D: successful multi-node workflow → promotion proposal → user saves.
  - Scenario E: two interleaved tasks → parallel dispatch → both complete → separate summaries.
  - 5+ integration tests covering these scenarios.

## Files

| File | Action |
|---|---|
| `src/dan/server/concierge/policy.py` | Create — `ActionPolicy`, `ExecutionPolicy`, `BehaviorPolicy`, `ClarificationRequest`, `resolve_policy()` |
| `src/dan/server/concierge/queue.py` | Create — `ProjectMessageQueue`, queue decision logic |
| `src/dan/server/concierge/progress.py` | Create — `ProgressReporter`, push/pull progress |
| `src/dan/server/concierge/promotion.py` | Create — `WorkflowPromoter`, promotion proposal |
| `src/dan/server/concierge/__init__.py` | Modify (from 25-6) — wire policy check + queue + promotion into dispatch loop |
| `src/dan/cli/chat.py` | Modify — wire existing message queue through `ProjectMessageQueue` |
| `src/dan/cli/adapter.py` | Modify — replace local policy branches with shared `BehaviorPolicy` decisions |
| `src/dan/adapters/base.py` | Modify — keep `auto_approve` as deprecated compatibility alias that maps into `BehaviorPolicy` |
| `src/dan/engine/user_profile.py` | Modify — add `action_policy_overrides: dict[str, str]` and `search_dirs: list[str]` |
| `docs/architecture.md` | Update — concierge package description |
| `docs/cli.md` | Update — queue, progress, `/save` |
| `tests/test_concierge/` | Create — policy, queue, progress, promotion, integration tests |

## Dependencies

- **25-6 (Concierge Runtime)** must be complete first. This plan extends the concierge foundation with policy, queue, progress, and promotion.
- **25-8+ (Solver Runtime and follow-ons)** will depend on this policy substrate rather than replacing it. Clarification, queueing, progress, and safety remain shared execution policy concerns.
- No other new dependencies. All subsystems called (RunManager, ActivityTracker, ExperienceStore, MetaController) already exist.

## Acceptance Criteria

- File sends never ask "would you like me to...?". They just do it.
- "Delete workflow X" always asks for confirmation, regardless of surface or user settings.
- Messages typed during a running project/task are queued and processed after completion, in order.
- "What's happening?" during a run returns an instant progress snapshot (no LLM call, < 200ms).
- Successful complex workflows trigger a save proposal. Trivial tasks (file sends, status checks) do not.
- Messaging surfaces (Telegram, WhatsApp) default to confirm for mutations; CLI/editor default to auto.
- User can override policies via `UserProfile.action_policy_overrides`.
- The policy layer never spawns unbounded parallel projects from one noisy surface.

## Notes

- 2026-03-06 implementation snapshot: policy/queue/progress/promotion scaffolding is live in the shared `concierge/` package and covered by focused unit/integration tests, with `UserProfile` now carrying policy overrides and search directories. The runtime now persists pending confirmation/clarification state so follow-ups like `"yes"`, `"2"`, or a matching filename fragment can resume the waiting action.
- As of the solver-first redesign discussion, this plan is now the long-lived policy substrate for `25-8+`, not just a per-handler add-on. Anything about clarification, queueing, progress, promotion, or safety should continue to live here even after top-level routing changes.
- Remaining gaps before this plan can be marked complete: bounded parallel-project enforcement, push progress updates, `/save` follow-up handling, real cost estimation, broader handler-specific clarification coverage, and full end-to-end surface tests.
- `AdapterConfig.auto_approve` is superseded but not deleted in this plan — it becomes a legacy alias that maps to the new policy model. Deprecation warning in logs.
- The existing `/undo` stack (25-5) continues to work. Auto-apply + undo is the CLI/editor default. Messaging surfaces use confirm instead.
- Cost estimation is advisory (based on static cost table from `providers/costs.py`). It can be wrong for custom models. The threshold is configurable via `UserProfile` or env var `DAN_COST_CONFIRM_THRESHOLD`.
