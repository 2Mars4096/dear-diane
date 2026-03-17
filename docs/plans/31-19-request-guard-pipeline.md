# 31-19: Request Guard Pipeline

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Add inter-step guards, entity awareness, and system-prompt clarification rules so the concierge validates understanding at each stage before proceeding — catching misinterpretations early instead of delivering wrong results.

## Problem

The current message lifecycle flows classify → resolve → execute → post-process with no validation between steps. The only quality check is the completion guard (31-9), which runs *after* the full response is generated and only checks coverage, not correctness.

The user's core requirement: **requests should be clearly understood, clearly executed, and thoroughly executed.** Three distinct quality dimensions, each needing its own check:

1. **Clearly understood** — does the system know what the user wants? (Currently: no validation. The solver guesses and acts.)
2. **Clearly executed** — does the action match the understood intent? (Currently: no check between resolution and execution.)
3. **Thoroughly executed** — does the response cover everything asked? (Currently: completion guard exists but runs post-hoc, coverage-only.)

Observable failures:
- User says "any updates on the Kaggle project" → classifier sees it as `direct_task` → solver tells LLM to "check the GitHub project" → user gets irrelevant response about GitHub repos
- LLM never asks for clarification because the solver prompt says `"clarification_question: null unless ambiguity blocks useful action"` — and the LLM can always *construct* an action, even a wrong one
- The system doesn't know "Kaggle" is a DAN `Project` entity with stored state, not an external website
- No system-prompt rule instructs the LLM to prefer asking over guessing
- Existing internal loops (completion guard, preflight clarification, goal loop) are disconnected — they don't compose into a coherent quality chain

## Industry Context

Surveyed: DeerFlow 2.0 (ByteDance), OpenClaw, CrewAI, Google ADK, LangGraph/Reflexion, AutoGen, Anthropic think tool.

| Pattern | Source | Adoption |
|---------|--------|----------|
| Inter-step guardrails with retry | CrewAI task guardrails | Standard in CrewAI, Google ADK |
| Before/after callbacks at every boundary | Google ADK (6 hook types) | Production pattern |
| Generate → Critique → Revise loop | LangGraph Reflexion | 91% task completion improvement |
| Plan verification before execution | DeerFlow coordinator | Plan-and-reflect pattern |
| Think step for sequential decisions | Anthropic think tool | Recommended for policy-heavy contexts |
| Nested chat as inner monologue | AutoGen | Enables entity grounding |

Key industry consensus: **every production agent framework has explicit guard/callback hooks between pipeline steps**. DAN's pipeline stages are correct but lack the guard hooks.

## Design

### Core Principle: Cascading Validation

Each guard validates not just its own step, but implicitly validates all prior steps — because it sees the *cumulative* output. A classification error that slips past Guard 1 can still be caught by Guard 2 (bad assumptions derived from bad classification) or Guard 3 (irrelevant response). No single guard needs to be perfect.

Each guard receives a `GuardContext` accumulating all prior outputs:

```python
@dataclass
class GuardContext:
    message: SurfaceMessage
    entity_ctx: EntityContext
    classification: ClassificationResult | None = None
    solver_decision: SolverDecision | None = None
    response_content: str | None = None
```

Guards are functions `(GuardContext) → GuardResult` where:

```python
@dataclass
class GuardResult:
    passed: bool
    action: Literal["proceed", "reclassify", "clarify", "short_circuit"] = "proceed"
    clarification_question: str | None = None
    short_circuit_response: str | None = None
    notes: list[str] = field(default_factory=list)   # logged for debugging
```

### Pipeline with Guards

```
1. FAST COMMAND?
   ↓ no
2. ENTITY GROUNDING        ← NEW: scan message against ProjectStore + active workflows
   ↓
3. CLASSIFY                 (keyword + LLM classifier, entity context available)
   ↓
   GUARD 1: Classification  ← NEW: is the classification coherent with entities and message?
   ↓                          (can short-circuit or reclassify)
4. RESOLVE GOAL             (solver receives entity context in planning_ctx)
   ↓
   GUARD 2: Understanding   ← NEW: is the solver's interpretation faithful to the user's words?
   ↓                          (can halt and ask for clarification)
5. EXECUTE                  (handler / goal orchestrator / capability dispatch)
   ↓
   GUARD 3: Response        ← NEW: is the response relevant and complete?
   ↓                          (extends completion guard with relevance; advisory)
6. DELIVER
```

Both execution paths (solver path via `_solver_path()` AND goal orchestrator path via `_execute_goal()`) must run through the same Guard 2. Guard 3 runs in `_post_process_response()` which is already shared by both paths.

### Entity Grounding

New lightweight step after `_try_fast_command`, before classification. Scans the user message against known entities:

```python
@dataclass
class EntityContext:
    matched_projects: list[Project]   # fuzzy label match against ProjectStore
    matched_tasks: list[Task]         # active tasks within matched projects
    matched_workflows: list[str]      # known workflow names/IDs mentioned
    unresolved_refs: list[str]        # words that look like entity refs but didn't match
    is_about_project: bool            # heuristic: message asks ABOUT a project, not work within it
```

Matching uses the same fuzzy logic as `_resolve_project_by_name()` (already proven in `/project` command). `is_about_project` detects status/update/progress/review language combined with a project match.

The `EntityContext` is:
- Passed to classification (informs intent choice)
- Injected into solver's `build_planning_context()` (so LLM knows "Kaggle" is a local project with N tasks and M memories)
- Injected into `{context_block}` of `UNIFIED_SYSTEM_PROMPT` as a "Known projects" section
- Carried in `GuardContext` for all three guards
- Available to memory retrieval (project-scoped supplement uses matched project ID)

### Guard 1: Classification Coherence

Runs after `classify_intent_llm()`, before `GoalResolver.resolve()`.

**"Clearly understood" check — does the classification make sense?**

Checks:
- If `EntityContext.matched_projects` is non-empty AND `is_about_project` is true → short-circuit to project info response. Bypasses solver entirely.
- If classification is `file_request` but no path/filename in message AND a project entity was matched → suspect misclassification, lower confidence, let LLM reclassify.
- If classification confidence < 0.6 → ask for clarification instead of proceeding.
- Cascading: this also implicitly validates entity grounding (if entity grounding matched the wrong project, the classification check may catch the inconsistency).

On failure: reclassify or generate a clarification question.

Escape hatch: if the user explicitly says "search online for..." or "check GitHub..." or uses action verbs that clearly indicate external action, `is_about_project` is false and the guard passes through. The guard only intercepts when the message is *about* a project, not when it's a task *within* a project.

### Guard 2: Understanding Coherence

Runs after `GoalResolver.resolve()` returns `SolverDecision`, before execution. Must run on BOTH the solver path (`_solver_path`) and the goal orchestrator path.

**"Clearly executed" check — does the planned action match what the user asked?**

Checks:
- **Assumption grounding**: each entry in `SolverDecision.assumptions` is checked for lexical overlap with the user message or known entity context. Assumptions with zero grounding (no shared keywords with user text, no entity match) are flagged as "ungrounded." If >50% of assumptions are ungrounded → ask for clarification.
- **Goal paraphrase**: `SolverDecision.user_goal` should share significant key nouns with the original message. If the Jaccard similarity of content words is below a threshold (e.g., 0.2) → flag as "hallucinated goal."
- **Entity cross-check**: if entity context has matched projects but solver's `execution_mode` is `direct_action` and `user_goal` doesn't reference any matched project → coherence failure. The solver is ignoring a known entity.
- **Confidence**: if `SolverDecision.confidence < 0.7` → ask for clarification.
- Cascading: catches compound errors where Guard 1 passed (classification seemed OK) but the solver still misinterpreted (e.g., classified as `conversation` correctly, but solver decided to "go check GitHub" instead of conversing about the project).

On failure: set `clarification_question` and return to user before executing.

### Guard 3: Response Relevance

Extends the existing `_post_process_response()` completion guard with a relevance check.

**"Thoroughly executed" check — is the response complete and on-topic?**

Checks:
- **Topic match**: key content nouns from the user message should appear in the response. Extract nouns from user message (stop-word filtered), check what fraction appear in response. If < 30% → flag.
- **Entity coherence**: if user asked about project "Kaggle" (entity context) but response doesn't mention it → flag.
- **Coverage**: existing completion guard (31-9) — checks that every requirement in the user's message is addressed.
- Cascading: even if Guards 1 and 2 passed, this catches execution-level failures (e.g., the LLM understood the question but the tool returned wrong data, or the LLM went off on a tangent mid-response).

On failure: append a softened note — "I may have misunderstood your question. Did you mean to ask about your Kaggle project? Try `/project info Kaggle`." Advisory, not blocking — the response is still delivered.

### System Prompt Updates

Add to `UNIFIED_SYSTEM_PROMPT` rules:

```
9. Work autonomously by default. For implement/fix/refactor/build tasks,
   continue through investigation, execution, validation, and one self-review
   pass before stopping.
10. If the user's request is ambiguous and the next action is hard to reverse,
    ask focused clarifying questions before acting.
11. If the request is ambiguous but the next step is reversible, choose the
    safest reasonable interpretation, state it briefly, and proceed.
12. When the user asks for a review, provide findings first. Do not patch,
    rewrite, or broaden scope unless the user also asks you to fix or implement.
```

Update solver prompt: remove `"clarification_question: null unless ambiguity blocks useful action"` bias → replace with `"set clarification_question if the user's intent is unclear, multiple interpretations exist, or you need to make non-obvious assumptions"`.

Update classification prompt: remove stale "project review", "project progress" references from `status_check` description (those are now handled by `/project` command and Guard 1).

### Loop Composition

Restructure the existing quality mechanisms into three coherent phases with clear handoff contracts:

```
UNDERSTAND phase (inputs: message, surface_id):
  1. Entity grounding      → EntityContext
  2. Classification         → ClassificationResult
  3. Guard 1                → pass/reclassify/clarify/short-circuit
  4. Goal resolution        → SolverDecision
  5. Guard 2                → pass/clarify
  6. Preflight (31-14)      → pass/confirm (cost/time gating)
  Output: validated SolverDecision + EntityContext + GuardContext

EXECUTE phase (inputs: validated SolverDecision, context):
  7. Handler/capability dispatch  — or —
  7a. Goal loop (31-6, iterative tasks)
  7b. Mid-execution dependency (31-8)
  Output: raw response content

VERIFY phase (inputs: response, GuardContext):
  8. Guard 3 (relevance + coverage)
  9. Completion guard (31-9)
  10. Memory write-back
  Output: final response content
```

The key change: UNDERSTAND is now a real phase with multiple validation steps, not just "classify and go." Each phase has a typed output contract and clear entry/exit conditions. `_process_inner()` is reorganized with section markers and the `GuardContext` threading through.

## Tasks

- [x] 1. **Entity Grounding**
  - [x] 1-1. Create `EntityContext`, `GuardContext`, `GuardResult` dataclasses in new `entity_grounding.py`
  - [x] 1-2. Implement `ground_entities(text, project_store, surface_id)` — fuzzy match against active projects, detect `is_about_project` (status/update/progress/review words + project match)
  - [x] 1-3. Also match active tasks within matched projects and known workflow names
  - [x] 1-4. Call `ground_entities()` in `_process_inner()` after fast-command check, before classification
  - [x] 1-5. Inject `EntityContext` into solver's `build_planning_context()` as structured dict
  - [x] 1-6. Add "Known projects" section to `{context_block}` in `UNIFIED_SYSTEM_PROMPT` when entity context has matches — include project label, status, task count, memory count
  - [x] 1-7. Tests: entity matching (exact, partial, no match, multiple matches), `is_about_project` detection (positive: "updates on kaggle", negative: "run the kaggle pipeline"), workflow matching

- [x] 2. **Guard 1: Classification Coherence**
  - [x] 2-1. Create `guard_classification(guard_ctx: GuardContext) → GuardResult` in `entity_grounding.py`
  - [x] 2-2. If `is_about_project` + matched project → `action="short_circuit"` with project info response
  - [x] 2-3. If classification is `file_request` but no path + project matched → `action="reclassify"`
  - [x] 2-4. If classification confidence < 0.6 → `action="clarify"` with generated question
  - [x] 2-5. Escape hatch: explicit external-action verbs ("search online", "check GitHub", "look up on the web") → always pass through
  - [x] 2-6. Wire into `_process_inner()` after `classify_intent_llm()`, before solver
  - [x] 2-7. Tests: project-about queries short-circuited, escape hatch works, low-confidence triggers clarification, file_request without path reclassified

- [x] 3. **Guard 2: Understanding Coherence**
  - [x] 3-1. Create `guard_understanding(guard_ctx: GuardContext) → GuardResult`
  - [x] 3-2. Assumption grounding: extract content words from each assumption, check overlap with user message words + entity names. Flag if zero overlap.
  - [x] 3-3. Goal paraphrase: Jaccard similarity of content words between `user_goal` and original message. Flag if < 0.2.
  - [x] 3-4. Entity cross-check: matched projects but solver ignores them → flag
  - [x] 3-5. Confidence threshold: `SolverDecision.confidence < 0.7` → clarify
  - [x] 3-6. Wire into BOTH `_solver_path()` and goal orchestrator path, after resolve, before execution
  - [x] 3-7. On failure: set `clarification_question` on the decision, return to user
  - [x] 3-8. Tests: ungrounded assumptions caught, hallucinated goals detected, entity mismatches flagged, low-confidence triggers clarification

- [x] 4. **Guard 3: Response Relevance**
  - [x] 4-1. Extract key content nouns from user message (stop-word filtered)
  - [x] 4-2. Topic-match check: fraction of user nouns appearing in response. Flag if < 0.3.
  - [x] 4-3. Entity-coherence check: if user asked about a known project, response should reference it
  - [x] 4-4. Integrate with existing completion guard (31-9) — Guard 3 runs first (relevance), then completion guard (coverage)
  - [x] 4-5. On failure: append clarification suggestion mentioning `/project` if a project was matched (soft, not blocking)
  - [x] 4-6. Tests: off-topic responses flagged, on-topic responses pass, entity-coherence check

- [x] 5. **System Prompt Updates**
  - [x] 5-1. Add rules 9-11 to `UNIFIED_SYSTEM_PROMPT` (clarification preference, project-context use, assumption transparency)
  - [x] 5-2. Update solver prompt: replace `"null unless ambiguity blocks useful action"` with `"ask if intent unclear, multiple interpretations exist, or non-obvious assumptions needed"`
  - [x] 5-3. Update classification prompt: remove stale "project review" / "project progress" references from `status_check` description
  - [x] 5-4. Tests: verify prompt text contains new rules, verify old biased language removed

- [x] 6. **Loop Composition**
  - [x] 6-1. Reorganize `_process_inner()` into UNDERSTAND / EXECUTE / VERIFY sections with clear markers
  - [x] 6-2. Create `GuardContext` at entity grounding, accumulate classification + solver decision + response as pipeline progresses
  - [x] 6-3. Ensure Guard 1 runs before solver, Guard 2 runs before execution (both paths), Guard 3 runs before delivery
  - [x] 6-4. Each guard logs its `GuardResult.notes` for observability
  - [x] 6-5. Tests: end-to-end guard chain — verify cascading catches (classification error slips Guard 1 → caught by Guard 2; Guard 2 passes → wrong execution → caught by Guard 3)

## Decisions

- **Lightweight, not LLM-heavy** — Guards 1-3 are heuristic checks (string matching, entity lookup, Jaccard similarity), not additional LLM calls. This keeps latency low (~1-5ms per guard, not 1-2s for an LLM call).
- **Cascading by design** — Each guard sees all prior outputs. Guard N validates step N but implicitly re-validates steps 1..N-1 through the cumulative `GuardContext`. No single guard needs to be perfect.
- **Guards are opt-out, not opt-in** — Guards run by default. `DAN_GUARD_PIPELINE=0` disables all. Individual: `DAN_GUARD_CLASSIFICATION=0`, `DAN_GUARD_UNDERSTANDING=0`, `DAN_GUARD_RELEVANCE=0`.
- **Short-circuit, not just warn** — Guard 1 can short-circuit to a direct answer (project info) without invoking the solver. Guard 2 can halt execution and ask for clarification. Guard 3 is advisory (appends a note, doesn't block delivery).
- **Escape hatch for Guard 1** — Explicit external-action language ("search online", "check GitHub") bypasses the project-query short-circuit, so the user can still request external actions about a project.
- **Entity grounding is fuzzy, not exact** — Uses the same partial-match logic as `/project` command. Accepts substring matches and case-insensitive comparison.
- **Both execution paths** — Guards apply to both `_solver_path()` and the goal orchestrator path. Guard 2 checks `SolverDecision` in the solver path and goal context in the orchestrator path.
- **Backward compatible** — All new parameters optional. No changes to external APIs. Existing tests pass unchanged.
- **Low-confidence clarification requires entity matches** — Guard 1 only triggers the low-confidence clarification (< 0.6) when entity matches exist. Without entity matches, the default keyword classifier's 0.5 confidence for CONVERSATION is normal fallback behavior, not genuine ambiguity.
- **Threshold deviations from spec** — Guard 2 Jaccard threshold is 0.15 (spec: 0.2) and Guard 3 topic-match threshold is 0.25 (spec: 0.3). Started conservative (lower = fewer false positives) per the Notes guidance. Tighten after field observation.
- **`unresolved_refs` intentionally deferred** — `EntityContext.unresolved_refs` is always `[]`. The field exists in the data model for future use (e.g., NER-based detection of entity-like words that didn't match any project). No current guard reads it; implementing it would add complexity without benefit until a guard consumes it.
- **Guard 1 reclassification uses LLM classifier** — When Guard 1 triggers `reclassify`, it uses `classify_intent_llm()` (hybrid) rather than `classify_intent()` (keyword-only) to avoid reproducing the same misclassification.
- **Guard 2 on goal orchestrator path** — Uses a lightweight `SolverDecision` proxy constructed from `ConciergeGoal.description`. The proxy has empty assumptions and default 0.8 confidence, so the main active check is goal-paraphrase similarity.

## Notes

- Priority order: tasks 1 + 2 + 5 first (entity grounding + Guard 1 + prompts) — these fix the immediate "Kaggle → GitHub" class of errors. Tasks 3 + 4 + 6 are deeper structural improvements.
- CrewAI guardrails and Google ADK callbacks are the closest industry patterns. Our guards are similar to CrewAI's `guardrail` parameter but applied at the pipeline level rather than per-task.
- LangGraph Reflexion's 91% task completion improvement suggests Guard 3 (response relevance) has high ROI even after Guards 1-2 catch most errors.
- Guard 2's Jaccard similarity threshold (0.2) and Guard 3's topic-match threshold (0.3) should be tuned empirically. Start conservative (low thresholds = fewer false positives) and tighten based on observed misses.
- The `GuardContext` pattern is extensible: future guards (e.g., safety/policy guard, cost guard) can be added as additional functions consuming the same context.
