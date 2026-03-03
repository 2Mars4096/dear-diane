# 16-4: Loop as Context Manager

**Parent:** [16-execution-primitives](16-execution-primitives.md)
**Status:** not-started
**Goal:** Extend loop nodes (`GateNode(while)`, `WhileLoopNode`) with feedback selectors that declare which body outputs feed back to the next iteration vs. which are side-effect artifacts — giving loops explicit control over what context cycles back, enabling cleaner iteration patterns and reducing unnecessary context accumulation.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `GateNode(while)` | `models/control_flow.py` | `condition`, `max_iterations`, `state_schema`, `state_defaults` | All body outputs feed back via back-edges; no selective filtering; `state_schema` keys are matched by name but there's no explicit "these outputs are feedback, these are not" |
| Gate while-loop scheduling | `engine/scheduler.py` | Detects gate-controlled cycles, iterates cycle regions, injects loop-feedback as virtual inputs | Injects *all* outputs of the cycle's last node back to the cycle's first node; no filtering at the scheduler level |
| `WhileLoopNode` | `models/control_flow.py` | `condition`, `body_graph`, `max_iterations`, `state_schema`, `state_defaults`, `compaction_rule`, `failure_policy` | Entire `body_output` becomes next iteration's `working_data`; compaction reduces history but doesn't filter fields |
| `WhileLoopExecutor` | `executors/control_flow.py` | Iterates body sub-graph; merges all body outputs into `working_data`; applies compaction to history | `working_data = {**working_data, **body_output}` — no field filtering; all keys propagate |
| `CompactionRule` | `models/context.py` | `CompactionStrategy` enum: `sliding_window`, `keep_last`, `diff_based`, `summarize`, `none`; fields: `strategy`, `window_size`, `max_tokens` | Compacts the *history list* (list of past iterations); doesn't filter *which fields* cycle back per iteration |
| `state_schema` | `models/control_flow.py` | Dict[str, schema] on GateNode/WhileLoopNode; `state_defaults` for initial values | Defines typed loop state variables; scheduler auto-merges matching keys from body outputs back into scope | But body outputs that *don't* match `state_schema` still flow as regular data; no "this output is terminal, don't feed back" |
| `LocalStateManager` | `engine/context_runtime.py` | Node-scoped working memory; `get_scope()`/`set_scope()`/`delete_scope()` | Per-node state bag; used for loop counters and `state_schema` state; no feedback vs. artifact distinction |
| `ContextProjection` | `models/context.py` | `name`, `context_keys` (list[str]), `local_state_keys` (list[str]), `artifact_uris` (list[str]) | **Dead code** (noted in 14-* plans) — model exists but no executor or scheduler calls it. Designed for selecting which context/state/artifact keys to expose at scope boundaries. NOTE: does NOT have `include`/`exclude`/`rename`/`transform` fields — those are a different concern (output port filtering). |
| Builder while-loop CM | `builder/builder.py` | `wf.while_loop(name, condition, max_iterations)` context manager | Creates sub-graph body; no feedback selector parameter |
| Builder for-each CM | `builder/builder.py` | `wf.for_each(name, items, parallelism)` context manager | No feedback relevance for for-each (single pass) but feedback projection could filter what reaches the merge |
| Gate cycle scheduling | `engine/scheduler.py` | `cycle_regions: dict[str, set[str]]` maps gate_id to cycle member node IDs; `back_edges: dict[str, str]` maps gate_id to back-edge target; inline feedback injection in `_execute_with_cycles` (~line 774) sets `continue_data` entries as virtual inputs via `state.port_data.set()` | Feedback injection is inline code (not a separate function); injects *all* keys from gate's `continue` output to cycle's first node; could be filtered at this injection point |

## Tasks

- [ ] 1. Define feedback selector model
  - [ ] 1-1. Create `FeedbackSelector` model in `models/context.py` (alongside `CompactionRule` and `ContextProjection`): `include` (list[str] | None — output port names to feed back; if set, only these ports cycle back), `exclude` (list[str] | None — output port names to suppress from feedback; if set, all ports *except* these cycle back), `rename` (dict[str, str] | None — remap output port names before feeding back, e.g., `{"improved_draft": "draft"}` so the next iteration receives it under the original name), `transform` (str | None — expression evaluated on feedback dict to produce the actual feedback, e.g., `"{'summary': inputs['result'][:200]}"` for truncation).
  - [ ] 1-2. Validation: `include` and `exclude` are mutually exclusive (raise if both set). At least one must be non-None for the `FeedbackSelector` to have any effect (otherwise it's a no-op passthrough).

- [ ] 2. Add feedback_selector to loop nodes
  - [ ] 2-1. Add `feedback_selector: FeedbackSelector | None` field to `GateNode`. Default `None` (all outputs feed back — preserves current behavior).
  - [ ] 2-2. Add `feedback_selector: FeedbackSelector | None` field to `WhileLoopNode`. Default `None`.
  - [ ] 2-3. Add `artifact_ports` field (list[str] | None) to `GateNode` and `WhileLoopNode` — convenience shorthand: ports listed here are automatically excluded from feedback and routed to a `"artifacts"` output port that accumulates across iterations. This is the inverse of `feedback_selector.include`: declare what *isn't* feedback rather than what *is*.
  - [ ] 2-4. Interaction: `artifact_ports` and `feedback_selector` can coexist. `artifact_ports` is applied first (removes those ports from the feedback pool), then `feedback_selector` filters the remaining ports.

- [ ] 3. Implement feedback filtering in scheduler (gate loops)
  - [ ] 3-1. In `_inject_feedback` (scheduler.py), after collecting outputs from the cycle's last node, apply `FeedbackSelector` if the gate node has one:
    - If `include` is set: keep only keys matching `include`.
    - If `exclude` is set: drop keys matching `exclude`.
    - Apply `rename` mapping to surviving keys.
    - If `transform` is set, evaluate it (via `evaluate_expression`) with `{"inputs": filtered_dict}` and use the result.
  - [ ] 3-2. For `artifact_ports`: extract those ports from the body output before feedback injection; accumulate them in a list stored in `LocalStateManager` under `{gate_id}__artifacts`. After the loop exits (gate routes to "done"), merge accumulated artifacts into the gate's final output under an `"artifacts"` key.
  - [ ] 3-3. Preserve backward compatibility: when `feedback_selector` is `None` and `artifact_ports` is `None`, behavior is identical to current (all outputs feed back).

- [ ] 4. Implement feedback filtering in WhileLoopExecutor
  - [ ] 4-1. In `WhileLoopExecutor.execute()`, after `body_output = await context.run_subgraph(...)` and before `working_data = {**working_data, **body_output}`, apply `FeedbackSelector`:
    - Filter `body_output` using `include`/`exclude`.
    - Apply `rename` to filtered keys.
    - Apply `transform` if set.
    - Merge only the filtered result into `working_data`.
  - [ ] 4-2. For `artifact_ports`: extract and accumulate in scope under `"__artifacts"` key. Include in final `NodeResult.outputs`.
  - [ ] 4-3. If `state_schema` is also present, feedback selector applies *after* state_schema extraction. Both mechanisms cooperate: `state_schema` manages typed loop variables; `feedback_selector` manages what additional data cycles back.

- [ ] 5. Activate ContextProjection
  - [ ] 5-1. `ContextProjection` in `models/context.py` has been dead code since Phase 0. Its `include`/`exclude`/`rename`/`transform` fields align exactly with `FeedbackSelector`. Decision: **reuse `ContextProjection` as the `FeedbackSelector` model** instead of creating a new model. Rename the field on loop nodes to `feedback_projection: ContextProjection | None` for semantic clarity.
  - [ ] 5-2. Alternatively, if `ContextProjection` has additional fields/semantics that don't fit feedback selection, keep them separate and document the relationship. The key requirement is that the feedback selector has `include`, `exclude`, `rename`, and `transform` — whatever model provides those works.
  - [ ] 5-3. If reusing `ContextProjection`, update the plan's field name references accordingly (`feedback_selector` → `feedback_projection`). Update 14-* plan notes that say "ContextProjection is dead code" to reflect activation.

- [ ] 6. Extend builder API
  - [ ] 6-1. Add `feedback` parameter to `wf.while_loop()` context manager: `feedback=["draft", "score"]` (shorthand for `ContextProjection(include=["draft", "score"])`) or `feedback=ContextProjection(include=[...], rename={...})` (full model).
  - [ ] 6-2. Add `artifacts` parameter to `wf.while_loop()`: `artifacts=["plot", "raw_data"]` (shorthand for `artifact_ports=["plot", "raw_data"]`).
  - [ ] 6-3. Example usage:
    ```python
    with wf.while_loop("refine", condition="score < 0.9", max_iterations=5,
                        feedback=["draft", "score"],
                        artifacts=["reasoning_trace"]) as body:
        reviewer = body.llm("review", prompt=f"Review: {drafter}")
        drafter = body.llm("revise", prompt=f"Improve based on: {reviewer}")
    ```
  - [ ] 6-4. Extend builder compiler to set `feedback_projection` and `artifact_ports` on the generated gate/while node.
  - [ ] 6-5. Extend builder decompiler to emit `feedback=` and `artifacts=` parameters.

- [ ] 7. Extend markdown loader
  - [ ] 7-1. Support `feedback:` and `artifacts:` fields in while-loop flow statements or frontmatter. Example in workflow `## Flow`:
    ```
    loop refine_loop while score < 0.9 (max: 5, feedback: [draft, score], artifacts: [trace]):
      reviewer -> drafter
    ```
  - [ ] 7-2. Extend `flow_parser.py` to parse `feedback:` and `artifacts:` inside loop parenthetical options.
  - [ ] 7-3. Extend `loader/compiler.py` to set `feedback_projection` and `artifact_ports` on compiled loop nodes.
  - [ ] 7-4. Extend `loader/decompiler.py` to emit feedback/artifacts in flow notation.

- [ ] 8. Extend visual editor
  - [ ] 8-1. Add "Feedback" section to `ConfigPanel.tsx` for gate(while) and while_loop nodes: multi-select chips for feedback port names (populated from body exit-point output ports), multi-select for artifact ports.
  - [ ] 8-2. In drill-in view, visually distinguish feedback edges (solid, cycling back) from artifact edges (dashed, exiting the loop). Use different edge colors or styles.
  - [ ] 8-3. Show artifact accumulation indicator on loop node badge during execution: "3 artifacts collected".
  - [ ] 8-4. Tooltip/help text explaining feedback vs. artifact distinction.

- [ ] 9. Validation
  - [ ] 9-1. Extend `validate_graph()`: feedback selector `include`/`exclude` port names must reference actual output port names of the loop body's exit nodes. Warn on unknown port names.
  - [ ] 9-2. Warn if `feedback_projection` would filter out all ports (empty feedback = loop body has no effect on next iteration).
  - [ ] 9-3. Warn if `artifact_ports` and `feedback_projection.include` overlap (a port can't be both feedback and artifact).
  - [ ] 9-4. Validate `transform` expression is syntactically valid (parse-check via `evaluate_expression` dry run).

- [ ] 10. Tests and documentation
  - [ ] 10-1. Unit tests: `ContextProjection` model (or `FeedbackSelector`) serialization, `include`/`exclude` mutual exclusivity validation, `rename` mapping, `transform` expression evaluation.
  - [ ] 10-2. Integration tests (gate loop): while-gate with `feedback_projection(include=["score"])` — verify only `score` feeds back while `draft` is available as final output; gate with `artifact_ports=["trace"]` — verify traces accumulate across iterations.
  - [ ] 10-3. Integration tests (WhileLoopNode): while-loop with `feedback_projection(exclude=["debug_log"])` — verify `debug_log` doesn't pollute next iteration's working data; with `rename={"improved": "draft"}` — verify rename before feedback.
  - [ ] 10-4. Backward compat tests: loops without `feedback_projection` or `artifact_ports` behave identically to current.
  - [ ] 10-5. Builder round-trip tests: `wf.while_loop(feedback=[...], artifacts=[...])` → build → decompile → compare.
  - [ ] 10-6. Markdown round-trip tests: loop with feedback/artifacts → compile → decompile → compare.
  - [ ] 10-7. Update `docs/architecture.md` §Context Projection: replace "dead code" notes with implementation reference; document feedback selector mechanics.
  - [ ] 10-8. Update `docs/llm-api-guide.md`: feedback_projection field reference, builder API, markdown syntax.
  - [ ] 10-9. Update `docs/changelog.md`, `docs/todo.md`, and this plan as implementation progresses.

## Primary Files

- `src/dan/models/context.py` — `ContextProjection` (activated as feedback selector), or new `FeedbackSelector` model
- `src/dan/models/control_flow.py` — `feedback_projection` and `artifact_ports` fields on `GateNode`, `WhileLoopNode`
- `src/dan/engine/scheduler.py` — `_inject_feedback()` filtering logic for gate-based loops
- `src/dan/executors/control_flow.py` — `WhileLoopExecutor.execute()` filtering logic, artifact accumulation
- `src/dan/engine/context_runtime.py` — `LocalStateManager` artifact storage during loop execution
- `src/dan/engine/conditions.py` — `evaluate_expression()` for `transform` expressions
- `src/dan/builder/builder.py` — `feedback` and `artifacts` parameters on `wf.while_loop()`
- `src/dan/builder/compiler.py` — compile feedback/artifact declarations
- `src/dan/builder/decompiler.py` — decompile feedback/artifact fields
- `src/dan/loader/flow_parser.py` — parse `feedback:`/`artifacts:` in loop flow statements
- `src/dan/loader/compiler.py` — compile parsed feedback/artifacts
- `src/dan/loader/decompiler.py` — decompile to flow notation
- `editor/src/components/ConfigPanel.tsx` — feedback/artifact port selection UI
- `editor/src/components/GraphCanvas.tsx` — feedback vs. artifact edge styling in drill-in
- `src/dan/validation/graph.py` — feedback port name validation
- `tests/test_engine/` — feedback filtering integration tests
- `tests/test_builder/` — feedback builder round-trip
- `tests/test_loader/` — feedback markdown round-trip

## Decisions

- **Reuse `ContextProjection` if viable.** The model already has `include`, `exclude`, `rename`, `transform` — exactly the fields needed. Activating dead code is preferable to duplicating the model. Final decision after inspecting whether `ContextProjection.projections` list semantics on `CompositeNode` conflict with single-projection feedback semantics. If conflict: create `FeedbackSelector` as a focused model.
- **`artifact_ports` is sugar, not a separate mechanism.** It's equivalent to `feedback_projection(exclude=artifact_ports)` + accumulation. The sugar makes common patterns readable: "these ports are side-effects, collect them."
- **Feedback filtering is applied per-iteration, not post-loop.** Each iteration's output is filtered before becoming the next iteration's input. This prevents intermediate artifacts from polluting the feedback channel. Post-loop, all outputs (including accumulated artifacts) are available.
- **`state_schema` and `feedback_projection` cooperate.** `state_schema` manages typed variables (loop counters, convergence scores). `feedback_projection` manages which *additional* data cycles back beyond the state variables. They're orthogonal concerns applied sequentially: state_schema extraction first, then feedback filtering on the remaining data.
- **`transform` is an escape hatch.** Most users will use `include`/`exclude`/`rename`. `transform` handles edge cases (e.g., extracting a nested field, truncating a string, computing a derived value). It uses the existing safe expression evaluator.
- **Backward compatible by default.** All new fields default to `None`. Existing loops behave identically. No migration needed.

## Notes

- This plan addresses the "async loop design" backlog item's feedback concern: "orchestrator runs independently with access to current progress and can emit commands anytime" — feedback selectors are the mechanism for controlling what the orchestrator sees between iterations.
- The vibe-research workflow manually extracts feedback signals via code nodes between loop iterations. With `feedback_projection`, those code nodes become unnecessary — the loop node itself declares what feeds back.
- `ContextProjection` was designed in Phase 0 and has been dead code since. Its `include`/`exclude`/`rename`/`transform` fields were forward-looking for exactly this use case. This plan is where it finally gets activated.
- If 15-1 hyperedges land first, a guardrail hyperedge could enforce feedback constraints (e.g., "loops must have explicit feedback selectors") — but this plan doesn't depend on 15-1.
