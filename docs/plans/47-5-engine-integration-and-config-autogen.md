# 47-5: Engine Integration & Config Auto-Generation

**Parent:** [47-agent-output-linter](47-agent-output-linter.md)
**Status:** completed
**Goal:** Connect the standalone linter to the DAN engine scheduler at the `port_data.set()` handoff point, and enable the topology/compiler stack to auto-generate lint configs from the Worker-first contract surface established by plan 46, including shared-context references resolved from Worker metadata rather than only inline fields.

## Current State

- The scheduler handoff gate is live: lint runs before publication, can auto-fix or retry supported producers, and now cleanly skips fully disabled edge configs instead of emitting fake lint telemetry.
- `DataEdge.lint` is the typed runtime/model surface, with legacy `metadata["lint"]` preserved for compatibility and round-trip safety.
- Worker-first autogen is live in both runtime fallback and optional build/compile-time decoration, including schema-derived structural rules, description/ref-based semantic config, and role/instruction-derived intent config.
- Generated configs without a structural contract now start in conservative warning/canary mode with no deterministic structural autofix, instead of promoting purely heuristic semantic/intent guesses straight to blocking errors.
- Retry-with-feedback is now intentionally narrower too: the scheduler only re-invokes LLM-capable producers (`llm_operator` or Workers with `model` / meaningful `llm_hints`) instead of treating every Worker as a sensible retry target.
- Complex workflows can now optionally refine that deterministic intent statement during graph construction through an injected `lint_intent_refiner(base_intent, context)` callback; failures fall back conservatively to the deterministic intent text instead of blocking graph builds.
- Editor and analytics surfaces are already wired: lint is editable on edges, visible in run logs/chat/notifications, and rolled into telemetry summaries.

## Part A: Engine Integration

### Tasks

- [x] 1. Add lint hook in `src/dan/engine/scheduler.py` at the handoff point
  - [x] 1-1. After `result.outputs` is produced (around line 2492), before `state.port_data.set()`
  - [x] 1-2. Look up outgoing edges from the producing node
  - [x] 1-3. For each outgoing `DataEdge`, resolve a lint config from `edge.lint` first, with legacy `edge.metadata["lint"]` fallback
  - [x] 1-4. If lint config exists, call `lint(data=value, config=parsed_config, embedder=..., llm_judge=...)`
  - [x] 1-5. On `passed=True` or `auto_fixed=True`: proceed with `port_data.set()` (use `fixed_data` if auto-fixed)
  - [x] 1-6. On `passed=False`: handle based on severity
    - `error`: halt the node, mark as failed, record lint diagnostics in `node_metadata`
    - `warning`: log diagnostics, proceed with original data
    - `info`: record diagnostics in telemetry only
  - [x] 1-7. Treat lint as part of handoff publication: downstream nodes remain blocked until lint/fix completes and the edge value is actually published
- [x] 2. Lint retry integration
  - [x] 2-1. When lint fails with `RetryWithFeedbackFix` available, re-invoke the producing node's executor with the feedback prompt appended
  - [x] 2-2. Bounded by `max_retries` from the lint config
  - [x] 2-3. Each retry re-runs the full lint pass on the new output
  - [x] 2-4. If retries exhausted, fall through to error handling
- [x] 3. Inject embedder and LLM judge from engine context
  - [x] 3-1. The engine's `ExecutionContext` already has access to the model gateway and runtime config
  - [x] 3-2. Create thin adapter functions that wrap the gateway for embedding calls and cheap LLM judge calls
  - [x] 3-3. These adapters are the ONLY place where `dan.linter` and `dan.engine` touch — the linter receives them as callables
- [x] 4. Lint telemetry
  - [x] 4-1. Emit `lint_passed`, `lint_failed`, `lint_auto_fixed` events to the engine's event stream
  - [x] 4-2. Include tier reached, elapsed time, rule names, diagnostics summary
  - [x] 4-2-1. Surface lint events as a first-class category in the editor operations log with structured inline summaries, run-summary counts, notification-center alerts for live blocking failures, chat-run summaries in `RunOutputBlock`, telemetry-dashboard rollups in `TokenAnalyticsPanel`, and expandable diagnostics/fix details
  - [x] 4-3. These events feed into the existing telemetry/analytics pipeline
- [x] 5. Per-edge lint config storage
  - [x] 5-1. Lint config lives on `DataEdge.lint` as a typed `LintConfig | None`
  - [x] 5-2. Legacy `DataEdge.metadata["lint"]` still loads and stays synchronized for compatibility/serialization
  - [x] 5-3. Promote `lint` to a first-class field on `DataEdge` for type safety
- [x] 6. Tests
  - [x] 6-1. `tests/test_engine/test_worker_lint_integration.py` — lint fires at handoff, blocks on error, passes on success
  - [x] 6-2. Auto-fix integration: linter fixes data, scheduler uses fixed data
  - [x] 6-3. Retry integration: scheduler re-invokes node on lint failure with feedback
  - [x] 6-4. Telemetry: lint events appear in the event stream
  - [x] 6-5. No lint config = no overhead: verify zero-cost path when edges have no lint config

## Part B: Config Auto-Generation

### Tasks

- [x] 7. Structural config generation from schemas
  - [x] 7-1. Given a node's `output_ports` schemas and the downstream node's `input_ports` schemas, generate `StructuralConfig`
  - [x] 7-2. Extract `required_fields` from the input port schema's `required` list
  - [x] 7-3. Extract `ranges` from numeric fields with `minimum`/`maximum` constraints
  - [x] 7-4. Extract `format_patterns` from string fields with `pattern` constraints
  - [x] 7-5. This is pure schema analysis — deterministic, no LLM needed
- [x] 8. Semantic config generation from node descriptions
  - [x] 8-1. Given the receiving node's `description`, and for Workers especially `role`, short instruction/profile, referenced instruction bundles, and input port descriptions, generate `SemanticConfig`
  - [x] 8-2. Prefer a Worker-first path; legacy nodes fall back to their existing descriptions and executor-specific fields only when Worker metadata is unavailable
  - [x] 8-3. Extract `topic_keywords` by simple NLP: tokenize the canonical contract description, remove stop words, take top-N terms
  - [x] 8-4. Set `embedding_reference` to the receiving node's contract/intent text
  - [x] 8-5. Default `min_topic_similarity` based on task risk level (0.7 for normal, 0.85 for high-risk)
- [x] 9. Intent config generation from worker roles
  - [x] 9-1. Given the receiving worker's `role`, resolved short instruction/profile, input-port descriptions, and the edge's purpose in the workflow, generate an `IntentConfig`
  - [x] 9-2. The `intent` string is a synthesized statement: "Output that serves as {port_description} for a {role} worker whose goal is {instruction_summary}"
  - [x] 9-3. This can be generated deterministically from the node metadata — no LLM call needed for basic intents
  - [x] 9-4. For complex workflows, optionally use a single LLM call at graph construction time to refine the intent statement
  - [x] 9-5. If the useful instruction text lives behind a reference (`instruction_profile_ref`, `context_bundle_refs`), autogen should resolve it through the same canonical registries instead of ignoring it
- [x] 10. Auto-generation entry point
  - [x] 10-1. `generate_lint_config(source_node, target_node, edge) → LintConfig` utility function
  - [x] 10-2. Called by the topology compiler when creating edges between workers
  - [x] 10-3. Called by the builder DSL when connecting nodes (optional — off by default, enabled by config)
  - [x] 10-4. The generated config is stored in `edge.lint` and mirrored into `edge.metadata["lint"]`; explicit manual lint still overrides autogen
  - [x] 10-5. If source/target metadata is too weak to generate a trustworthy config, omit autogen rather than guess; manual lint config remains allowed
- [x] 11. Tests
  - [x] 11-1. `tests/test_linter/test_config_generation.py` — schema-to-structural, description-to-semantic, role-to-intent
  - [x] 11-2. Round-trip: generated config serializes to JSON and parses back to equivalent `LintConfig`
  - [x] 11-3. Override: manually specified lint config takes precedence over auto-generated

## Integration Point Detail

The handoff happens at one specific line in `scheduler.py`:

```python
# Current code (line ~2492):
for port_name, value in result.outputs.items():
    state.port_data.set(node_id, port_name, value)

# After integration:
for port_name, value in result.outputs.items():
    value = self._lint_output(node_id, port_name, value, graph, state, context)
    state.port_data.set(node_id, port_name, value)
```

`_lint_output` is a private method on the scheduler that:
1. Finds outgoing edges from `(node_id, port_name)`
2. Checks each edge for lint config
3. Calls `lint()` with injected backends
4. Returns `fixed_data` if auto-fixed, or `value` if no lint or lint passed
5. Raises/records if lint failed with error severity

## Dependency Boundary

```
dan.engine.scheduler
  ↓ imports
dan.linter (lint, LintConfig, LintResult)
  ↓ receives (injected)
embedder callable  ← built from dan.engine context
llm_judge callable ← built from dan.engine context
```

The linter never imports from `dan.engine`. The engine imports from `dan.linter` and provides the backends.

## Decisions

- Worker metadata from plan 46 is the canonical autogen source. Legacy nodes are supported via best-effort fallback only.
- Autogen should be conservative. Missing or weak contract metadata means "leave lint unset," not "invent a brittle config."
- The linter gate participates in blocking semantics. A handoff is not published until linting finishes; this is part of the runtime dependency model, not just analytics.

## Notes

- Autogen remains conservative. A generated config should never be stricter than the explicit contract metadata can justify; manual override still wins.
- In practice that now means semantic/intent-only autogen defaults to warning severity unless a structural contract is also present or a caller overrides the config explicitly.
- The key bridge back to 46 is resolved Worker metadata. Autogen uses referenced instruction/context/provider bundles when available instead of only looking at inline strings.
- Runtime backend absence degrades conservatively: semantic or intent tiers skip when their injected backends are unavailable instead of turning backend absence into a false handoff failure.
- The rollout harness at `tests/eval/worker_lint_benchmark.py` now covers both deterministic and optional live-provider cases, so this plan is benchmarked as well as unit-tested.
- Build-time intent refinement remains opt-in and callback-owned. The compiler/builder now pass an optional `lint_intent_refiner(base_intent, context)` into `generate_lint_config(...)`, which lets callers do one graph-construction-time LLM refinement for complex edges without introducing a hard engine dependency or making lint autogen brittle when that refinement path is absent.
- Detailed per-slice integration history now lives in `docs/changelog.md`; this plan now tracks the remaining rollout and polish tail rather than every landed integration bullet.
