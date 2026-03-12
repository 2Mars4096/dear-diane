# 33-10: Semantic Correctness Patch

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** completed
**Goal:** Fix the contract bugs and eval blind spots that make "passing" graphs fail at runtime. The pipeline produces correct topology; this plan makes the content inside each node correct too.

## Problem

Post-33-9, the headline pass rate is 46.7% (build lane) with quality scores averaging 94.3 when a graph is produced. But an LLM-as-judge review of every stored graph revealed that many "passing" graphs have fundamental semantic bugs that would prevent real-world use:

1. **Review-loop condition polarity mismatch.** `ReviewRequirement.condition` defaults to `"quality_score >= 8"` (a *stop-when-satisfied* condition), but `Builder.review_loop()` interprets its `condition` parameter as a *continue-while* condition and defaults to `"quality_score < 8"`. When the intent compiler passes `>= 8` through, the loop runs while `quality_score >= 8` — i.e., continues when quality is already high and stops when it's low. Exactly backwards. Affects 8+ generated graphs.

2. **Eval pass/fail ignores fixture expectations.** Each prompt fixture defines `expected.min_nodes`, `expected.max_nodes`, `expected.topology`, `expected.node_types` — but `_determine_status()` never reads these fields. Any structurally valid graph passes, including 1-node graphs for 10-step prompts. The reported pass rate overstates real quality.

3. **Tool-call nodes default to `web_search`.** `_compile_tool_call()` and `_build_tool_call()` fall back to `tool_id="web_search"` when `stage.config` lacks a `tool_id`. The runtime registers 39 tools including `file_read`, `csv_read`, `pdf_read`, `send_email` — but the LLM's extracted intent often omits `tool_id`, so file-reading nodes, CSV-reading nodes, and email-sending nodes all silently become web searches. Affects 7+ graphs.

4. **Code-execution nodes default to stub.** `_compile_code_execution()` and `_build_code_execution()` fall back to `code="result = 'done'"` — a no-op placeholder. Statistics, chart generation, data processing nodes all produce `result = 'done'` at runtime. Affects 5+ graphs.

5. **Codegen LLM has no tool catalog.** When the codegen path fires (not intent compiler), the LLM generates builder code without knowing which `tool_id` values are valid. It invents tool IDs or defaults to `web_search`. The codegen system prompt does not include the available tool registry.

6. **No automated semantic scoring.** The eval harness checks structural validity and computes a quality score (node count, topology, pattern presence) but cannot assess whether node prompts are specific, data flows are correct, or the graph would produce useful output. The transcript's LLM-as-judge review was manual and one-off.

## Approach

Six focused patches, ordered by impact. Each is independently shippable. No new architecture — pure correctness fixes and eval hardening.

## Tasks

### A. Fix review-loop condition contract (Critical)

- [x] 1. **Unify condition semantics**
  - [x] 1-1. Change `ReviewRequirement.condition` default from `"quality_score >= 8"` to `"quality_score < 8"` in `intent_schema.py` to match the builder's continue-while convention.
  - [x] 1-2. Update all few-shot examples in `intent_extraction.py` that use `"quality_score >= 8"` to `"quality_score < 8"`.
  - [x] 1-3. Update the `_pattern_review_loop()` default in `structural_mutations.py` (`"needs_revision == True"` → `"quality_score < 8"` for consistency with builder).
  - [x] 1-4. Add a normalization step in `IntentCompiler._build_review_loop()` and `_compile_review_loop()`: if the condition uses `>=` or `>` with a numeric threshold, flip to `<` / `<=` (stop-condition → continue-condition). Log a warning when normalization fires.

- [x] 2. **Validate condition variables against state schema**
  - [x] 2-1. `Builder.review_loop()` already defines its state schema as `{"draft": str, "quality_score": int, "feedback": str}` plus any custom `review_fields`. Add a validation step that checks whether the condition string references only variables present in the state schema. If it references unknown variables (e.g., `content_approved`, `citations_verified`, `bug_count`), replace with the default `"quality_score < 8"` and log a warning.
  - [x] 2-2. Add unit tests: condition with valid variable passes through; condition with unknown variable gets normalized; `>=` polarity gets flipped.

### B. Enforce fixture expectations in eval pass/fail (Critical)

- [x] 3. **Add expectation-fit gate to `_determine_status()`**
  - [x] 3-1. When `fixture.expected` is non-empty and a graph was created, check:
    - `graph_summary.node_count >= expected.min_nodes` (if set)
    - `graph_summary.node_count <= expected.max_nodes` (if set)
    - Required `expected.node_types` are present in `graph_summary.node_types` (if set)
    - Required `expected.topology` features are present: `"review_loop"` requires `has_loop`, `"fan_out"` requires `has_fan_out`, `"chain"` requires `node_count >= 2` (if set)
  - [x] 3-2. Graphs that fail expectation checks get `status="failed"`, `failure_mode="expectation_mismatch"` with details in a new `expectation_errors` field on `EvalRecord`.
  - [x] 3-3. Add `expectation_errors: list[str]` field to `EvalRecord`.
  - [x] 3-4. Report generator: add expectation-mismatch breakdown table.

### C. Eliminate unsafe tool_id and code defaults (High)

- [x] 4. **Replace `web_search` fallback with explicit failure**
  - [x] 4-1. In `IntentCompiler._compile_tool_call()` and `_build_tool_call()`, when `stage.config` has no `tool_id`: instead of defaulting to `"web_search"`, attempt to infer the correct tool from the stage `name` and `description` using a simple keyword map (e.g., "read file" → `file_read`, "CSV" → `csv_read`, "PDF" → `pdf_read`, "email" → `send_email`, "search" → `web_search`). If no match, use `"web_search"` but add a quality concern to the stage.
  - [x] 4-2. In `structural_mutations.py` `_try_macro_from_text()`, same pattern: infer tool from text before defaulting.

- [x] 5. **Replace `result = 'done'` stub with description-based code generation**
  - [x] 5-1. In `IntentCompiler._compile_code_execution()` and `_build_code_execution()`, when `stage.config` has no `code`: instead of `"result = 'done'"`, generate a minimal template from the stage description. E.g., if description is "compute statistics", emit `result = {"status": "computed", "description": "<stage.description>"}`. Still a placeholder, but at least descriptive and distinguishable from a no-op.
  - [x] 5-2. Add a quality flag when code is auto-generated from description (not user-provided). The quality gate can warn "code node uses auto-generated placeholder".

### D. Inject tool catalog into codegen prompt (High)

- [x] 6. **Include registered tool IDs in the codegen system prompt**
  - [x] 6-1. In `planner.py` `CodegenPromptBuilder`, query the `ToolRegistry` (or `discovery.discover_tools()`) to get the list of registered tool IDs with their one-line descriptions.
  - [x] 6-2. Append a "## Available Tools" section to the codegen system prompt: `"When creating tool nodes, use one of these registered tool_ids: file_read (read file contents), csv_read (parse CSV), pdf_read (extract PDF text), web_search (search the web), send_email (send email), ..."`. Cap at ~20 tools to avoid prompt bloat.
  - [x] 6-3. Add an instruction: "Do NOT invent tool_ids. If no registered tool matches the needed capability, use `wf.code()` with inline Python instead of `wf.tool()` with a made-up tool_id."

### E. Add LLM-as-judge scoring to eval harness (Medium)

- [x] 7. **Automated semantic quality assessment**
  - [x] 7-1. Add a `--judge` flag to the eval CLI. When enabled, after each graph is produced, send the prompt + graph JSON to an LLM judge (use the configured chat model) with a scoring rubric:
    - **Prompt faithfulness** (0-10): Does the graph structure match what the prompt asked for?
    - **Node specificity** (0-10): Are node prompts specific enough to produce useful output?
    - **Data flow correctness** (0-10): Do edges, ports, and template variables connect correctly?
    - **Executability** (0-10): Would this graph produce useful output if run?
  - [x] 7-2. Store judge scores in `EvalRecord` as `judge_scores: dict[str, int] | None`.
  - [x] 7-3. Report generator: add judge score summary (avg per tier, worst 5 graphs).
  - [x] 7-4. Do NOT use judge scores for pass/fail — keep them advisory. The structural + expectation gates (tasks 3, B) are the hard gates.

### F. Intent extraction tool-awareness (Medium)

- [x] 8. **Teach intent extraction about available tools**
  - [x] 8-1. In `extract_workflow_intent()`, include the available tool IDs in the system prompt so the LLM can set correct `config.tool_id` values in `tool_call` stages instead of omitting them.
  - [x] 8-2. Update the few-shot examples in `INTENT_FEW_SHOT_EXAMPLES` to use a variety of tool IDs beyond just `web_search` and `file_read`. Add examples with `csv_read`, `pdf_read`, `python_eval`, `shell_command`.

## Key Files

| File | Action |
|------|--------|
| `src/dan/meta/intent_schema.py` | **Modify** — flip `ReviewRequirement.condition` default |
| `src/dan/meta/intent_compiler.py` | **Modify** — condition normalization, tool_id inference, code stub improvement |
| `src/dan/meta/intent_extraction.py` | **Modify** — fix few-shot conditions, add tool catalog to extraction prompt |
| `src/dan/meta/structural_mutations.py` | **Modify** — align review_loop default condition, tool inference |
| `src/dan/meta/planner.py` | **Modify** — inject tool catalog into codegen prompt |
| `src/dan/meta/graph_quality.py` | **Review** — may need new quality flags for auto-generated code/tool stubs |
| `tests/eval/__init__.py` | **Modify** — add `expectation_errors` and `judge_scores` to `EvalRecord` |
| `tests/eval/runner.py` | **Modify** — expectation-fit gate in `_determine_status()` |
| `tests/eval/report.py` | **Modify** — expectation mismatch table, judge score summary |
| `tests/eval/__main__.py` | **Modify** — `--judge` flag |

## Success Criteria

- [x] Review loops use correct continue-while semantics — no more `quality_score >= 8` passed as a continue condition
- [x] Conditions referencing unknown state variables are normalized to the safe default
- [x] Eval pass rate drops (honestly) when expectation-fit gate is enabled — graphs with wrong topology or node count are caught
- [x] Tool nodes use contextually appropriate tool_ids, not universal `web_search`
- [x] Code nodes have at least descriptive placeholders, not `result = 'done'`
- [x] Codegen LLM knows which tools are available
- [x] LLM-as-judge scoring is available via `--judge` and produces actionable per-graph assessments
- [ ] Re-run battery with expectation gate: expect pass rate to drop to ~30-35% (honest baseline), then climb as A/C/D/F fixes take effect

## Implementation Order

```
A (review-loop condition) ← highest ROI, pure correctness bug
  ↓
B (expectation-fit gate) ← makes pass rate honest, can run in parallel with A
  ↓
C + D (tool/code defaults + codegen prompt) ← address generation quality
  ↓
F (intent extraction tool-awareness) ← feeds C
  ↓
E (LLM-as-judge) ← diagnostic, do last
```

A and B are independent and can be done in parallel. C, D, and F are related and should be done together. E is standalone and lowest priority.

## Decisions

- Review-condition normalization stays conservative by defaulting unknown or malformed conditions to `quality_score < 8` instead of guessing at custom state variables or partially parsed stop-conditions.
- Tool inference should only match phrase-level, word-boundary keywords; generic fallbacks like bare `file` are too risky because they create false positives (`profile` → `file_read`).

## Notes

- This plan does NOT address the `timeout_planning` reliability problem (72% of failures). That is an LLM API infrastructure issue, not a pipeline correctness issue. A separate plan for provider fallback / shorter classification timeouts would address that.
- The condition polarity bug (A) is the most impactful single fix. It affects every review-loop graph produced by the intent compiler — the most common non-trivial pattern.
- The expectation-fit gate (B) will cause the reported pass rate to drop. This is correct behavior — the current rate is inflated by counting semantically wrong graphs as passes.
- The LLM-as-judge (E) is explicitly advisory, not a gate. Using LLM judgment for pass/fail would introduce non-determinism into the eval. Keep it as a diagnostic signal.
- Code-node stubs (C.5) are a partial fix. The real solution is either teaching the codegen LLM to write real Python (prompt engineering) or falling back to `llm_operator` when real code isn't available. The template approach in 5-1 at least makes stubs distinguishable.
- The tool inference map (C.4-1) should be kept small and conservative — only map unambiguous keywords. Ambiguous cases should stay as `web_search` with a quality warning rather than risk wrong tool assignment.
- Final follow-up hardening extended review-condition normalization to negate whole threshold-based stop expressions (`and`/`or`, reversed threshold forms like `8 <= quality_score`) while preserving quoted literals and falling back safely on malformed expressions.
- Final code-review patches fixed edge cases in composed compilation wiring (bypassing conditional gates), orphaned subgraphs in loop unwrapping, JSON extraction regex fragility, and parallelize macro truncation.
