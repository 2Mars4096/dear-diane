# 52-3: Organ Patterns Research Coding Validation Synthesis

**Parent:** [52-multicellular-composition](52-multicellular-composition.md)
**Status:** completed
**Goal:** Build bounded mixed-role modules that combine tissues and specialized cells into clear functional organs.

## Problem

The first organs cannot stay at the vague level of “research” or “validation.” If they do, the multicellular layer will blur back into loose agent clusters with no stable responsibility boundaries.

The first organ set should instead capture the real high-value functions DAN will need for a self-improving organism:

- a deep researcher that can gather, rank, cross-check, and synthesize evidence
- a universal validator that can score nearly any task outcome against a layered contract
- a coding/build organ that can act on validated plans
- a synthesis organ that can turn internal outputs into one coherent external result

## Dependencies

- `52-1` and `52-2` should already be landed so organs can compose the existing cell membrane and tissue patterns rather than inventing new ones.
- `52-3` should define stable organ boundaries before `52-4` chooses the first full organism benchmark.

## Tasks
- [x] 1. Choose the first bounded organs
  - [x] 1-1. Deep researcher organ
  - [x] 1-2. Universal validator organ
  - [x] 1-3. Coding/build organ
  - [x] 1-4. Synthesis organ
  - [x] 1-5. Define the promotion path for a later meta workflow builder organ once [45-4-meta-workflow-builder-eval-harness](45-4-meta-workflow-builder-eval-harness.md) proves itself
- [x] 2. Define strict organ boundaries
  - [x] 2-1. Input contract
  - [x] 2-2. Output contract
  - [x] 2-3. Internal escalation rules
  - [x] 2-4. Failure surface
  - [x] 2-5. Score/report schema where the organ emits evaluations rather than direct task output
- [x] 3. Define the first organ internals at a useful level
  - [x] 3-1. Deep researcher organ: retrieval/search, source reading, evidence ranking, contradiction checking, and synthesis cells
  - [x] 3-2. Universal validator organ: structural checks, task/contract completion checks, semantic quality checks, risk/confidence scoring, and missing-requirement reporting
- [x] 4. Prevent organ leakage
  - [x] 4-1. Keep internal cell chatter hidden behind bounded organ interfaces
- [x] 5. Validate specialization value
  - [x] 5-1. Show that mixing distinct cell roles inside one organ is better than a single oversized worker
- [x] 6. Pick one organ as the first hardened production module
  - [x] 6-1. Prefer the smallest organ with the clearest output contract

## Primary Files

- `src/dan/worker/organs/__init__.py`
- `src/dan/worker/organ.py`
- `src/dan/worker/tissue.py`
- `src/dan/worker/__init__.py`
- `src/dan/worker/organisms/project_execution.py`
- `tests/eval/test_bounded_organ_patterns.py`

## Success Criteria

- the deep researcher organ has one explicit external contract for grounded findings, evidence refs, contradictions/open questions, and confidence
- the universal validator organ has one explicit external contract for score breakdown, pass/fail/partial verdict, missing requirements, and remediation hints
- organs reuse the existing signaling and tissue primitives instead of inventing their own membrane
- the coding/build and synthesis organs remain bounded and do not absorb research/validation responsibilities by prompt accretion
- the meta workflow builder stays outside the initial organ set until `45-4` proves it deserves promotion

## Decisions
- Organs are mixed-role modules with strict external contracts, not informal bags of subagents.
- Organ internals can evolve, but the organ boundary should stay stable.
- The universal validator should score almost any task through one layered framework rather than becoming a single magical judge prompt.
- The meta workflow builder is not an initial organ default; it is a later promoted capability gated by `45-4`.
- The first reusable organ runtime should stay intentionally small: one tissue plus one lead cell behind a strict membrane instead of an open-ended recursive swarm surface.
- The first hardened production organ is the universal validator organ because its outward contract is clearer and more reusable than research/coding/synthesis first drafts.
- Organ boundary contracts should reject missing required keys and unexpected public keys by default so internal tissue chatter cannot leak through “helpful” extra fields.

## Notes
- The first organs should stay small and function-bounded. This phase is not the place to build a universal everything-organ.
- “Deep researcher” is intentionally stronger than plain retrieval: it should gather evidence, compare sources, surface contradictions, and produce grounded synthesis.
- “Universal validator” is the main self-evolution organ: build -> run -> validate -> score -> repair -> compare -> learn.
- The execution-ready target for this plan is not “have organ ideas.” It is “have the first organ contracts and module boundaries written tightly enough that implementation can start without another architectural round-trip.”
- Landed API surface in this slice:
  - `execute_organ_pattern(...)`
  - `deep_research_organ(...)`
  - `coding_build_organ(...)`
  - `universal_validator_organ(...)`
  - `synthesis_organ(...)`
- The hardened validator organ currently runs as:
  - review/quorum tissue
  - lead validator synthesis cell
  - strict top-level JSON input/output contract
  - explicit escalation codes for input contract, tissue failure, synthesis failure, and output contract failure
- The deep-research organ now also has a concrete bounded internal shape:
  - retrieval-enrichment tissue
  - requirement/implementation reader cells
  - one lead synthesis cell that emits grounded findings, evidence, and recommended changes
- The later meta-builder promotion path is now explicit but still deferred:
  - `52-4` keeps the first organism limited to planner + research/build/validator/synthesis organs
  - a future promoted builder/rebuilder organ must consume the same bounded validator/research outputs after [45-4-meta-workflow-builder-eval-harness](45-4-meta-workflow-builder-eval-harness.md) proves it deserves elevation
- Focused validation in the main workspace:
  - `PYTHONPATH=src python -m py_compile src/dan/worker/organ.py src/dan/worker/__init__.py tests/eval/test_bounded_organ_patterns.py`
  - `PYTHONPATH=src pytest -q tests/eval/test_bounded_organ_patterns.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_model.py`
