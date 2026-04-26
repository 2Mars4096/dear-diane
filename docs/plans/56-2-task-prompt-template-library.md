# 56-2: Task Prompt Template Library

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** completed
**Goal:** Promote scattered prompt fragments into a shared parameterized template library so orchestrators compose task-specific briefs into a fixed prompt architecture, instead of duplicating paragraphs across `_build_live_*_worker` / `_build_live_*_validator` functions.

## Tasks

- [x] 1. Establish the snippet module layout
  - [x] 1-1. Add `src/dan/worker/contracts/snippets.py` for parameterized contract-bullet functions (pacing, incremental edits, no-scratch-files, read-only enforcement, policy-bound warnings)
  - [x] 1-2. Add `src/dan/worker/contracts/output_shapes.py` for canonical JSON example payloads + matching JSON-Schema strings (validation report, coding completion summary, research evidence note, scheduler proposal)
  - [x] 1-3. Add `src/dan/worker/contracts/fail_predicates.py` for parameterized fail-predicate phrasing + optional runtime-check predicates (anti-template, single-file-redesign, prompt-echo, low-mutation-coverage)
  - [x] 1-4. Add `src/dan/worker/contracts/recovery_hints.py` for parameterized "if X is rejected, do Y" hints (split oversized writes, prefer file_edit over file_write, narrow scope then retry, switch to grounded read)
  - [x] 1-5. Add `src/dan/worker/contracts/sampling.py` exposing baseline profiles plus explicit brief-level override support from 56-1
  - [x] 1-6. Add a centrally managed `PromptContext` / `PromptEnvelope` contract that carries the stable prompt architecture id, system constitution id, tool-schema hash, policy-schema hash, output-contract hash, prompt-slot values, dynamic snapshot refs, and rendered-prompt provenance
- [x] 2. Move existing reusable strings into the snippet library
  - [x] 2-1. Promote `_live_pacing_contract()` from `cli/super_organism.py` into `snippets.pacing_contract(policy)` where word/line guidance is optional and supplied by the caller
  - [x] 2-2. Promote `_live_validation_return_shape()` and `_live_expected_return_shape()` into `output_shapes.validation_v1()` / `output_shapes.coding_v1()`
  - [x] 2-3. Promote `_WEBSITE_TEMPLATE_PHRASES` into `fail_predicates.anti_template_predicate(phrases)` so callers can supply their own phrase list
  - [x] 2-4. Promote `_LIVE_FILE_WRITE_SAFE_WORD_LIMIT` / `_LIVE_FILE_WRITE_SAFE_LINE_LIMIT` into a product policy default consumed by `pacing_contract(...)`, with brief-level override or omission support
  - [x] 2-5. Add deduplicated `recovery_hints.split_oversized_write()`, `recovery_hints.prefer_file_edit()`, `recovery_hints.narrow_scope_then_retry()` from existing inline phrasing
- [x] 3. Add task-family templates
  - [x] 3-1. `templates.role_brief(...)` — composes a generic orchestrator-discovered `RoleSpec` into prompt slots, policies, and output contract
  - [x] 3-2. `templates.coding_brief(...)` — optional profile that composes pacing + incremental-edits + no-scratch + coding output shape for any role the orchestrator decides needs code mutation
  - [x] 3-3. `templates.review_brief(...)` — optional profile that composes read-only tool policy + reviewer output shape + failure predicates for any reviewer/auditor role the orchestrator discovers
  - [x] 3-4. `templates.research_brief(...)` — optional profile that composes source-grounding, evidence-note output shape, and read-only/retrieval policy for any research role the orchestrator discovers
  - [x] 3-5. `templates.scheduler_brief(...)` — optional profile that composes scheduler-action JSON shape + guardrail context when the orchestrator chooses to create a scheduling role
- [x] 4. Lock the template surface with regressions
  - [x] 4-1. Snapshot the rendered text of each template under representative inputs to catch accidental drift
  - [x] 4-2. Prove that two templates can share a snippet (e.g. coding-worker and explorer both depend on `pacing_contract`) without duplication
  - [x] 4-3. Cover the policy-value parity invariant: when a snippet mentions a number (e.g. word/line guidance), the same brief policy value is also what runtime checks use; if the brief omits a cap, the snippet must not invent one
  - [x] 4-4. Cover prompt-cache discipline: stable prompt/tool/schema fingerprints stay unchanged across routine event/context deltas, while only dynamic slots/snapshot refs vary

## Decisions

- Snippets are pure functions returning strings. They never read live state; all variability is via parameters.
- Templates are compositions of snippets plus role/policy prompt slots. Adding a new task family is usually a new role-composer or policy profile, not a new organism file or fixed role. The orchestrator decides which roles exist for a task, and every role still renders through the fixed prompt architecture.
- Prompt context is centrally managed. Callers do not hand-assemble prompt prefixes; they provide slot values, refs, and policy objects, and the prompt renderer emits a `PromptEnvelope` with stable fingerprints so cache breaks are visible.
- Numeric thresholds in snippets are policy-supplied and optional. The same policy value flows into both the prompt and the runtime check, but snippets must not introduce hidden caps that the orchestrator did not select.
- Output shapes ship as JSON examples first, with JSON Schema as a backstop. The cell prompt shows the example; the runtime parser uses the schema, with `OutputContract.output_schema` already wired through `WorkerCoreExecutor` for repair.
- Fail predicates and recovery hints are paired: every fail predicate that the runtime can detect should have a matching recovery hint that tells the model what to do instead.

## Notes

- 2026-04-26: first contract library landed under `src/dan/worker/contracts/` with pure snippets, canonical output-shape examples/schema helpers, failure predicates, recovery hints, sampling profiles/overrides, prompt context fingerprints, and role/coding/review/research/scheduler brief composers.
- 2026-04-26 follow-up: rendered prompt hash snapshots now cover `role_brief`, `coding_brief`, `review_brief`, `research_brief`, and `scheduler_brief`; a shared-snippet regression proves `pacing_contract(...)` can be reused by coding/explorer-style briefs without duplicating rendered text.
- This is the home for accumulated prompt-engineering wisdom. New eval failure modes become new fail-predicate or recovery-hint snippets here, used by every orchestrator.
- Prompt-cache friendliness is part of the design: stable constitution/tool/schema sections should be reused across calls, and live progress/context should enter through compact dynamic slots or snapshot refs rather than rewriting the whole prompt surface.
- The five task families listed in task 3 are an opening set. New families are added as needed without touching the cell or organism layers.
- The snippet library is the only place where prompt-engineering rules cross-reference deterministic runtime checks. If a product policy changes a runtime threshold (e.g. word guidance), the snippet exposes it through policy data so the prompt updates in lockstep; infrastructure defaults should not silently cap new task families.
- This module deliberately stays string-pure with no DAN runtime imports, so it is also reusable by any future external worker bundle (`46-7`).
