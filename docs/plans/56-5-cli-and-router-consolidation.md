# 56-5: CLI and Router Consolidation

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** not-started
**Goal:** Replace substring-cue routers (`_is_website_objective`, `_supports_live_execution`) and per-product CLI builders with a single `select_orchestrator(intent, context) -> OrchestratorChoice` function that resolves to a brief composer, so dispatch becomes a single deterministic chokepoint with one swappable substrate.

## Tasks

- [ ] 1. Consolidate the deterministic dispatch surface
  - [ ] 1-1. Add `src/dan/cli/dispatch.py` with `select_orchestrator(intent, context) -> OrchestratorChoice`
  - [ ] 1-2. The function consumes the existing CLI-subcommand table from `cli/main.py:_SUBCOMMANDS` and the substring-cue logic that today lives in `cli/super_organism.py:_is_website_objective` / `_supports_live_execution`
  - [ ] 1-3. Keep substring matching for intent triage but isolate it behind one function so an LLM-based intent classifier could swap in later (`56-*` does not introduce one; this just preserves the option)
  - [ ] 1-4. `OrchestratorChoice` is a typed value: orchestrator id, brief composer reference, sampling policy, tool/runtime policy, organism plan template, and structured artifact/acceptance policy
- [ ] 2. Retire per-variant CLI builders
  - [ ] 2-1. Remove `_build_live_website_worker`, `_build_live_generic_worker`, `_build_live_website_validator`, `_build_live_generic_validator` from `cli/super_organism.py` (after `56-3` lands the brief-driven paths)
  - [ ] 2-2. Remove `_LIVE_WEBSITE_TOOL_IDS`, `_LIVE_GENERIC_TOOL_IDS`, `_LIVE_WEBSITE_FILES` constants — tool policy and required artifact sets now arrive as structured brief/plan policy data, not CLI globals or free-form task text
  - [ ] 2-3. Keep `_LIVE_FILE_WRITE_SAFE_WORD_LIMIT` and `_LIVE_FILE_WRITE_SAFE_LINE_LIMIT` only as product policy defaults consumed by the snippet library (e.g. `pacing_contract(policy=...)`), with brief override/omission support
  - [ ] 2-4. Remove `_WEBSITE_TEMPLATE_PHRASES` from `cli/super_organism.py`; the orchestrator brief now passes its own phrase list to `fail_predicates.anti_template_predicate(phrases)`
- [ ] 3. Update CLI entry points
  - [ ] 3-1. `dan code`, `dan research`, `dan reader`, `dan organism`, `dan super-organism` continue to exist as shells. Durable conversation controllers remain the public multi-turn shells; bounded execution turns route through: parse args / controller decision -> build brief composer -> call `select_orchestrator(...)` (deterministic) -> invoke `execute_universal_organism(...)`
  - [ ] 3-2. CLI-level argparse stays unchanged for backward compatibility
  - [ ] 3-3. CLI/editor live progress consumes throttled run-state deltas from the universal organism event stream; it does not poll the LLM orchestrator for status. Raw logs stay full-fidelity while terminal/UI rendering may coalesce high-frequency events.
- [ ] 4. Lock the new dispatch shape
  - [ ] 4-1. Add a regression that asserts `select_orchestrator(...)` returns a stable `OrchestratorChoice` for each existing CLI command + representative intent text
  - [ ] 4-2. Add an import-boundary check that no `cli/*.py` file constructs cell builders directly; everything goes through brief composers + `execute_universal_organism(...)`

## Decisions

- The dispatcher stays deterministic. An LLM intent classifier is explicitly a future-only option, behind the same `select_orchestrator(...)` function signature.
- The CLI subcommand table in `cli/main.py` stays as the top-level static dispatch (subcommand → module) because users type subcommands explicitly. The substring-cue logic that decides between organism variants moves into `select_orchestrator(...)` so it is the single chokepoint.
- After this plan, adding a new task family is one new brief composer plus optional policy profile in the snippet/template library and an entry in the dispatch table — no organism, no cell builder, no fixed CLI constants.
- Live progress streaming is a runtime/event concern. CLI routing selects the orchestrator and starts the run; progress comes from organism events, heartbeats, and state deltas, with optional semantic observer decisions surfaced as admitted/rejected decision records.

## Notes

- This is the cleanup pass. It only becomes safe after `56-1` / `56-2` / `56-3` / `56-4` have landed, because it removes the legacy per-variant code paths.
- After this plan, the answer to "where does task X live in the codebase?" is: a brief composer in `worker/contracts/templates.py`, plus optionally a CLI flag in the shared CLI shell. Not a new organism, not a new cell builder, not a new router cue.
- The grand-total deletion is meaningful: the per-variant builder functions, their constants, their substring router, their inline prompts, and their per-organism executors all collapse. The remaining surface is one cell, one organism, one dispatcher, plus the snippet/template library that captures the prompt-engineering wisdom.
- Future-only optionality preserved by this plan: an LLM intent classifier behind `select_orchestrator(...)`, a third sampling preset for niche cases, additional task-family templates as eval coverage demands them.
