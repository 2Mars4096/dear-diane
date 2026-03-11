# 32-6: Workflow Convenience Gaps Patch-Up

**Parent:** [32-workflow-optimization](32-workflow-optimization.md)
**Status:** completed
**Goal:** Close the three highest-leverage ergonomics gaps surfaced during the Phase 32 review: composed conditional branching, configurable review-loop criteria, and compound structural follow-up mutations.

## Problem

Phase 32 materially improved the NL→workflow pipeline, but three gaps still force users and LLMs to drop from convenience helpers to raw DSL:

1. **No composed branching convenience.** `wf.if_else()` (line 335) and `wf.gate(..., gate_mode="if_else")` (line 363) both exist as raw primitives, but neither wires up the then/else branches. A user who wants "if sentiment is positive → summarize, otherwise → alert" must manually create the gate, create both branch nodes, and wire `gate["true"] >> summarize`, `gate["false"] >> alert`. This is the third most common control-flow shape after chains and loops.
2. **`wf.review_loop()` is hardcoded to one review pattern.** The condition (`quality_score < 8`), reviewer output schema (`quality_score: int`, `feedback: str`), loop ports (`draft`, `quality_score`, `feedback`), and writer feedback injection (`\n\nPrevious feedback: {feedback}`) are all baked in. Any review pattern that doesn't fit this shape ("until citations_verified", "until accuracy > 0.9") must abandon the convenience helper entirely.
3. **Structural mutation dispatch is single-shot.** `dispatch_structural_mutation()` matches the first keyword and stops (line 569-572 in `structural_mutations.py`). Compound NL follow-ups ("add a review loop and fan out the research step") fall through to the slower LLM mutation/codegen path.

## Existing Infrastructure

| What | Where | Relevance |
|------|-------|-----------|
| `wf.if_else()` | `builder.py:335` | Raw `if_else` node type — no branch wiring |
| `wf.gate()` | `builder.py:363` | Raw `gate` node with `gate_mode` — has `true`/`false` output ports |
| `DEFAULT_OUTPUT_PORTS["gate"]` | `compiler.py:58` | Default output is `"true"` for if_else gates |
| `wf.review_loop()` | `builder.py:663` | Hardcoded condition, schema, ports |
| `StageType` enum | `intent_schema.py:20` | 7 values, no `conditional` |
| `StageIntent` model | `intent_schema.py:67` | Has `review: ReviewRequirement` but no conditional fields |
| `ReviewRequirement` | `intent_schema.py:54` | Already has `condition` and `reviewer_prompt` — but `review_loop()` ignores `condition` |
| `dispatch_structural_mutation()` | `structural_mutations.py:555` | First-match-only keyword loop |
| `_atomic_macro` decorator | `structural_mutations.py:106` | Per-macro deep-copy rollback |
| `_MACRO_KEYWORDS` | `structural_mutations.py:513` | Longest-first sorted keyword dict |

## Design

### 1. Composed branching convenience: `wf.branch()`

A convenience method that creates a gate + then-branch node + else-branch node and wires them together. Compiles down to the existing `gate` node type (not `if_else`), because `gate` has explicit `true`/`false` output ports.

```python
gate_ref, then_ref, else_ref = wf.branch(
    condition="sentiment > 0.5",
    then_prompt="Summarize the positive findings",
    else_prompt="Draft an alert about negative sentiment",
    name="sentiment_check",
)
# gate_ref for upstream wiring, then_ref / else_ref for downstream wiring
```

Returns a 3-tuple `(gate_ref, then_ref, else_ref)` so upstream wiring can target the gate directly and downstream wiring can continue from either branch. Internally creates:
- A `gate` node with `gate_mode="if_else"` and the given condition
- A `then` LLM node wired from `gate["true"]`
- An `else` LLM node wired from `gate["false"]`

Optionally accept `then_model` / `else_model` for different model tiers on each branch. For non-LLM branches (tool calls), users drop to raw `wf.gate()` — keep this helper narrow.

### 2. Configurable `review_loop()` criteria

Make the three coupled hardcoded values (`condition`, reviewer `output_schema`, loop ports) configurable via three optional kwargs (`condition`, `review_fields`, `feedback_key`) while preserving the current defaults.

```python
# Current default behavior — unchanged
result = wf.review_loop("Write a draft", "Review for quality")

# Custom criteria
result = wf.review_loop(
    "Write a draft with citations",
    "Verify all citations are real and correctly attributed",
    condition="citations_valid == true",
    review_fields={"citations_valid": {"type": "boolean"}, "issues": {"type": "string"}},
    feedback_key="issues",
)
```

- `condition`: replaces hardcoded `"quality_score < 8"` on the while_loop. Default: `"quality_score < 8"`.
- `review_fields`: replaces the hardcoded `output_schema` on the reviewer node. Default: `{"quality_score": {"type": "integer"}, "feedback": {"type": "string"}}`.
- `feedback_key`: which review field is injected back into the writer prompt. Default: `"feedback"`.
- Loop input/output port names derived from `review_fields` keys + `"draft"`.

### 3. Compound structural mutation dispatch

Extend `dispatch_structural_mutation()` to extract *all* non-overlapping macro matches from one message and execute them sequentially with message-level atomic rollback.

Current flow:
```
match first keyword → extract params → execute one macro → return
```

New flow:
```
scan all keywords → collect non-overlapping matches → snapshot graph once →
execute macros sequentially → if any fails, restore snapshot → return compound result
```

Key design choices:
- Message-level snapshot happens *outside* the per-macro `@_atomic_macro` decorator. The decorator still handles individual macro failures, but the outer snapshot ensures the full compound sequence is all-or-nothing.
- `DispatchResult` gains `results: list[MutationMacroResult]` for compound reporting.
- The `chat_manager.py` fast-path reports all applied macros in one summary message.
- Per-macro parameter extraction and target resolution run independently for each match.

### 4. Intent compiler conditional path

Add `StageType.conditional` to the intent schema and a `ConditionalRequirement` model to `StageIntent` so the deterministic compiler can target `wf.branch()`.

```python
class ConditionalRequirement(BaseModel):
    condition: str = "result == true"
    then_description: str = ""
    else_description: str = ""

class StageType(str, Enum):
    # ... existing values ...
    conditional = "conditional"
```

`StageIntent` gains `conditional: ConditionalRequirement | None = None` (mirroring the `review: ReviewRequirement | None` pattern). The intent compiler's `_compile_stage` dispatch table gains a `_compile_conditional` entry.

Add `"conditional"` to `COVERAGE_CATALOG` and the intent extraction system prompt.

### Explicitly deferred

- Typed item schemas for `map_reduce()` — speculative until Phase 33 shows a real gap
- Decomposer backtracking in `_try_decompose()` — greedy works for most real intents
- Broader intent taxonomy (full routing, parallel-branch families) — wait for evaluation data
- Multi-node branch bodies in `wf.branch()` — raw `wf.gate()` covers this for power users
- Context-manager ref ergonomics (`for_each` / `while_loop` / `parallel_subagents` / `orchestrator` yielding usable refs directly, or exposing a `.ref`/feed-style API) — this was identified in the earlier review as a high-leverage verbosity reduction, but it is intentionally held out of 32-6 to keep the patch slice narrow and focused on the three most review-confirmed gaps

## Tasks

- [x] 1. `wf.branch()` convenience method
  - [x] 1-1. Add `branch(condition, then_prompt, else_prompt, *, name, then_model, else_model) -> tuple[NodeRef, NodeRef, NodeRef]` to `WorkflowBuilder` in `builder.py`. Creates a `gate` node (`gate_mode="if_else"`), a then-branch LLM node wired from `gate["true"]`, and an else-branch LLM node wired from `gate["false"]`. Returns `(gate_ref, then_ref, else_ref)`.
  - [x] 1-2. Add `"conditional_branch"` entry to `COVERAGE_CATALOG` in `intent_compiler.py` with `stage_types: [StageType.conditional]`.
  - [x] 1-3. Add `StageType.conditional` to `intent_schema.py`. Add `ConditionalRequirement` model. Add `conditional: ConditionalRequirement | None` field to `StageIntent` with a model validator requiring it when `stage_type == conditional`.
  - [x] 1-4. Add `_compile_conditional()` to `IntentCompiler` targeting `wf.branch()`.
  - [x] 1-5. Add `conditional` to `_STAGE_TYPE_DESCRIPTIONS` and intent extraction system prompt in `intent_extraction.py`.
  - [x] 1-6. Add `wf.branch()` to the codegen system prompt DSL reference and one few-shot example in `planner.py`.
  - [x] 1-7. Tests: 16 tests — basic branch, branch after chain, branch with model overrides, custom name, intent compiler round-trip, coverage checker includes conditional, backward compat (existing `wf.if_else()` and `wf.gate()` unchanged).

- [x] 2. Flexible `review_loop()` criteria
  - [x] 2-1. Add optional `condition`, `review_fields`, `feedback_key` parameters to `review_loop()` in `builder.py`. Default values preserve current behavior exactly. Derive loop port names from `review_fields` keys + `"draft"`. Build reviewer `output_schema` from `review_fields`. Inject `feedback_key` into writer prompt suffix.
  - [x] 2-2. Update `_compile_review_loop()` and `_compile_segment()` (pattern `"review_loop"`) in `intent_compiler.py` to pass through `ReviewRequirement.condition` to the `condition` kwarg. Currently `review_loop()` ignores `ReviewRequirement.condition` — wire it.
  - [x] 2-3. Update codegen system prompt `review_loop()` reference in `planner.py` to show the optional `condition` / `review_fields` kwargs.
  - [x] 2-4. Tests: 11 tests — default behavior unchanged, custom boolean condition, custom review fields, feedback_key override, intent compiler passes through ReviewRequirement.condition.

- [x] 3. Compound structural follow-up mutations
  - [x] 3-1. Add `dispatch_compound_mutations(graph, user_text) -> DispatchResult` to `structural_mutations.py`. Scans all `_MACRO_KEYWORDS` and collects every non-overlapping match (skip keywords whose text span overlaps an already-matched longer keyword). For each match, independently extract parameters and resolve target nodes.
  - [x] 3-2. Execute the matched macros sequentially on a message-level deep copy. If all succeed, commit to the original graph. If any fails, restore the snapshot. Add `results: list[MutationMacroResult]` and `macro_names: list[str]` fields to `DispatchResult` for compound reporting.
  - [x] 3-3. Update the structural mutation fast-path in `chat_manager.py` to call `dispatch_compound_mutations()` instead of `dispatch_structural_mutation()`. Format the summary message to list all applied macros.
  - [x] 3-4. Keep `dispatch_structural_mutation()` as-is for backward compatibility (existing callers). `dispatch_compound_mutations()` is the new primary entry point.
  - [x] 3-5. Tests: 11 tests — single-macro behavior unchanged, two-macro compound, per-macro parameter extraction in compound, failure rollback, overlapping keyword dedup, DispatchResult extended fields, original dispatcher backward compat.

- [x] 4. Docs
  - [x] 4-1. Update `docs/llm-api-guide.md` with `wf.branch()` API, extended `review_loop()` kwargs, and compound dispatch paragraph.
  - [x] 4-2. No new files or patterns introduced — no `docs/architecture.md` update needed.
  - [x] 4-3. Update `docs/changelog.md` and `docs/todo.md` after implementation.

## Decisions

- `wf.branch()` returns a 3-tuple `(gate_ref, then_ref, else_ref)`, not a single ref — upstream wiring targets `gate_ref`, and both branches remain available for downstream wiring. This differs from `chain()` / `review_loop()` which return a single terminal ref.
- `wf.branch()` compiles to a `gate` node (not `if_else` node type) because `gate` has explicit `true`/`false` output ports that the compiler already handles.
- `review_loop()` configurability is done via three optional kwargs (`condition`, `review_fields`, `feedback_key`) that default to current behavior. No new method signature.
- Compound mutations use a two-layer atomicity: message-level snapshot (new) wrapping per-macro `@_atomic_macro` snapshots (existing). The outer layer ensures all-or-nothing at the compound level.
- `dispatch_structural_mutation()` is preserved unchanged for backward compatibility. `dispatch_compound_mutations()` is a new function.
- This is a patch slice, not a broad re-open. Implementation priority if time-constrained: (1) conditional branch, (2) flexible review_loop, (3) compound mutations.
- If Phase 33 still shows a lot of manual `NodeRef(...)` recovery after context managers in generated builder code, treat that as the next ergonomics slice rather than expanding 32-6 further.

## Notes

- The core problem is ergonomic completeness, not raw power. All three gaps have working low-level primitives; what's missing is the convenience layer.
- `ReviewRequirement.condition` already exists in `intent_schema.py` (line 58) but is ignored by `review_loop()` — this is a bug-adjacent gap, not new architecture.
- The `if_else` node type in `builder.py:335` is kept as-is. `wf.branch()` targets the `gate` node type instead because `gate` is the runtime primitive the engine actually executes for conditional branching (Phase 6-12 control flow consolidation).
