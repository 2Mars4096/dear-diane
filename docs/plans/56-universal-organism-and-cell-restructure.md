# 56: Universal Organism and Cell Restructure

**Status:** not-started
**Goal:** Collapse the per-product organism / worker / validator inventory into one universal organism and one universal cell, with task specialization living in a shared parameterized prompt-template library composed by orchestrator briefs instead of per-variant builder functions, constants, and substring-cue routers.

## Tasks

- [ ] 1. Land the universal cell substrate via [56-1-universal-cell-and-fixed-system-prompt](56-1-universal-cell-and-fixed-system-prompt.md)
- [ ] 2. Build the parameterized prompt-template library via [56-2-task-prompt-template-library](56-2-task-prompt-template-library.md)
- [ ] 3. Move task specialization into orchestrator-composed briefs via [56-3-orchestrator-brief-driven-specialization](56-3-orchestrator-brief-driven-specialization.md)
- [ ] 4. Collapse organism families into one universal organism via [56-4-collapse-organisms-into-one](56-4-collapse-organisms-into-one.md)
- [ ] 5. Retire substring routers and per-variant CLI builders via [56-5-cli-and-router-consolidation](56-5-cli-and-router-consolidation.md)

## Decisions

- One `WorkerDefinition` for every cell. No more `_build_live_website_worker`, `_build_live_generic_worker`, `_build_live_website_validator`, `_build_live_generic_validator`, etc.
- Cell system prompt is invariant ("you are an execution cell") and contains zero domain knowledge. It states only role identity, operating principles, hard/soft constraint discipline, tool/budget discipline, output-shape discipline, and graceful-fallback behavior.
- 100% of task-specific behavior moves into the orchestrator's brief, composed from a shared parameterized template library. The brief is the program; the cell is the interpreter.
- Validators stop being a code concept; they become a brief shape (read-only tool clamp, deterministic sampling preset, fail-heavy contract) emitted by the orchestrator.
- One universal organism replaces the per-product execution paths in `coding_execution`, `project_execution` (which today holds DAN Research execution), `super_organism` live lanes, `incident_execution`, and `reference_demo`. Different products differ by `OrganismPlan` shape, not by organism class.
- The migrated execution files are archived under `src/dan/worker/organisms/_archive/` rather than deleted outright, so trace replay and bug-fix back-porting still have access to the legacy stage-orchestration logic. Public exports in `worker/organisms/__init__.py` rebind to thin facades over `execute_universal_organism(...)` so external callers, CLI entry points, and tests do not break in one big step.
- Sampling is externalized to two named presets: `creative` (worker) and `deterministic` (validator). Per-cell `temperature=0.30 / 0.35 / 0.0` constants disappear from organism builders.
- Dispatcher stays deterministic. The substring-cue router (`_is_website_objective`, `_supports_live_execution`) and per-variant CLI builders consolidate behind a single `select_orchestrator(intent, context) -> OrchestratorChoice` function that resolves to a brief composer, not to an organism class. An LLM intent classifier remains a future-only option behind the same signature.
- The cell tool loop, model gateway dispatch, scheduler / contracts / policy, capsule bus, readiness signals, member-completion callbacks, and speculative validation stay where they are. This work touches only the prompt-assembly + organism-orchestration layers above them.

## Notes

- This work is a structural follow-up to `46-7` (worker bundle extraction) and the `54-*` DAN-v2 control-plane stack. It does not change the cell tool loop, the model gateway, or the scheduler — only what those substrates ingest.
- Concrete pain point that triggered this plan: `cli/super_organism.py` carries `_build_live_website_worker`, `_build_live_generic_worker`, `_build_live_website_validator`, `_build_live_generic_validator`, plus matching constants `_LIVE_WEBSITE_TOOL_IDS`, `_LIVE_GENERIC_TOOL_IDS`, `_LIVE_WEBSITE_FILES`, `_WEBSITE_TEMPLATE_PHRASES`, plus substring-cue router functions `_is_website_objective`, `_supports_live_execution`. Each new variant requires copy-paste-modify across all of these. The same shape recurs across `coding_execution.py`, `project_execution.py`, `incident_execution.py`, and `reference_demo.py`.
- The existing `_live_pacing_contract()` helper (a parameterized text snippet shared between worker `instruction` and `system_prompt`) is the right pattern, generalized to all contract bullets, fail predicates, recovery hints, and output shapes.
- After landing this work, adding a "build a CLI tool" mode is one new brief composer in `worker/contracts/templates.py`, not a new organism + builders + constants + router cues.
- This is intentionally a refactor pass, not a redesign. `WorkerCoreExecutor`, `ToolLoopCompletionProvider`, `ExecutionRequest` / `OutputContract`, `ContextCapsule` plumbing, scheduler guardrails, and the durable conversation controllers (`*_conversation.py`) all stay as they are.
- The riskiest slice is `56-4`: `coding_execution.py` is ~3000 lines of scheduler/readiness/repair wiring that is the de-facto reference. Migration must preserve its behavior and emergent invariants while moving the implementation onto the shared substrate.
