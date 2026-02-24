# 4-1: Grounded Paper-Writing Upgrade

**Parent:** [4-phase-3-paper-writing](4-phase-3-paper-writing.md)
**Status:** completed
**Goal:** Upgrade the paper-writing workflow from a mock literature/writing demo to an INFORMS-oriented, internet-grounded, evidence-gated pipeline with human interview and stronger review-revise rigor.

## Tasks
- [x] 1. Rebuild exploration as grounded parallel survey
  - [x] 1-1. Replace single `lit_survey` LLM call with `aspect_planner -> for_each(search_papers -> survey_aspect) -> lit_synthesizer`
  - [x] 1-2. Add citation verification + claim-evidence gate outputs before outline freeze
  - [x] 1-3. Expand outline schema for INFORMS targeting (`journal`, richer section structure, keywords)
- [x] 2. Add human-in-the-loop interview loop during exploration
  - [x] 2-1. Add `interview_loop` with iterative interviewer/refiner logic
  - [x] 2-2. Wire `human_in_the_loop` node with CLI callback support
  - [x] 2-3. Ensure max rounds and termination semantics match requirements
- [x] 3. Upgrade writing and review flow
  - [x] 3-1. Generate LaTeX section content and compile to PDF
  - [x] 3-2. Add evidence-aware writing inputs (verified literature + results artifacts)
  - [x] 3-3. Replace single reviewer with parallel method/writing/venue review panel and merged revision plan
- [x] 4. Add tooling for real search and submission artifacts
  - [x] 4-1. Implement tool functions in `examples/paper_writing.py` (`search_papers`, `search_web`, `citation_verifier`, `compile_latex`, `check_latex_deps`, `save_paper`, `package_submission`)
  - [x] 4-2. Register relevant built-in tools in `src/dan/server/app.py`
  - [x] 4-3. Save `.tex`, `.bib`, `.pdf`, and submission bundle outputs
- [x] 5. Update tests and docs
  - [x] 5-1. Update `tests/test_examples/test_paper_writing_e2e.py` for new graph topology and tool usage
  - [x] 5-2. Verify mock execution still runs deterministically in tests
  - [x] 5-3. Sync `docs/todo.md`, `docs/changelog.md`, and `docs/architecture.md`

## Decisions
- Prioritize deterministic architecture and testability over perfect publication quality in one pass.
- Keep external search as tool-operator based (no new node primitives) to preserve engine simplicity.
- Use parallel fan-out for literature aspects and review roles to mirror real workflow decomposition.

## Notes
- Initial implementation should degrade gracefully when optional web-search credentials are missing.
- INFORMS `informs3.cls` / `informs2014.bst` are not bundled and must be checked at runtime.
