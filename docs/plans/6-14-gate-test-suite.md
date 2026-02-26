# 6-14: Workflow Seam Hardening + Incremental Gate Regression

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** not-started
**Goal:** Make workflows robust at integration seams first (fail loud, avoid silent data loss), then expand gate and composition tests from single-node to multi-node.

## Why this plan changed

Recent failures were mostly seam bugs (compiler semantics, executor output conventions, runtime data-flow drops), not pure gate primitive bugs. A gate-only test plan is necessary but not sufficient.

## Batches (do one at a time)

### Batch 1 — CodeExecutor `.result` contract fix

**What:** When `result` is a dict, `CodeExecutor` currently exposes only flattened keys (e.g. `{"value": 42}`). `ToolExecutor` additionally sets `outputs["result"] = <full dict>`. Fix both inline and subprocess paths to match.

**Why first:** Every downstream `node.result` edge silently gets nothing for code nodes returning dicts. Highest-impact one-liner fix.

**Files touched:** `src/dan/executors/code.py`, `tests/test_engine/test_sandbox.py`

- [ ] 1-1. **Fix inline mode** — after `outputs = result_value`, add `outputs["result"] = result_value` (mirrors `tool.py` L142-144). ~2 lines in `code.py` L130-131.
- [ ] 1-2. **Fix subprocess mode** — same pattern for dict branch at `code.py` L192-193.
- [ ] 1-3. **Update 3 existing tests** that assert dict-only outputs: `test_sandbox.py` L339 (`{"value": 42}`), L452 (`{"key": "value", "num": 42}`), L595 (`{"v": 1}`). Each now also has `"result"` key.
- [ ] 1-4. **Add 2 new regression tests** in `test_sandbox.py`: (a) inline dict return wired via `.result`, (b) subprocess dict return wired via `.result`.

---

### Batch 2 — Compile-time edge port diagnostics

**What:** Improve error messages when explicit port references are invalid. The loader already rejects bad explicit ports (`_resolve_chain_ports` L675-688); the validator also catches mismatches (`_check_edge_endpoints` L155-162). But neither shows available ports in the error. Add that, plus tests.

**Why:** Currently the error says `"source node 'X' has no output port 'Y'"` — user has to guess valid ports. Show them.

**Files touched:** `src/dan/validation/graph.py`, `src/dan/loader/compiler.py`, `tests/test_loader/test_compiler.py`, `tests/test_engine/test_validator.py`

- [ ] 2-1. **Improve `_check_edge_endpoints` diagnostic** — append available ports to error message. ~4 lines in `validation/graph.py`.
- [ ] 2-2. **Improve `_resolve_chain_ports` diagnostic** — same "available:" suffix in `compiler.py` L677-685.
- [ ] 2-3. **Add 3 tests**: (a) loader rejects `source.bad_port -> target` with error listing available ports, (b) loader rejects `source -> target.bad_port` similarly, (c) validator rejects graph with nonexistent port and lists alternatives.

---

### Batch 3 — Runtime dead-edge warnings

**What:** When `resolve_inputs` processes a data edge but the source port has no value, emit a warning event. Suppress for edges from gate inactive branches (the scheduler already knows which branch is active).

**Why:** Silent data loss at runtime is the hardest bug to diagnose. A warning event makes it visible in run logs.

**Files touched:** `src/dan/engine/state.py` or `src/dan/engine/scheduler.py`, `src/dan/engine/events.py`, tests

- [ ] 3-1. **Add `DEAD_EDGE_WARNING` event type** to `events.py`. In scheduler `_execute_node` (or `resolve_inputs`), emit warning when a data edge source port has no value.
- [ ] 3-2. **Suppress for gate inactive branches** — if the source node is a gate and the edge's source port doesn't match the active branch, skip the warning. Use `_should_skip` logic or `port_data` branch metadata.
- [ ] 3-3. **Add 3 scheduler tests**: (a) warning emitted for missing source port, (b) no warning for gate inactive branch, (c) no warning for intentionally optional edges.

---

### Batch 4 — GateExecutor input handling tests

**What:** The GateExecutor has ~15 lines of input flattening/unwrapping logic (L90-104) with **zero test coverage**: dict unwrapping via `val.get("result", val)`, single-value `"input"` port mapping to condition var name, `try_more` safe default. Also the `done` output unwrapping (L130) and `gate_evaluated` event payload (L140-145) are untested.

**Why:** This is the biggest testing gap in gate execution. Every real workflow hits this flattening path.

**Files touched:** `tests/test_engine/test_gate_executor.py` (add new test class)

- [ ] 4-1. **Dict input flattening tests** — (a) `{"input": {"result": {"verdict": "revise"}}}` flattens `verdict` into condition vars, (b) plain dict `{"verdict": "revise"}` also works, (c) nested dict without `"result"` key uses outer dict.
- [ ] 4-2. **Single-value `"input"` port mapping** — condition `use_builtin` with `{"input": True}` → `condition_vars["use_builtin"] = True`.
- [ ] 4-3. **`try_more` default injection** — condition contains `try_more` but inputs don't have it → defaults to `True`.
- [ ] 4-4. **`done` output unwrapping** — while-gate done branch: `{"input": <body_value>}` → output is `<body_value>` not the full dict.
- [ ] 4-5. **`gate_evaluated` event payload** — assert event contains `gate_mode`, `active_branch`, `iteration`, `condition_vars`, `condition`.

---

### Batch 5 — Compiler semantics regression

**What:** The loader compiler converts `until: "X"` to `not (X)` (L466) and generates loop edges. Current tests only check `"verdict" in condition` (L78) — no exact semantics.

**Files touched:** `tests/test_loader/test_compiler.py`

- [ ] 5-1. **`until:` → `not(X)` exact assertion** — compile fixture, assert `wg.condition == "not (verdict == 'accept')"` (or whatever the exact fixture condition is).
- [ ] 5-2. **Loop edge structure** — assert gate has incoming data edge, outgoing `continue` edge back to body, outgoing `done` edge forward.
- [ ] 5-3. **If-gate edge structure** — assert `true` and `false` edges exist and target correct nodes.

---

### Batch 6 — Integration: real executors in small graphs

**What:** All existing scheduling tests use mock executors. Add tests with real `GateExecutor` + real `CodeExecutor` running through the engine.

**Why:** Catches seam bugs between executor output format and scheduler input resolution that mocks hide.

**Files touched:** `tests/test_engine/test_gate_scheduling.py` (add new test class) or new file

- [ ] 6-1. **If/else with real executors** — `CodeExecutor` → `GateExecutor` → two `CodeExecutor` branches. Assert correct branch runs, other skipped, outputs correct.
- [ ] 6-2. **While loop with real executors** — counter increment loop, real `GateExecutor` evaluating real condition, `CodeExecutor` incrementing counter. Assert loop runs N times and exits.
- [ ] 6-3. **End-to-end markdown → engine** — compile a small markdown with `until:` loop, run through engine with real executors, assert correct output. Validates the full stack: compiler semantics + gate input flattening + scheduler cycling.

## Existing coverage (baseline)

| File | Covers | Remaining gap |
|---|---|---|
| `test_gate.py` (14 tests) | GateNode model + GateExecutor basic branch/error | no input flattening, no `gate_evaluated` event |
| `test_gate_executor.py` (8 tests) | branch routing, iteration counter, output ports | same flattening gap |
| `test_gate_scheduling.py` (11 tests) | if/else routing, while loop, events, topo sort | all mock executors |
| `test_cycle_scheduling.py` (11 tests) | DAG fast-path, while loop, max_iter, validation | all mock executors |
| `test_conditions.py` (10 tests) | `evaluate_condition` directly | well-covered; no gap |
| `test_compiler.py` (5 tests) | loop/if/foreach creates correct node type | no semantic assertions (condition text, edge structure) |
| `test_sandbox.py` (14 tests) | CodeExecutor inline+subprocess | encodes dict-without-`result` convention (will break after Batch 1) |

## Rules

- One batch per session. Don't start the next batch until the current one's tests pass.
- Keep tests deterministic (no external API calls).
- Prefer real executors over mocks in integration tests (Batch 6).
- After each batch: update this plan, changelog, run full test suite.

## Decisions

- Prioritize cross-component seam robustness over isolated primitive depth.
- Fail loudly at compile/build time whenever explicit wiring is invalid.
- Batch structure: fix code first (1-3), then test-only expansion (4-6).

## Notes

- (to be filled during implementation)
