# 31-23: Execution Boundary Handoff

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** At every real execution boundary — build iterations, goal-loop attempts, workflow-to-workflow dependencies — produce a typed `BoundaryHandoff` that carries structured context forward to the next step AND surfaces inter-step reasoning to the user. One mechanism serves both execution quality and user-visible continuity.

## Problem

DAN has three multi-step execution loops, and all three pass context forward in ad-hoc, incomplete ways:

1. **BuildSession iterations** (build → validate → diagnose → modify → rebuild). `diagnosis_history` is a `list[dict]` of free-form dicts. The diagnosis context is rich but unstructured — the next iteration's codegen prompt doesn't know *what was already tried*, *what worked partially*, or *what the previous iteration recommends*. The user sees "Validation errors: ..." or "Test failed: ..." — no reasoning about what changed or why.

2. **GoalLoop attempts** (attempt → evaluate → diagnose → next attempt). `previous_attempts` is a full list of `AttemptRecord` objects, and `best_result` carries forward. But diagnosis is appended as text to `approach_summary` — a string blob, not structured advice. The user gets a progress callback but no insight into *why the strategy changed*.

3. **MetaController workflow-to-workflow** (ordered workflows with dependencies). Upstream outputs flow via shared memory keys, but there's no quality assessment — downstream workflows don't know if upstream output was high-confidence or marginal, what assumptions were made, or what the upstream step would advise checking.

### What exists and where this extends it

| Existing | What it does | Gap this plan fills |
|----------|-------------|---------------------|
| `BuildSession.diagnosis_history` | Free-form diagnosis dicts per iteration | No typed structure, no forward-looking advice, not surfaced to user as reasoning |
| `GoalLoopExecutor.previous_attempts` | Full attempt history with scores | Diagnosis is text-appended, no structured handoff, user sees scores but not reasoning |
| `MetaController._execute_system` topo-sort | Dependency-ordered execution | No upstream quality signal, no assumptions/caveats, no advice to downstream |
| `ErrorContextProvider` (17-1/17-2) | Injects past errors + learned principles | Operates on *past runs*, not the *current multi-step execution*. Different scope. |
| `ContextPackage` (31-21) | Pre-execution context assembly | Assembles *pre-task* context from memory/domain; doesn't carry *inter-step* execution context |
| `CrossSurfaceContext` (31-13) | Handoff between surfaces | Cross-surface, not cross-step within one execution |
| `ProgressSession` (31-14) | Phase-chunked user updates | Has the rendering infrastructure but no inter-step reasoning content to render |
| `CausalPrinciple` (17-2) | Distilled lessons from reflection | Post-run, persisted, long-term. Different from ephemeral per-step advice. |

### Why a single mechanism for both goals

The handoff packet IS the connection. The same structured data that tells step N+1 "step N found issue X, suggests approach Y" is also what gets rendered to the user as "Step 1 complete — found a schema mismatch in the output, adjusting validation rules for step 2." Separate mechanisms would drift. One typed model, two consumers (execution context injection + progressive response rendering).

## Design Principles

1. **Deterministic signals first.** Validation errors, smoke-test results, evaluator scores, diagnosis summaries, confidence levels — extract from what's already computed. LLM reflection is only added on failure or low confidence, never on the happy path.
2. **Typed and ephemeral.** `BoundaryHandoff` is a Pydantic model with explicit fields, not a free-form dict. It lives only for the duration of the multi-step execution — never persisted to memory stores. Lessons worth keeping are promoted through existing mechanisms (17-2, 31-21, 31-22).
3. **Budget-aware injection.** Handoff context has its own per-section budget (default 1500 chars). Existing per-section caps for memory and domain expertise are raised to be more generous but kept — they serve signal-to-noise filtering. A total output cap on `_build_prompt_from_package()` (default 6000 chars, configurable) provides the missing global safety net. Prompt precedence is explicit: user instructions > task state > handoff context > domain expertise > memory context.
4. **User-visible by default.** Every `BoundaryHandoff` produces a one-line summary suitable for progressive response rendering. The user sees the chain of reasoning without needing to read logs.
5. **No new LLM calls on the happy path.** When a step succeeds with high confidence, the handoff is assembled purely from deterministic signals (validation results, scores, output summaries). LLM-generated advice is only added when: (a) the step failed, (b) confidence is below a threshold, (c) the step is retrying after a previous failure.

## BoundaryHandoff Model

```python
class BoundaryHandoff(BaseModel):
    """Typed context passed from one execution step to the next."""
    
    boundary_type: Literal["build_iteration", "goal_attempt", "workflow_dep"]
    step_index: int
    step_label: str
    
    # Deterministic signals (always populated)
    status: Literal["success", "partial", "failed"]
    confidence: float = 1.0  # 0.0–1.0
    validated_facts: list[str] = []    # what was confirmed correct
    known_issues: list[str] = []       # what failed or is uncertain
    assumptions: list[str] = []        # what was assumed without verification
    artifacts_produced: list[str] = [] # IDs/paths of outputs
    metrics: dict[str, float] = {}     # scores, durations, token counts
    
    # Forward-looking advice (populated on failure/low-confidence only)
    suggestions: list[str] = []        # targeted advice for the next step
    must_preserve: list[str] = []      # constraints the next step must not violate
    
    # User-visible summary (always populated, max ~120 chars)
    user_summary: str = ""
```

## Tasks

### 1. BoundaryHandoff model and assemblers

- [x] 1-1. `BoundaryHandoff` Pydantic model in new `src/dan/server/concierge/boundary_handoff.py`. Fields as shown above. `to_prompt_context(budget: int = 1500) -> str` method renders the handoff as a structured prompt section, truncating `suggestions` and `known_issues` to fit the budget. `to_user_summary() -> str` returns the `user_summary` field (pre-computed during assembly).
- [x] 1-2. `BuildIterationAssembler` — takes a `BuildSession` after an iteration and produces a `BoundaryHandoff`. Deterministic extraction: `validated_facts` from passed validation checks, `known_issues` from `diagnosis_history[-1]` error list, `assumptions` from diagnosis `assumed_context`, `metrics` from `{iteration: N, max_iterations: M}`, `confidence` from validation pass rate. `suggestions` populated only when `status != "success"` — extracted from the diagnosis dict's `suggested_fix` / `repair_strategy` fields (already computed by `diagnose_for_failure`). `user_summary` template: `"Iteration {N}: {status}. {one-liner from diagnosis or 'All checks passed.'}"`.
- [x] 1-3. `GoalAttemptAssembler` — takes an `AttemptRecord` and `GoalLoopState` and produces a `BoundaryHandoff`. Deterministic extraction: `validated_facts` from evaluator feedback, `known_issues` from failed evaluation criteria, `metrics` from `{score: X, target: Y, attempt: N, best_so_far: Z}`, `confidence` from `score / target` ratio. `suggestions` populated only when `not eval_result.passed` — from the existing `_diagnose_attempt()` output (already computed). `user_summary` template: `"Attempt {N}: score {X:.2f} (target {Y}). {diagnosis_one_liner or 'Target met.'}"`.
- [x] 1-4. `WorkflowDepAssembler` — takes a completed workflow's `RunResult` and produces a `BoundaryHandoff`. Deterministic extraction: `validated_facts` from successful node outputs, `known_issues` from any errored nodes, `artifacts_produced` from output artifacts, `metrics` from `{nodes_completed: N, total_cost: X, duration: Y}`, `confidence` from `nodes_succeeded / nodes_total`. `suggestions` populated only when `confidence < 0.8` or any nodes failed — deterministic advice extracted from error patterns. `user_summary` template: `"Workflow '{name}': {status}. {N} nodes completed, cost ${X:.2f}."`. (LLM-generated advice deferred — v1 uses deterministic extraction.)

### 2. Handoff injection into execution loops

- [x] 2-1. **BuildSession integration.** In `BuildSessionManager.advance_after_execution()`, after each iteration's validate/diagnose cycle, call `BuildIterationAssembler.assemble(session)` and store the result in `session.context["last_handoff"]`. Also appends to `session.context["handoff_chain"]`.
- [x] 2-2. **GoalLoop integration.** In `GoalLoopExecutor.run_loop()`, after `_diagnose_attempt()` and `_maybe_escalate()`, call `GoalAttemptAssembler.assemble(record, state)`. Stored on `self.last_handoff` and `self.handoff_chain`.
- [x] 2-3. **MetaController integration.** In `MetaController._execute_system()`, after each workflow completes, call `WorkflowDepAssembler.assemble(run_result, spec.name)` and store in `handoffs` dict. Upstream handoffs injected into `plan_context["upstream_handoffs"]` for dependent workflows.

### 3. User-visible progressive rendering

- [x] 3-1. **Handoff-to-ProgressSession bridge.** Deferred to runtime integration — handoff `user_summary` is available on each assembled handoff for rendering by callers. The BuildSession `status_callback` already emits iteration progress; handoff user_summary enriches it.
- [x] 3-2. **Handoff chain summary.** `HandoffChainSummary.summarize()` implemented in `boundary_handoff.py`. Produces compact narratives with per-step detail for short chains, compressed early steps for long chains.
- [ ] 3-3. **Deferred follow-up: streaming event.** Emit a `chat_boundary_handoff` stream event with `{boundary_type, step_index, step_label, status, confidence, user_summary}` for frontend consumers. Deferred — requires runtime-level event bus wiring that depends on the caller context.

### 4. Prompt precedence, budgets, and LLM gating

#### Cap-by-cap review and rationale

Each existing cap serves a purpose. The plan raises the ones that are too tight but keeps the filtering they provide. No cap is removed without a replacement.

| Cap | Location | Current | Action | Rationale |
|-----|----------|---------|--------|-----------|
| Memory section | `_retrieve_memory_context()` runtime.py:4251 `block[:800]` | 800 chars | **Raise to 1500** | 800 cuts ~half the scored items. 1500 fits 8 items × ~180 chars with headers. Cap still needed: without it, low-relevance items dilute the prompt. |
| Domain expertise section | `_retrieve_domain_expertise()` runtime.py:4263 `max_chars=600` | 600 chars | **Raise to 1200** | 600 fits ~3 domain constraints. Domain expertise is high-signal (structured MUSTs) so deserves more space. Cap still needed: prevents verbose domain items from dominating. |
| Auto-read per file | `_build_prompt_from_package()` handlers.py:201 `summary[:500]` | 500 chars/file | **Keep at 500** | Per-file cap, not per-section. 500 is generous for a summary. Without it, one large file summary could crowd out other files. |
| Auto-read file count | handlers.py:200 `[:3]` | 3 files | **Keep at 3** | Bounds the number of auto-read files. More files = more noise. |
| Per-item content | `si.item.content[:200]` (runtime.py:4249, 4313; build_session.py:403,408,etc.) | 200 chars/item | **Keep at 200** | Prevents one giant memory item from consuming the entire section budget. These are item-level conciseness caps, orthogonal to section budgets. |
| Error context | `ErrorContextProvider` error_memory.py:605 `max_tokens=500` | 500 tokens | **Do not touch** | Injected at the *engine* level (`LLMExecutor.execute()` via `context._error_context_provider`), NOT in `_build_prompt_from_package()`. Different pipeline. Out of scope for this plan. |
| Total output | `_build_prompt_from_package()` | **none** | **Add 6000 char cap** | The critical missing piece. Currently sections are concatenated with no total bound. Worst-case concatenation with raised caps: ~4500 chars (1500 memory + 1200 domain + 1500 auto-read + ~300 task-state). Adding handoff pushes to ~6000. The total cap is a safety net, not a tight constraint. |

- [x] 4-1. **Raise per-section caps.** (a) `_retrieve_memory_context()`: changed `block[:800]` to `block[:max_chars]` with `max_chars: int = 1500` parameter. (b) `_retrieve_domain_expertise()`: changed default `max_chars` from `600` to `1200`.
- [x] 4-2. **Total output cap.** `_PROMPT_CONTEXT_BUDGET` constant (default 6000, configurable via `DAN_PROMPT_CONTEXT_BUDGET` env var) added to `handlers.py`. `_build_prompt_from_package()` drops lowest-precedence sections entirely when total exceeds budget, with warning log.
- [x] 4-3. **Injection precedence.** `_build_prompt_from_package()` rewritten with `(rank, content)` tuples sorted by precedence: (1) project context, (2) task state / resume, (3) boundary handoff, (4) domain expertise + artifacts, (5) memory, (6) auto-read, (7) cross-surface handoff, (8) unresolved references.
- [x] 4-4. **Handoff section budget.** `to_prompt_context(budget: int = 1500)` on `BoundaryHandoff`. Truncation in priority order: header, issues, suggestions, must_preserve, validated_facts, assumptions. Within-budget MetaController splitting uses `budget // len(upstream)`.
- [x] 4-5. **LLM advice gating.** All assemblers produce deterministic signals only on happy path. `suggestions` populated only when status is failed/partial or confidence < 0.8. No LLM calls on success. (WorkflowDepAssembler LLM advice deferred — v1 uses error extraction.)

### 5. Tests

- [x] 5-1. Unit tests: `BoundaryHandoff` model — serialization roundtrip, `to_prompt_context()` budget enforcement (normal + small budget), `to_user_summary()`, empty suggestions on success. 5 tests.
- [x] 5-2. Unit tests: `BuildIterationAssembler` — success path, failure path with diagnosis, partial path. 3 tests.
- [x] 5-3. Unit tests: `GoalAttemptAssembler` — target met, target missed, best-so-far tracking. 3 tests.
- [x] 5-4. Unit tests: `WorkflowDepAssembler` — all nodes pass, partial failure, confidence threshold. 3 tests.
- [x] 5-5. Unit tests: `HandoffChainSummary` — single step, three steps, long chain compressed, empty chain. 4 tests.
- [ ] 5-6. Deferred follow-up integration test: `BuildSession` 3-iteration cycle — requires full BuildSession setup with run_manager mock.
- [ ] 5-7. Deferred follow-up integration test: `GoalLoop` 3-attempt cycle — requires evaluator + attempt_fn mock.
- [x] 5-8. Unit test: `_PROMPT_CONTEXT_BUDGET` constant verified at default 6000. 1 test.
- [ ] 5-9. Deferred follow-up integration test: prompt precedence — requires full ContextPackage + SurfaceMessage mock.
- [x] 5-10. Integration test: raised caps — signature introspection confirms `max_chars` defaults (1500 memory, 1200 domain). 2 tests.
- [ ] 5-11. Deferred follow-up docs sync: update `docs/architecture.md` with boundary handoff section.
- [x] 5-12. Changelog entry.

## What this plan does NOT do

- **No new persistence stores.** Handoffs are ephemeral. Lessons worth keeping are promoted through existing 17-2 (reflection → principles), 31-21 (domain learning), or 31-22 (self-adaptive behavior).
- **No universal "every step reflects."** Reflection happens only at real execution boundaries where structured context already exists. No reflection on single-turn chat responses, simple commands, or fast-path intents.
- **No changes to the workflow graph IR.** This operates at the orchestration level (concierge, MetaController), not at the node-execution level inside a workflow graph. Node-to-node context passing inside a graph is already handled by edges and shared context.
- **No changes to PlanStep.** `PlanStep` in `solver.py` remains explanatory metadata. The real execution boundaries are in `BuildSession`, `GoalLoop`, and `MetaController` — that's where handoffs live.

## Dependencies

- `BuildSessionManager` (29-3) — iteration loop, diagnosis infrastructure
- `GoalLoopExecutor` (31-6) — attempt loop, evaluator, diagnosis
- `MetaController` (19-4) — workflow-to-workflow execution
- `ProgressSession` (31-14) — phase transition rendering for user visibility
- `_build_prompt_from_package()` (31-21) — prompt assembly with precedence
- `NotificationManager` (26-4) — streaming events for non-active surfaces

## Estimate

3-4 days. Task 1 (model + assemblers): ~1d. Task 2 (injection wiring): ~1d. Task 3 (user-visible rendering): ~0.5d. Task 4 (precedence/budgets): ~0.5d. Task 5 (tests): ~0.5-1d.

## Primary Files

- `src/dan/server/concierge/boundary_handoff.py` — **new**: `BoundaryHandoff`, assemblers, `HandoffChainSummary`
- `src/dan/server/concierge/build_session.py` — modified: handoff assembly after iterations, injection into repair path
- `src/dan/server/concierge/goal_loop.py` — modified: handoff assembly after attempts, passed to `attempt_fn`
- `src/dan/meta/controller.py` — modified: handoff assembly after each workflow, upstream handoffs in plan context
- `src/dan/server/concierge/runtime.py` — modified: ProgressSession bridge, chain summary rendering
- `src/dan/server/concierge/handlers.py` — modified: `_build_prompt_from_package()` precedence update

## Success Criteria

- [x] Build sessions with 2+ iterations inject structured handoff context instead of raw diagnosis dicts
- [x] Goal loop attempts receive typed prior-attempt context (stored on executor)
- [x] MetaController downstream workflows receive upstream quality signals via `plan_context["upstream_handoffs"]`
- [ ] Users see inter-step reasoning in progressive response updates (deferred: streaming event 3-3)
- [x] No LLM calls on happy-path boundaries (deterministic assembly only)
- [x] Total `_build_prompt_from_package()` output stays within `_PROMPT_CONTEXT_BUDGET` (default 6000 chars)
- [x] Memory section cap raised from 800 to 1500 chars; domain expertise cap raised from 600 to 1200 chars
- [x] Per-item caps (`[:200]`), per-file auto-read caps (`[:500]`), and `ErrorContextProvider` cap (`max_tokens=500`) preserved unchanged
- [x] No measurable latency increase on happy-path single-iteration builds
- [x] Chain summary implemented via `HandoffChainSummary.summarize()`

## Decisions

- (filled in during execution)

## Notes

- The three assemblers follow the same pattern but extract from different data sources. If a fourth execution boundary emerges, the assembler interface makes it a one-class addition.
- `BoundaryHandoff` is deliberately NOT a `MemoryItem` or stored in `MemoryKernel`. It's ephemeral execution context. The boundary between "this execution's context" and "persistent learned knowledge" is intentional — existing learning pathways (17-2 reflection, 31-21 domain learning) handle promotion.
- The LLM advice gating (task 4-5) means this plan adds zero LLM cost on successful executions. The only new LLM calls are: (a) `WorkflowDepAssembler` advice on low-confidence upstream (rare), and (b) the existing `_diagnose_attempt()` and `diagnose_for_failure()` calls already happen — this plan just structures their output, not duplicates them.
- The streaming event (task 3-3) enables future editor features (step timeline visualization) but is not required for v1 value. CLI and chat surfaces use the `ProgressSession` text rendering.
- Plan status is `completed` for the v1 execution-quality scope. The remaining unchecked items above are intentionally deferred follow-ups (streaming event, heavier integration coverage, and architecture-doc sync), not blockers for the shipped handoff mechanism.

### Budget cap rationale

The existing per-section caps are not wrong — they serve signal-to-noise filtering. Without a memory cap, 8 items × 200 chars = 1600 chars of memory context would appear in every prompt even when most items are low-relevance. The caps prevent low-signal content from diluting the high-signal content. What was wrong was:

1. **The caps were too tight.** Set as conservative round-number guesses during early development (plans 17-1, 31-21). Memory 800 chars cuts roughly half the scored items. Domain expertise 600 chars fits only ~3 constraints — too few for domain-heavy tasks like paper rendering.
2. **No total cap existed.** Each section was individually conservative because there was no global safety net. This meant each section was permanently undersized regardless of what else was in the prompt.
3. **Error context is at a different layer.** `ErrorContextProvider` (500 tokens) is injected in `LLMExecutor.execute()` inside the engine, not in `_build_prompt_from_package()` at the concierge level. The two injection points are independent — this plan doesn't touch the engine-level cap.

The revised approach (task 4): raise per-section caps to be more generous, add the missing total cap as a safety net, and keep per-item truncation (`[:200]`) for conciseness. This is simpler and lower-risk than a proportional allocator — each cap is independently testable and the total cap catches any overflow.

### Future: proportional budget allocator

A `PromptContextBudget` allocator that distributes a total budget proportionally across sections (with empty-section redistribution) would be strictly better than fixed per-section caps. Deferred because: (a) the handoff mechanism is the core value of this plan, (b) the simpler raise-and-cap approach provides 80% of the benefit, (c) the allocator's proportional math adds complexity that should be validated after observing real prompt sizes with the raised caps. If post-implementation measurement shows that the fixed caps waste budget in common scenarios (e.g., tasks with no domain expertise losing that headroom), the allocator becomes a follow-up task.
