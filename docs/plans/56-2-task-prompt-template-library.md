# 56-2: Task Prompt Template Library

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** not-started
**Goal:** Promote scattered prompt fragments into a shared parameterized template library so orchestrators compose briefs from the same prompt-engineering wisdom, instead of duplicating paragraphs across `_build_live_*_worker` / `_build_live_*_validator` functions.

## Tasks

- [ ] 1. Establish the snippet module layout
  - [ ] 1-1. Add `src/dan/worker/contracts/snippets.py` for parameterized contract-bullet functions (pacing, incremental edits, no-scratch-files, read-only enforcement, threshold warnings)
  - [ ] 1-2. Add `src/dan/worker/contracts/output_shapes.py` for canonical JSON example payloads + matching JSON-Schema strings (validation report, coding completion summary, research evidence note, scheduler proposal)
  - [ ] 1-3. Add `src/dan/worker/contracts/fail_predicates.py` for parameterized fail-predicate phrasing + optional runtime-check predicates (anti-template, single-file-redesign, prompt-echo, low-mutation-coverage)
  - [ ] 1-4. Add `src/dan/worker/contracts/recovery_hints.py` for parameterized "if X is rejected, do Y" hints (split oversized writes, prefer file_edit over file_write, narrow scope then retry, switch to grounded read)
  - [ ] 1-5. Add `src/dan/worker/contracts/sampling.py` exposing the two named presets from 56-1
- [ ] 2. Move existing reusable strings into the snippet library
  - [ ] 2-1. Promote `_live_pacing_contract()` from `cli/super_organism.py` into `snippets.pacing_contract(word_limit, line_limit)`
  - [ ] 2-2. Promote `_live_validation_return_shape()` and `_live_expected_return_shape()` into `output_shapes.validation_v1()` / `output_shapes.coding_v1()`
  - [ ] 2-3. Promote `_WEBSITE_TEMPLATE_PHRASES` into `fail_predicates.anti_template_predicate(phrases)` so callers can supply their own phrase list
  - [ ] 2-4. Promote `_LIVE_FILE_WRITE_SAFE_WORD_LIMIT` / `_LIVE_FILE_WRITE_SAFE_LINE_LIMIT` into snippet defaults consumed by `pacing_contract(...)`
  - [ ] 2-5. Add deduplicated `recovery_hints.split_oversized_write()`, `recovery_hints.prefer_file_edit()`, `recovery_hints.narrow_scope_then_retry()` from existing inline phrasing
- [ ] 3. Add task-family templates
  - [ ] 3-1. `templates.coding_worker_brief(...)` — composes pacing + incremental-edits + no-scratch + coding output shape
  - [ ] 3-2. `templates.validator_brief(...)` — composes read-only tool clamp + deterministic sampling + validation output shape + anti-template predicate
  - [ ] 3-3. `templates.research_reader_brief(...)` — composes read-only tools + grounded-source preference + research evidence-note output shape
  - [ ] 3-4. `templates.explorer_brief(...)` — composes read-only tools + listing-first heuristics + capsule-style output shape
  - [ ] 3-5. `templates.scheduler_brief(...)` — composes scheduler-action JSON shape + guardrail context
- [ ] 4. Lock the template surface with regressions
  - [ ] 4-1. Snapshot the rendered text of each template under representative inputs to catch accidental drift
  - [ ] 4-2. Prove that two templates can share a snippet (e.g. coding-worker and explorer both depend on `pacing_contract`) without duplication
  - [ ] 4-3. Cover the numeric-threshold parity invariant: when a snippet mentions a number (e.g. word/line limit), the same number is also the runtime check threshold

## Decisions

- Snippets are pure functions returning strings. They never read live state; all variability is via parameters.
- Templates are compositions of snippets plus a small task-family preamble. Adding a new task family is one new template function, not a new organism file.
- Numeric thresholds in snippets are parameterized so the same number flows into both the prompt and the runtime check (the "numeric-threshold parity" prompt-engineering rule).
- Output shapes ship as JSON examples first, with JSON Schema as a backstop. The cell prompt shows the example; the runtime parser uses the schema, with `OutputContract.output_schema` already wired through `WorkerCoreExecutor` for repair.
- Fail predicates and recovery hints are paired: every fail predicate that the runtime can detect should have a matching recovery hint that tells the model what to do instead.

## Notes

- This is the home for accumulated prompt-engineering wisdom. New eval failure modes become new fail-predicate or recovery-hint snippets here, used by every orchestrator.
- The five task families listed in task 3 are an opening set. New families are added as needed without touching the cell or organism layers.
- The snippet library is the only place where prompt-engineering rules cross-reference deterministic runtime checks. If you change a runtime threshold (e.g. word limit), the snippet exposes it through a parameter so the prompt updates in lockstep.
- This module deliberately stays string-pure with no DAN runtime imports, so it is also reusable by any future external worker bundle (`46-7`).
