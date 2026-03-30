# 44-6: Rollout Hardening

**Parent:** [44-structured-workflow-generation](44-structured-workflow-generation.md)
**Status:** completed
**Goal:** Fix the chain of cwd/startup, mutation-preview, capability-surface, and validator-port bugs that surfaced during the 44-series rollout.

## Tasks

### A. Backend startup path hardening
- [x] 1. Centralize graph-dir resolution with missing-cwd fallback anchored to repo root or `~/.dan`
- [x] 2. Centralize workspace-root resolution via `resolve_workspace_root()`
- [x] 3. Seed `DAN_GRAPHS_DIR` in `server/__main__.py` before uvicorn imports `dan.server.app`
- [x] 4. Regressions in `tests/test_server/test_paths.py` (7 passed)

### B. Mutation duplicate-edge repair
- [x] 5. Drop redundant exact `add_edge` ops when same endpoints+semantics already exist in graph or earlier in plan
- [x] 6. Preserve remove-then-readd flows and differing edge semantics
- [x] 7. Regressions in `tests/test_mutation_duplicate_add_repair.py` (10 passed)

### C. Capability surface runtime alignment
- [x] 8. Serialize nodes from typed `Graph` model via `model_dump(...)` for `inspect_node` / `get_activity`
- [x] 9. Replace stale `format_check` validator defaults with schema-valid rules; normalize legacy payloads
- [x] 10. Route file-tool workspace fallback through `resolve_workspace_root()` instead of `os.getcwd()`
- [x] 11. Regressions in `tests/test_capabilities_introspection.py` (14 passed)

### D. Validator input-port contract
- [x] 12. Seed `ValidatorNode` with canonical `data` input port; `DefaultsEnricher` and `insert_validator()` emit explicit ports
- [x] 13. Update live builder-code integration expectation to match enriched 3-node graph shape
- [x] 14. Regressions in `tests/test_meta/test_generation_defaults.py`, `test_structural_mutations.py`, `test_validator.py` (119 passed)

### E. Cwd-safe runtime execution
- [x] 15. Anchor relative engine storage paths (checkpoint, memory, rules, state-store, cache) to resolved workspace root
- [x] 16. Wrap inline code-node `open(...)` so relative paths resolve against `DAN_WORKSPACE_ROOT`
- [x] 17. Regressions including a real `RunManager` run under a deleted cwd (7 passed)

### F. Mutation port-alias repair
- [x] 18. Detect `add_edge` ops referencing missing source ports for known tool nodes; rewrite unambiguous aliases (e.g. `http_request.response -> body`)
- [x] 19. Regressions in `tests/test_chat_mutation_parser.py` (12 passed)

## Decisions
- Graph-dir and workspace-root resolution use shared helpers; only fallback to absolute anchor when cwd is unavailable.
- Duplicate-edge repair belongs in chat mutation normalization, not in `GraphMutator`.
- Capability handlers treat typed Pydantic payloads as source of truth via `model_dump(...)`.
- Canonical validator contract: `input_ports=["data"]`, `output_ports=["valid", "invalid"]`.
- Server runtime dirs should be absolute and rooted at the resolved workspace.
- Port-alias repair stays conservative: only rewrite known stale aliases when replacement is unambiguous.

## Notes
- All patches emerged on 2026-03-27 as a chain of fixes during/after the 44-series rollout.
- Originally mis-numbered as standalone top-level plans 45–50; consolidated here for hierarchy consistency.
