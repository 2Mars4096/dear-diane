# Bench 2: GAIA Level 3

**Parent:** [benchmark-plan](benchmark-plan.md)
**Status:** planned
**Goal:** Establish DAN's first leaderboard-native public score track on a widely recognized general-agent benchmark, showing that DAN succeeds on 6+ step real-world tasks where monolithic agents fail.

## Why This Benchmark

GAIA is the most cited general agent benchmark. It tests multi-step reasoning with real tool use on questions that require web browsing, file processing, and multi-hop inference. Humans score 92%, GPT-4 with plugins scored 15%. Level 3 tasks (6+ steps with tool chaining) are exactly where monolithic agents break down and DAN's graph architecture should shine.

- **Paper:** [GAIA: A Benchmark for General AI Assistants](https://arxiv.org/abs/2311.12983)
- **HuggingFace:** https://huggingface.co/datasets/gaia-benchmark/GAIA
- **Leaderboard:** https://huggingface.co/spaces/gaia-benchmark/leaderboard
- **Size:** 466 questions (165 Level 1, 86 Level 2, 215 Level 3)
- **Evaluation:** Exact match against ground-truth answers
- **Key baseline:** Top systems ~70% Level 1, ~55% Level 2, ~35% Level 3

## Position In Execution Order

GAIA should be the **first public benchmark DAN refreshes regularly**.

Recommended gate:

- finish the Phase 33 honest rerun
- close the benchmark-blocking parts of `33-10` for any code-heavy generation paths used in GAIA
- reuse the thin reporting/reproducibility layer from [bench-5-analysis-framework](bench-5-analysis-framework.md) rather than inventing a separate runner

Recommended rollout:

1. validation split only
2. Level 3 subset with stable artifacts
3. full validation split
4. official leaderboard submission on the hidden test split

## Current Readiness

What is already true:

- benchmark execution trust hardening from Phase 29 (`42-*`) is complete
- the benchmark smoke-path workflow hardening from Phase 30 (`43-*`) is complete
- the benchmark program already has a public-first master spec and a thin-framework direction

What is still required before publication:

- the minimal `bench-5` reporting/profile slice must exist
- the relevant Phase 33 gate must be green enough for external publication
- the run must execute in the benchmark-grade server path, not an ad-hoc local/debug path

This plan should therefore be treated as three explicit stages: **pilot wiring**, **public validation artifact**, and **official leaderboard submission**.

## What DAN's Architecture Should Prove

| DAN Feature | GAIA Signal |
|-------------|------------|
| Meta-orchestrator decomposition | Correct multi-step plans for 6+ step questions |
| Context projection | Bounded token usage even on deep chains |
| Model heterogeneity | Cheap models for simple extraction, expensive for reasoning |
| Checkpoint recovery | Resume from mid-chain failure instead of restarting |
| Tool composition | web_search → pdf_read → python_eval chains |
| While-loop quality gate | Re-attempt extraction when first parse fails |

## Adapter Design

### Task Parser

```
GAIA question JSON
  → extract question text
  → extract attached files (PDF, spreadsheet, image)
  → identify required tool categories (web, file, code, reasoning)
  → format as concierge/MetaController input
```

GAIA questions come with optional file attachments. The adapter must stage files in DAN's artifact store before workflow execution.

### Generation Path

Two strategies, benchmarked against each other:

**Strategy A: Full meta-orchestrator**
```
Question + files
        ↓
MetaController.plan()  →  workflow graph
        ↓
Engine.run(graph)      →  tool execution
        ↓
Extract final answer from RunResult
```

**Strategy B: Solver-direct**
```
Question + files
        ↓
Concierge solver path  →  direct tool use
        ↓
Extract final answer from response
```

Compare both strategies to isolate when workflow decomposition adds value vs. when direct tool use suffices.

For public reporting, DAN should choose **one primary public profile** and keep the other DAN strategy as analysis rather than score-shopping. The primary profile may be the highest-scoring DAN mode, but it must be frozen before the official validation publication run.

### Tool Mapping

| GAIA Capability | DAN Tool |
|----------------|----------|
| Web browsing | `web_search`, `web_fetch` |
| PDF reading | `pdf_read` |
| Spreadsheet processing | `csv_read`, `spreadsheet_read`, `python_eval` |
| Image understanding | `image_describe` |
| Code execution | `python_eval` |
| File I/O | `file_read`, `file_write` |
| Arithmetic/reasoning | LLM operator (direct) |

### Answer Extractor

GAIA expects short, exact-match answers. The extractor must:
- Parse the final node/response output
- Strip formatting, units, and explanation
- Normalize numbers, dates, names to GAIA expected format
- Handle multi-part answers (comma-separated)

## Submission And Refresh Policy

GAIA is the cleanest external record because it has a public leaderboard. Treat it as a repeatable score track, not a one-time stunt.

Every GAIA refresh should publish:

- git commit SHA
- DAN model/tier configuration
- memory mode
- learning mode
- benchmark split (`validation` or `test`)
- artifact bundle with raw predictions and scored outputs where allowed

Refresh cadence:

1. after any major release that materially changes routing, planning, memory, or tool execution
2. after any dedicated benchmark-improvement effort intended to move GAIA
3. not while the internal Phase 33 gate is red

## Fairness And Red Lines

- Run GAIA through the strict benchmark-grade server path only; do not use convenience local/debug execution for public numbers.
- Freeze one public DAN profile before the first publishable validation run. Do not choose the headline path after looking at hidden-test outcomes.
- Use the same underlying model family and same tool surface for DAN and the monolithic baseline unless a benchmark rule forbids it.
- Report coverage explicitly. If a task is excluded because DAN lacks a required tool, mark it as `coverage_excluded`; do not silently drop it from the denominator.
- Do not tune on the hidden GAIA test split. All tuning, prompt edits, and profile selection must happen on validation or smaller pilot subsets.
- Keep benchmark memory effectively off unless a GAIA-specific experiment explicitly studies memory. Cross-task memory carryover would contaminate the score.

## Run Profiles

The plan should standardize a small set of named GAIA profiles instead of ad-hoc runs:

| Profile | Purpose | Scope |
|---------|---------|-------|
| `gaia_smoke_5` | wiring smoke test | 5 covered validation examples across mixed tool types |
| `gaia_l3_pilot_25` | first real pilot | 25 covered Level 3 validation examples |
| `gaia_validation_primary_v1` | first publishable artifact | full covered validation split on the frozen primary DAN profile |
| `gaia_validation_monolithic_v1` | fair external-style comparison | same covered validation subset, monolithic baseline |
| `gaia_test_submission_v1` | official public score | hidden GAIA test submission with the frozen primary DAN profile |

## Tasks

- [ ] 0. **Entry gate and public-profile freeze**
  - [ ] 0-1. Implement the minimal `bench-5` profile/artifact bundle slice required for public GAIA reporting
  - [ ] 0-2. Confirm the relevant Phase 33 gate is green enough for external publication
  - [ ] 0-3. Freeze one primary public DAN profile: approach, model/tier map, memory mode, learning mode, behavior snapshot, server path
  - [ ] 0-4. Decide the secondary DAN strategy that will be kept for analysis only (`dan_meta` or `dan_solver`)
- [ ] 1. **Dataset and coverage inventory**
  - [ ] 1-1. Load and pin the GAIA dataset revision / snapshot used for the run
  - [ ] 1-2. Stage file attachments into DAN's benchmark artifact path
  - [ ] 1-3. Categorize questions by level, attachment type, and required tool categories
  - [ ] 1-4. Classify each question as `covered` or `coverage_excluded` based on DAN's actual available tools
- [ ] 2. **Task and answer adapter**
  - [ ] 2-1. Parse GAIA question format into DAN benchmark input
  - [ ] 2-2. Implement attachment staging and per-question tool availability checks
  - [ ] 2-3. Build answer normalization for numbers, dates, names, units, and multi-part answers
  - [ ] 2-4. Validate the normalization path against the GAIA exact-match evaluator on a pilot subset
- [ ] 3. **Pilot runs**
  - [ ] 3-1. Run `gaia_smoke_5` through the benchmark-grade server path
  - [ ] 3-2. Run `gaia_l3_pilot_25` on covered Level 3 validation questions
  - [ ] 3-3. Verify artifact completeness: predictions, score outputs, profile snapshot, coverage accounting, failure taxonomy
  - [ ] 3-4. Freeze any final prompt/profile adjustments before the publishable validation run
- [ ] 4. **First public validation artifact**
  - [ ] 4-1. Run `gaia_validation_primary_v1` on the full covered validation split
  - [ ] 4-2. Run `gaia_validation_monolithic_v1` on the exact same covered subset
  - [ ] 4-3. Publish a score table with raw accuracy, covered accuracy, coverage rate, token cost, wall-clock time, and run-failure rate
  - [ ] 4-4. Record the result bundle location, commit SHA, benchmark revision, and run date in the public score log
- [ ] 5. **Official leaderboard submission**
  - [ ] 5-1. Freeze the validation artifact and config snapshot as the publication basis
  - [ ] 5-2. Run `gaia_test_submission_v1` on the hidden test split with the frozen primary profile
  - [ ] 5-3. Submit predictions to the public leaderboard
  - [ ] 5-4. Record the submission date and returned leaderboard score in the refresh log
- [ ] 6. **Secondary analysis after the first public score exists**
  - [ ] 6-1. Compare the secondary DAN strategy against the primary profile on validation only
  - [ ] 6-2. Run limited Level 3 ablations on the covered validation subset
  - [ ] 6-3. Break down failures into planning, tool-use, extraction, normalization, timeout, and execution-contract errors

## Success Criteria

The first GAIA phase is successful if it produces all of the following:

- one clean validation artifact bundle for a frozen primary DAN profile
- one fair monolithic baseline artifact on the same covered subset
- explicit coverage accounting instead of silent exclusions
- one official hidden-test submission with a commit-linked refresh record

Score movement matters, but the first benchmark goal is not "win GAIA immediately." It is "establish a trustworthy, refreshable public GAIA record."

## Estimated Effort

- Dataset + adapter + coverage inventory: 2 days
- Pilot runs + answer normalization hardening: 1-2 days
- Primary validation artifact + monolithic baseline: 2 days
- Official submission + reporting: 1 day
- Secondary analysis / ablations: 1-2 days
- **Total first publishable cycle: ~7-9 days**

## Decisions

- (to be filled during execution)

## Notes

- GAIA test split answers are hidden — submit to leaderboard for official scores
- Validation split has visible answers — use for development and ablation
- Some GAIA questions require real-time web data that may have changed since dataset creation
- Image questions require `image_describe` tool — skip if not available, report coverage
- Leaderboard placement is the highest-visibility marketing deliverable from this benchmark suite
- GAIA should be DAN's first public benchmark refresh loop because it combines visibility, standardization, and a relatively manageable integration surface.
- Do not mix benchmark-development tuning with official submission runs. Treat validation as development and test submission as publication.
- The most important failure to avoid is not "low score." It is "non-reproducible or ambiguously filtered score."
