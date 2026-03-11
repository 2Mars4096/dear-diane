# 32-2: Intent Compiler Expansion

**Parent:** [32-workflow-optimization](32-workflow-optimization.md)
**Status:** completed
**Goal:** Expand the intent compiler's pattern catalog from 7 to ~15 patterns with auto-composition support, so the fast deterministic generation path handles a much larger fraction of user requests.

## Problem

The intent compiler (24-2) has 7 entries in `COVERAGE_CATALOG`: `linear_chain`, `review_loop`, `fan_out_fan_in`, `rag_qa`, `data_pipeline`, `tool_augmented`, `human_gate`. Everything outside falls to free-form builder codegen, which is slower (~2-3x more tokens), less predictable, and more error-prone.

Common real-world requests that currently miss the intent fast path:
- "Research and write with review" → chain + review_loop composition
- "Analyze multiple items then compare" → fan_out + merge + analysis
- "Build a pipeline with web search and code analysis" → tool_chain + review
- "Create a screening pipeline" → fan_out + scoring + chain
- "Build a content pipeline with editing loop" → chain + review_loop

These are compositions of existing patterns, but `CoverageChecker` matches exactly one pattern — it can't compose.

## Design

### New catalog entries

| Pattern | `StageType` Combination | Compiles Using |
|---------|------------------------|----------------|
| `tool_chain` | `[tool_call, transform]` in linear sequence | `wf.tool_chain()` (32-1) |
| `comparison` | `fan_out` + `transform` (compare) | `wf.map_reduce()` + comparison LLM |
| `research_review` | `transform` sequence + `review_loop` | `wf.chain()` + `wf.review_loop()` |
| `web_briefing` | `tool_call` (web_search) + `transform` chain | `wf.tool_chain()` with web tools |
| `code_analysis` | `tool_call` (file_read) + `code_execution` + `transform` | `wf.tool_chain()` with code tools |
| `document_pipeline` | `tool_call` (file) + `transform` + `tool_call` (file_write) | `wf.tool_chain()` with file tools |
| `multi_source_merge` | multiple `tool_call` sources + `fan_out` + `transform` | parallel fan-out + reduce |
| `iterative_improvement` | `transform` + `review_loop` with goal condition | chain + `goal_loop` |

### Auto-composition

When `CoverageChecker` detects a `WorkflowIntent` whose stages span multiple catalog patterns:

1. **Decompose** — Partition stages into runs of same-pattern stages (greedy left-to-right scan).
2. **Check** — Every constituent pattern must be in the catalog.
3. **Compose** — Emit builder code that chains convenience methods with data-flow wiring between segments.

Example: "Research papers in parallel, synthesize, review"
- Stages: `[fan_out(papers), transform(synthesize), review_loop(review)]`
- Decomposed: `fan_out_fan_in` + `linear_chain(1)` + `review_loop`
- Composed code:

```python
wf = workflow("lit_review")
summaries = wf.map_reduce("{papers}", "Summarize this paper", "Synthesize findings")
final = wf.review_loop(
    writer_prompt=f"Polish: {summaries}",
    reviewer_prompt="Check for completeness",
)
```

### Domain-aware skeleton selection

When `detect_domain()` (31-21) identifies the domain before intent compilation, the compiler biases toward domain-preferred patterns:

| Domain (`detect_domain()` key) | Preferred Patterns |
|--------|-------------------|
| `paper_rendering` / `literature_review` | `research_review`, `fan_out_fan_in`, `rag_qa` |
| `equity_research` | `web_briefing`, `code_analysis`, `research_review` |
| `data_analysis` | `data_pipeline`, `tool_chain`, `tool_augmented` |
| `code_generation` | `tool_chain`, `iterative_improvement`, `linear_chain` |

Bias is a tie-breaker, not a hard constraint. If the user explicitly requests a different pattern, honor it.

## Tasks

- [x] 1. New pattern definitions
  - [x] 1-1. Add 8 new entries to `COVERAGE_CATALOG` with `description`, `stage_types`, `composable` flag.
  - [x] 1-2. Compilation functions for each new pattern using convenience layer methods (32-1). Add to `IntentCompiler.compile()` dispatch.
  - [x] 1-3. Tests: each new pattern compiles to a valid graph via `build()`.

- [x] 2. Auto-composition
  - [x] 2-1. `CoverageChecker.check()` upgrade: when single-pattern match fails, attempt decomposition into constituent patterns (greedy scan). Return `recommendation: "compose"` when all constituents are covered.
  - [x] 2-2. `IntentCompiler.compile_composed()` — emit builder code that chains convenience methods for multi-pattern intents. Data flow: previous segment's return `NodeRef` feeds next segment's input.
  - [x] 2-3. Composition limit: max 3 constituent patterns. Beyond that, fall through to codegen.
  - [x] 2-4. Tests: 3-4 composition scenarios (research+review, fan_out+chain, tool_chain+review, multi_source+analysis).

- [x] 3. Domain-aware selection
  - [x] 3-1. Accept optional `domain: str | None` parameter in `IntentCompiler.compile()`.
  - [x] 3-2. Domain → preferred pattern mapping dict. When domain is set and multiple patterns could match, prefer domain-preferred patterns.
  - [x] 3-3. Tests: same intent with different domains produces different pattern selections.

- [x] 4. Intent extraction prompt update
  - [x] 4-1. Update the intent extraction system prompt (`INTENT_EXTRACTION_SYSTEM_PROMPT`) to recognize new pattern keywords.
  - [x] 4-2. Add few-shot examples for composition scenarios.
  - [x] 4-3. Tests: intent extraction produces correct `WorkflowIntent` for new patterns.

- [x] 5. Fallback behavior
  - [x] 5-1. When composition fails or a pattern is unsupported, fall through to builder codegen with a diagnostic note (which patterns were recognized, which weren't).
  - [x] 5-2. Telemetry: emit `metadata.intent_compiler_result: "compiled" | "composed" | "fallback"` on the `chat_turn` event for hit-rate tracking.

- [x] 6. Documentation
  - [x] 6-1. Update `docs/llm-api-guide.md` expanded pattern catalog section.
  - [x] 6-2. Changelog entry.

## Files

| File | Action |
|------|--------|
| `src/dan/meta/intent_compiler.py` | Modify — new catalog entries, auto-composition, domain wiring |
| `src/dan/meta/intent_schema.py` | Potentially modify — if new `StageType` values needed |
| `src/dan/meta/intent_extraction.py` | Modify — update `INTENT_EXTRACTION_SYSTEM_PROMPT` with new pattern keywords |
| `src/dan/meta/planner.py` | Modify — wire domain detection into intent path |
| `docs/llm-api-guide.md` | Modify — expanded pattern catalog |
| `tests/test_meta/test_intent_compiler.py` | Modify/extend — new pattern + composition tests |

## Decisions

- (filled in during execution)

## Notes

- Auto-composition is limited to 2-3 constituent patterns. More complex intents should fall through to codegen — the composition logic becomes fragile beyond that.
- Domain-aware selection is a bias, not a hard constraint. If the user explicitly requests a pattern outside their domain's preferences, honor it.
- The intent compiler should never silently drop parts of the user's request. If a feature can't be mapped to a pattern, include it as a diagnostic note for the codegen fallback.
- New `StageType` values should only be added if they represent genuinely new stage semantics. Most new patterns are compositions of existing stage types.
- `CoverageChecker.SUPPORTED_TYPES = set(StageType)` — all 7 individual stage types are already "supported". The gap is at the **pattern catalog** level, not stage-type level. The coverage checker needs upgrading to detect pattern-level composition, not just stage-type support.
- Telemetry tracking (task 5-2) uses the existing `TelemetryStore` (31-20) — no new emission infrastructure needed, just a `metadata` field on the `chat_turn` event.
