# 47-5: Engine Integration & Config Auto-Generation

**Parent:** [47-agent-output-linter](47-agent-output-linter.md)
**Status:** not-started
**Goal:** Connect the standalone linter to the DAN engine scheduler at the `port_data.set()` handoff point, and enable the topology compiler to auto-generate lint configs from worker contracts.

## Part A: Engine Integration

### Tasks

- [ ] 1. Add lint hook in `src/dan/engine/scheduler.py` at the handoff point
  - [ ] 1-1. After `result.outputs` is produced (around line 2492), before `state.port_data.set()`
  - [ ] 1-2. Look up outgoing edges from the producing node
  - [ ] 1-3. For each outgoing `DataEdge`, check if `edge.metadata.get("lint")` contains a lint config
  - [ ] 1-4. If lint config exists, call `lint(data=value, config=parsed_config, embedder=..., llm_judge=...)`
  - [ ] 1-5. On `passed=True` or `auto_fixed=True`: proceed with `port_data.set()` (use `fixed_data` if auto-fixed)
  - [ ] 1-6. On `passed=False`: handle based on severity
    - `error`: halt the node, mark as failed, record lint diagnostics in `node_metadata`
    - `warning`: log diagnostics, proceed with original data
    - `info`: record diagnostics in telemetry only
- [ ] 2. Lint retry integration
  - [ ] 2-1. When lint fails with `RetryWithFeedbackFix` available, re-invoke the producing node's executor with the feedback prompt appended
  - [ ] 2-2. Bounded by `max_retries` from the lint config
  - [ ] 2-3. Each retry re-runs the full lint pass on the new output
  - [ ] 2-4. If retries exhausted, fall through to error handling
- [ ] 3. Inject embedder and LLM judge from engine context
  - [ ] 3-1. The engine's `ExecutionContext` already has access to the model gateway and runtime config
  - [ ] 3-2. Create thin adapter functions that wrap the gateway for embedding calls and cheap LLM judge calls
  - [ ] 3-3. These adapters are the ONLY place where `dan.linter` and `dan.engine` touch — the linter receives them as callables
- [ ] 4. Lint telemetry
  - [ ] 4-1. Emit `lint_passed`, `lint_failed`, `lint_auto_fixed` events to the engine's event stream
  - [ ] 4-2. Include tier reached, elapsed time, rule names, diagnostics summary
  - [ ] 4-3. These events feed into the existing telemetry/analytics pipeline
- [ ] 5. Per-edge lint config storage
  - [ ] 5-1. Lint config lives in `DataEdge.metadata["lint"]` as a JSON-serializable dict
  - [ ] 5-2. The scheduler parses it into `LintConfig` at edge resolution time
  - [ ] 5-3. Optional future: promote `lint` to a first-class field on `DataEdge` for type safety
- [ ] 6. Tests
  - [ ] 6-1. `tests/test_engine/test_lint_integration.py` — lint fires at handoff, blocks on error, passes on success
  - [ ] 6-2. Auto-fix integration: linter fixes data, scheduler uses fixed data
  - [ ] 6-3. Retry integration: scheduler re-invokes node on lint failure with feedback
  - [ ] 6-4. Telemetry: lint events appear in the event stream
  - [ ] 6-5. No lint config = no overhead: verify zero-cost path when edges have no lint config

## Part B: Config Auto-Generation

### Tasks

- [ ] 7. Structural config generation from schemas
  - [ ] 7-1. Given a node's `output_ports` schemas and the downstream node's `input_ports` schemas, generate `StructuralConfig`
  - [ ] 7-2. Extract `required_fields` from the input port schema's `required` list
  - [ ] 7-3. Extract `ranges` from numeric fields with `minimum`/`maximum` constraints
  - [ ] 7-4. Extract `format_patterns` from string fields with `pattern` constraints
  - [ ] 7-5. This is pure schema analysis — deterministic, no LLM needed
- [ ] 8. Semantic config generation from node descriptions
  - [ ] 8-1. Given the receiving node's `description`, `persona` (for Workers), and input port descriptions, generate `SemanticConfig`
  - [ ] 8-2. Extract `topic_keywords` by simple NLP: tokenize description, remove stop words, take top-N terms
  - [ ] 8-3. Set `embedding_reference` to the receiving node's description text
  - [ ] 8-4. Default `min_topic_similarity` based on task risk level (0.7 for normal, 0.85 for high-risk)
- [ ] 9. Intent config generation from worker roles
  - [ ] 9-1. Given the receiving worker's `role`, `persona`, and the edge's purpose in the workflow, generate an `IntentConfig`
  - [ ] 9-2. The `intent` string is a synthesized statement: "Output that serves as {port_description} for a {role} agent whose goal is {persona_summary}"
  - [ ] 9-3. This can be generated deterministically from the node metadata — no LLM call needed for basic intents
  - [ ] 9-4. For complex workflows, optionally use a single LLM call at graph construction time to refine the intent statement
- [ ] 10. Auto-generation entry point
  - [ ] 10-1. `generate_lint_config(source_node, target_node, edge) → LintConfig` utility function
  - [ ] 10-2. Called by the topology compiler when creating edges between workers
  - [ ] 10-3. Called by the builder DSL when connecting nodes (optional — off by default, enabled by config)
  - [ ] 10-4. The generated config is stored in `edge.metadata["lint"]` and can be manually overridden
- [ ] 11. Tests
  - [ ] 11-1. `tests/test_linter/test_config_generation.py` — schema-to-structural, description-to-semantic, role-to-intent
  - [ ] 11-2. Round-trip: generated config serializes to JSON and parses back to equivalent `LintConfig`
  - [ ] 11-3. Override: manually specified lint config takes precedence over auto-generated

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

- (to be filled during execution)

## Notes

- Lint config starts in `edge.metadata["lint"]` (a dict) to avoid model changes on `DataEdge`. If lint proves valuable, a future PR promotes `lint: LintConfig | None = None` to a first-class field on `DataEdge`.
- The auto-generation is best-effort and conservative. A generated config should never be stricter than what the node contracts specify. Manual override is always available.
- The embedder adapter wraps the same model gateway that LLM executors use, so embedding calls go through the existing rate limiting, telemetry, and retry infrastructure.
- When lint fails with error severity, the scheduler treats it like a node failure: the node is marked failed, diagnostics are stored in `node_metadata`, and the normal retry/error handling kicks in. This means lint failures are visible in the UI, the event stream, and the run history.
