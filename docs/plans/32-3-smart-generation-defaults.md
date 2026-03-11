# 32-3: Smart Generation Defaults

**Parent:** [32-workflow-optimization](32-workflow-optimization.md)
**Status:** completed
**Goal:** Make generated workflows production-ready by default — automatically including retry policies, validation gates, feedback loops, and appropriate model tiers where the workflow structure calls for them.

## Problem

Generated workflows are structurally correct but bare. A "write a research paper" workflow produces a linear chain of LLM nodes with no:
- **Retry policies** — if an LLM call fails, the entire workflow fails
- **Validation gates** — no quality checks between stages
- **Feedback loops** — no iterative refinement unless explicitly requested
- **Model tiering** — all nodes use the same model, even when some (routing, planning) could use cheaper models

Users must explicitly ask for each of these. Most don't, and their workflows are fragile as a result.

## Design

### Default injection rules

| Rule | Condition | Default Applied |
|------|-----------|----------------|
| Retry on LLM nodes | Any `llm_operator` | `retry_policy: {max_retries: 2, backoff: "exponential", base_delay: 1.0}` |
| Retry on external tools | `tool_operator` calling web_search, web_fetch, send_email | `retry_policy: {max_retries: 3, backoff: "exponential", base_delay: 2.0}` |
| Validation gate | Workflow has a clear final-output node (no outgoing data edges) | Insert `validator` node before final node |
| Review on content | LLM node producing long-form content (reports, papers, memos — inferred from prompt keywords) | Suggest review_loop wrapping with `max_rounds=2` |
| Model tiering | Workflow has >3 LLM nodes and `DAN_ENABLE_TIER_POLICY=1` | Assign `routine` tier to planning/routing nodes, `standard` to content, `premium` to final output |
| Error notification | Workflow has >5 nodes | Set `on_failure` notification config |

### Two injection paths

1. **Codegen prompt injection** (primary) — Default-wiring rules are included in the codegen system prompt as instructions. The LLM generates code that already includes retry policies, validation gates, etc.

2. **Post-generation enrichment** (safety net) — After successful compilation, a `DefaultsEnricher` pass scans the graph and adds missing defaults non-destructively. Catches cases the LLM omitted.

### Default profiles

```python
class DefaultProfile(str, Enum):
    minimal = "minimal"    # no defaults — bare graph
    standard = "standard"  # retry + validation gate
    robust = "robust"      # retry + validation + review suggestion + tiering + notification
```

Profile selection: explicit user signal > domain profile (32-5) > `standard`.

### Opt-out

Users can suppress defaults with explicit instructions:
- "Build a simple chain without review" → no review loop
- "Skip validation" → no validator
- "Use the same model for all nodes" → no tiering

The codegen prompt recognizes suppression keywords.

## Tasks

- [x] 1. Default rules model
  - [x] 1-1. `GenerationDefaults` Pydantic model in new `src/dan/meta/generation_defaults.py`: per-rule enabled/config flags.
  - [x] 1-2. `DefaultProfile` enum with `minimal`, `standard`, `robust` presets.
  - [x] 1-3. Profile selection logic: user intent > domain profile > `standard` fallback.

- [x] 2. Codegen prompt updates
  - [x] 2-1. Add default-wiring instructions to `CodegenPromptBuilder._SYSTEM_PROMPT`. Include when-to-apply rules and when-to-skip signals.
  - [x] 2-2. Few-shot examples showing workflows with retry policies and validation gates.
  - [x] 2-3. Suppression signal recognition: "simple", "minimal", "without review", "no validation" → skip relevant defaults.

- [x] 3. Post-generation enrichment
  - [x] 3-1. `DefaultsEnricher` class: takes a compiled `Graph`, scans for missing defaults, applies non-destructively.
  - [x] 3-2. Retry policy injection: add `retry_policy` to LLM and external-tool nodes that lack one.
  - [x] 3-3. Validation gate injection: if no `validator` node exists before the terminal node, insert one with format/completeness rules.
  - [x] 3-4. Model tier assignment: assign tiers based on node role heuristics (classify by prompt content: planning→routine, content→standard, final→premium). Only when `DAN_ENABLE_TIER_POLICY=1`.
  - [x] 3-5. Wire enricher in `planner.py` after successful compilation, before saving graph.

- [x] 4. Intent compiler defaults
  - [x] 4-1. When intent compiler produces builder code (32-2), pass active `DefaultProfile` so compiled code includes retry/validation by default.
  - [x] 4-2. `review_loop()` convenience method defaults to `max_rounds=2` for content workflows.

- [x] 5. Tests
  - [x] 5-1. `DefaultsEnricher` adds retry to bare LLM nodes, adds validation gate, applies model tiers.
  - [x] 5-2. Suppression signals prevent default injection.
  - [x] 5-3. Enricher is non-destructive: existing retry policies are not overridden.
  - [x] 5-4. Integration: codegen with `standard` profile produces enriched graph.
  - [x] 5-5. Regression: existing quality suite fixtures still pass.

- [x] 6. Documentation
  - [x] 6-1. Update codegen prompt documentation.
  - [x] 6-2. Changelog entry.

## Files

| File | Action |
|------|--------|
| `src/dan/meta/generation_defaults.py` | Create — `GenerationDefaults`, `DefaultProfile`, `DefaultsEnricher` |
| `src/dan/meta/planner.py` | Modify — update `CodegenPromptBuilder._SYSTEM_PROMPT` with default-wiring instructions, suppression recognition |
| `src/dan/meta/planner.py` | Modify — wire enricher after compilation |
| `src/dan/meta/intent_compiler.py` | Modify — apply defaults to intent-compiled code |
| `tests/test_meta/test_generation_defaults.py` | Create — enricher + profile tests |

## Decisions

- (filled in during execution)

## Notes

- The enricher is non-destructive: it never removes or overrides user-specified settings. If a node already has a retry policy, the enricher skips it.
- Model tiering defaults require `DAN_ENABLE_TIER_POLICY=1` (31-1). When disabled, tiering defaults are skipped entirely.
- "Review on content" is the most aggressive default. It fires only when the workflow clearly produces long-form content (inferred from prompt keywords: "write", "report", "paper", "memo", "draft"). Short responses don't get review loops.
- The post-generation enricher is a safety net, not the primary path. The codegen prompt should produce correct defaults in most cases. The enricher catches omissions.
- `DefaultProfile.minimal` exists for power users and testing. It produces bare graphs identical to today's output.
- **Graph surgery complexity:** `DefaultsEnricher` operates on a compiled `Graph` model (Pydantic). Inserting a validator node requires: creating the node, creating new edges to/from it, removing the original edge between neighbors, and re-running `validate_graph()`. This is conceptually similar to `GraphMutator` operations. The enricher should use `GraphMutator` internally or replicate its edge-rewiring logic.
- **`codegen_prompts.py` does not exist** — the codegen system prompt lives in `meta/planner.py` (`CodegenPromptBuilder._SYSTEM_PROMPT`). References in task 2 should target that location.
