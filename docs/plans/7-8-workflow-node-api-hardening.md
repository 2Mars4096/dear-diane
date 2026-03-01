# 7-8: Workflow Node API Hardening

**Parent:** [7-core-hardening](7-core-hardening.md)
**Status:** in-progress
**Goal:** Fix correctness gaps in the workflow authoring pipeline (loader, validation, builder, mutator) so that **users can describe workflows in their mind** and we can use existing nodes, logical nodes, and tools to **easily, logically, and structurally** build them. Core mechanisms must be comprehensive and strong — round-trip lossless, validation aligned with runtime, sensible defaults, and fail-fast feedback when ambiguous.

## Design Principle: Convenient Workflow Building

The ideal flow: user describes intent (NL, markdown, or Python) → system compiles to graph → validates → runs. Friction points that hinder this:

- **Silent behavior change** — round-trip through markdown inverts loop semantics or drops state
- **False validation errors** — ContextEdge read checks wrong node, blocking valid workflows
- **Wrong defaults** — gate `>>` chain uses `true` instead of `continue` for while-mode, causing silent miswiring
- **Partial graphs from parse failures** — invalid flow lines produce warnings, compilation continues, user gets broken graph
- **Typos masked as ports** — mutator auto-creates unknown target ports, hiding wiring mistakes
- **Ambiguous wiring** — bare edges with multiple matching ports only wire first; no clear signal to user

This plan addresses all of the above so the core pipeline is trustworthy and authoring is fluid.

## Tasks

### 1. Markdown loop round-trip lossless (Critical)

**Bug:** [docs/bugs.md](../bugs.md) — decompiler writes gate condition into `until` without inversion; omits `state`/`defaults`.

- [x] 1-1. **Decompiler: invert `gate.condition` → `until`**
  - Compiler stores `not (cond)` in gate; decompiler must emit `cond` for `until`.
  - Rule: if `gate.condition` matches `not (...)` pattern, strip one layer and emit inner expression.
  - Edge cases: nested `not (not (x))` → emit `x`; document in code and llm-api-guide.
- [x] 1-2. **Decompiler: emit `state:` and `defaults:` when present**
  - When gate has `state_schema` / `state_defaults`, add to loop() output as JSON strings.
  - Flow parser already supports these kwargs (flow_parser.py lines 179–192).
- [x] 1-3. **Round-trip test**
  - `md → graph → md' → graph'` with semantic equivalence (same nodes, edges, gate condition meaning, state schema).
- [x] 1-4. **Document inversion rule** in decompiler and llm-api-guide (12b Loop semantics).

**Primary files:** `src/dan/loader/decompiler.py`, `tests/` (round-trip).

---

### 2. ContextEdge read validation aligned with runtime (High)

**Bug:** [docs/bugs.md](../bugs.md) — validator checks `source_node_id` read_set; runtime injects on `target_node_id`.

- [x] 2-1. **Fix `_check_context_edge_permissions`**
  - For `ContextMode.READ`: validate `target_node_id`'s `read_set` (consumer of context).
  - For `WRITE`/`APPEND`: keep validating `source_node_id`'s `write_set` (unchanged).
- [x] 2-2. **Update llm-api-guide** — document context-edge semantics: read = target consumes, write = source produces.

**Primary files:** `src/dan/validation/graph.py`, `docs/llm-api-guide.md`.

---

### 3. Gate while-mode default output safe (High)

**Bug:** [docs/bugs.md](../bugs.md) — builder `DEFAULT_OUTPUT_PORTS["gate"] = "true"`; while-mode gates have `continue`/`done`.

- [x] 3-1. **Builder: mode-aware default output for gate**
  - In `dan.builder.compiler`: `default_output_port` must consider `gate_mode` when node_type is `gate`.
  - `gate_mode="while"` → `"continue"` (typical loop body chain); `gate_mode="if_else"` → `"true"`.
  - Requires builder/compiler to have access to gate_mode at chain resolution time (NodeRef or pending node kwargs).
- [x] 3-2. **Loader parity check**
  - Loader already has mode-aware `_default_output_port` (lines 1038–1041). Verify behavior is consistent; loader uses `"done"` for while-gate default (exit chain). Confirm no regressions.
- [x] 3-3. **Update llm-api-guide playbook**
  - Relax 12a rule 3: after fix, `gate >> body` works for while-mode gates; explicit `gate["continue"]` still recommended for clarity but not required.

**Primary files:** `src/dan/builder/compiler.py`, `src/dan/builder/builder.py`, `docs/llm-api-guide.md`.

---

### 4. Flow parse strict mode (High)

**Bug:** [docs/bugs.md](../bugs.md) — parse failures are warnings; compilation continues with partially wired graphs.

- [x] 4-1. **Add `strict` parameter**
  - `compile_workflow(path, strict=False)` or `WorkflowSpec(..., strict_parse=False)`.
  - When `strict=True`: parse failures and parse_warnings → `BuildError` / fatal diagnostics; compilation stops.
- [x] 4-2. **Default and migration**
  - Default `strict=False` for backward compatibility.
  - Document in llm-api-guide: recommend `strict=True` for LLM-generated and new workflows.
- [x] 4-3. **Scope**
  - Strict mode: flow parse errors + parse_warnings (invalid flow lines). Optionally include auto-wire ambiguity warnings as fatal in strict mode (task 6).

**Primary files:** `src/dan/loader/compiler.py`, `src/dan/loader/parser.py`, `docs/llm-api-guide.md`.

---

### 5. Mutator port auto-create — warn, optional strict (Medium)

**Bug:** [docs/bugs.md](../bugs.md) — typo in target_port silently creates untyped optional port.

- [x] 5-1. **Add diagnostic on auto-create**
  - When mutator auto-creates a port, add a non-fatal diagnostic: "Auto-created input port 'X' on node 'Y' (port not declared). Verify spelling."
  - Return diagnostic in mutation response so chat/UI can surface it.
- [x] 5-2. **Optional strict mode for mutations**
  - `add_edge` op: when `strict=True`, fail with clear error instead of auto-creating.
  - Default: permissive (auto-create + warn) for conversational convenience; strict opt-in for programmatic use.

**Primary files:** `src/dan/server/graph_mutator.py`, mutation API schema.

---

### 6. Ambiguous bare-edge — fail in strict mode (Medium, convenience)

**Context:** Loader emits warning when multiple ports match; uses first alphabetically. merge→governor historically dropped `new_strategies` this way.

- [x] 6-1. **Strict mode: ambiguous auto-wire → error**
  - When `strict=True` and `len(matches) > 1`, emit error (not warning): "Ambiguous: A → B has multiple matching ports {X, Y}. Use explicit .port syntax."
  - Prevents silent single-port wiring when user intended both.
- [x] 6-2. **Improve hint message**
  - Non-strict: keep warning but strengthen hint: "Use explicit 'A.port_x → B.port_y' to wire all intended ports."

**Primary files:** `src/dan/loader/compiler.py`, `_resolve_ports`, `_auto_wire`.

---

### 7. Decompiler control/context semantics (Medium, deferred)

**Context:** Control and context edges emit `<!-- SKIPPED -->` comments; round-trip not lossless for those workflows.

- [x] 7-1. **Document as known limitation**
  - In llm-api-guide 12b: "Context-heavy workflows: markdown decompilation prioritizes data-flow readability. Control/context edges are emitted as comments. For full round-trip of those workflows, keep a Python/JSON canonical source."
- [x] 7-2. **Future: extend markdown syntax** (out of scope for 7-8)
  - If needed later: add flow syntax for context read/write, control edges. Requires format extension.

**Primary files:** `docs/llm-api-guide.md`.

---

### 8. Tests and docs

- [x] 8-1. **Round-trip tests** — loop with state_schema/defaults, until inversion, semantic equivalence.
- [x] 8-2. **Unit tests** — ContextEdge validation (read=target, write=source), gate default port by mode.
- [x] 8-3. **Integration** — strict mode compile fails on invalid flow line; mutator diagnostic on auto-create.
- [x] 8-4. **Update changelog, architecture, bugs** — per project tracking rules.

---

## Sequencing

| Order | Task | Rationale |
|-------|------|-----------|
| 1 | Task 1 (loop round-trip) | Critical; unblocks safe markdown editing |
| 2 | Task 2 (ContextEdge) | Small, independent; removes false validation |
| 3 | Task 3 (gate default) | Enables convenient `gate >> body` in builder |
| 4 | Task 4 (strict mode) | Fail-fast; enables tasks 6-1 |
| 5 | Task 6 (ambiguous edge) | Depends on strict mode |
| 6 | Task 5 (mutator) | Independent; improves chat mutation feedback |
| 7 | Task 7 (docs) | Documentation only |
| 8 | Task 8 (tests) | Throughout |

---

## Decisions

(To be filled during execution)

- **Until inversion rule:** Exact pattern for stripping `not (...)` — handle nested, complex expressions.
- **Strict default:** Keep `strict=False` for backward compat; recommend `strict=True` in playbook.
- **Gate default for while:** `continue` (loop body) vs `done` (exit) — builder uses `continue` for `gate >> body`; loader uses `done` for `gate | next` (exit chain). Both valid for different cases.

---

## Notes

- **Loader vs builder:** Loader `_default_output_port` is already mode-aware (gate_mode=while → "done"). Builder is not. Builder fix is the main work.
- **7-6 dependency:** Task 6-2 in 7-6 added state_schema/defaults to markdown flow syntax. Decompiler must emit them (task 1-2).
- **8-4 relation:** Round-trip tests extend 8-4 conformance tests; same methodology.
- **Convenience vs correctness:** This plan favors strong core mechanisms (fail-fast when ambiguous, correct validation) so that the system is trustworthy. Convenience comes from sensible defaults (gate mode-aware port, clear errors) rather than silent fallbacks.
