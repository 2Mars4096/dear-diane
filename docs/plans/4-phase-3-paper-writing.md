# 4: Phase 3 — Paper-Writing Proof of Concept

**Status:** completed
**Goal:** Build an end-to-end paper-writing workflow as a Python script using the builder DSL, exercising all node types, ForEach, WhileLoop, structured output, tool integration, and visual editor loading.

## Tasks
- [x] 1. Create `examples/paper_writing.py`
  - [x] 1-1. Define workflow graph via builder DSL (LLM x3 + ForEach body + WhileLoop body, Code x2, Tool x1, ForEach x1, WhileLoop x1 = 8 nodes)
  - [x] 1-2. Wire ToolRegistry with `save_paper` function; build custom ExecutorRegistry
  - [x] 1-3. Compile graph, save JSON to `graphs/paper_writing.json`
  - [x] 1-4. Run workflow via `Engine.run()` with configurable topic
  - [x] 1-5. Print progress via event callback, save output to `output/`
- [x] 2. Create `tests/test_examples/test_paper_writing_e2e.py`
  - [x] 2-1. Happy-path mock e2e test (6 tests)
  - [x] 2-2. Tool-failure recovery test (2 tests)
  - [x] 2-3. Checkpoint/resume mid-workflow test (1 test)
- [x] 3. Run pytest — 252 pass (243 existing + 9 new)
- [x] 4. Run live against vectorengine.ai — all 8 nodes completed, outline needed 2 norm attempts, review loop ran 2 iterations
- [x] 5. Verify `graphs/paper_writing.json` loads in graph store
- [x] 6. Update docs: `todo.md`, `changelog.md`, `architecture.md`

## Decisions
- Single `review_and_revise` LLM node in WhileLoop body (not separate reviewer + reviser) for simpler data flow
- Assembler outputs `{"draft": ..., "verdict": "pending", "feedback": "..."}` to bootstrap WhileLoop condition
- Explicit ToolRegistry wiring: pre-register tool_operator in ExecutorRegistry before Engine init (avoids empty-registry default)
- Explicit edge wiring for structured outputs (outline_planner schema ports, assembler code outputs)
- Three reliability tests: happy-path, tool failure, checkpoint/resume

## Notes
- Outline planner needed 2 normalization attempts on live run (LLM initially returned non-JSON, re-prompt succeeded)
- ForEach generated 8 sections (model chose an 8-section outline) and processed them with parallelism=3
- Review loop ran 2 full iterations before accepting on the live run
- Identified 3 reliability gaps surfaced by this PoC, added to backlog: tool retry, per-node retry policy, handoff validators
