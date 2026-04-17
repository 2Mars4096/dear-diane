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
  - ad hoc reader fan-out sized per task breadth and capped at 8 concurrent readers
  - one lead synthesis cell that emits grounded findings, evidence, and recommended changes
- The later meta-builder promotion path is now explicit but still deferred:
  - `52-4` keeps the first organism limited to planner + research/build/validator/synthesis organs
  - a future promoted builder/rebuilder organ must consume the same bounded validator/research outputs after [45-4-meta-workflow-builder-eval-harness](45-4-meta-workflow-builder-eval-harness.md) proves it deserves elevation
- 2026-04-13 follow-up:
  - the deep-research organ contract is now explicitly stronger at the public membrane and requires `evidence_refs`, `contradictions`, and `confidence` in addition to `findings`, `evidence_summary`, `open_questions`, and `recommended_change`
  - the organ shape is still intentionally unchanged at the substrate level: one retrieval-enrichment tissue and one lead synthesis cell behind the same bounded membrane, with reader fan-out now chosen ad hoc per task and capped at 8 concurrent readers instead of being fixed at 2
  - `dan organism` / `dan-organism` now has a thin `--research-only` surface that executes just the deep-research organ through the same local runtime instead of forcing callers through the full reference organism
  - the reference organism now threads focused validation commands plus hard/soft constraints deeper into the research/build/validate/synthesis path and honors the planner-selected `pass_threshold` for final completion
- 2026-04-14 follow-up:
  - the standalone deep-research path now keeps task-derived reader briefs instead of falling back to static role suffixes unless the caller explicitly overrides them
  - DAN now has a first-class durable research product surface (`dan research` / `dan-research` / `danresearch`) built as `provider wrapper -> durable research orchestrator -> bounded deep-research organ`, mirroring the same thin product seam used by DAN Code without changing the universal-agent substrate
  - that research product persists workspace-local state under `.dan-research/`, keeps the research tool basket read-only, and exposes width/depth controls at the top level: auto-sized or explicit readers up to 8, plus `--depth shallow|standard|deep` or raw tool-loop budgets
  - the remaining known limitation is budget shape: current research depth profiles still map to per-worker local tool-loop limits rather than one shared organ-wide budget envelope
  - live-run hardening on the same date exposed one more useful boundary lesson: the durable product should tolerate small model/tool formatting drift at the narrow runtime/report seam without weakening the organ contract, so the local runtime now normalizes accidental `web_search queries=[...]` payloads and the research report seam now coerces confidence labels/percentages like `MEDIUM-HIGH (70%)` into bounded numeric scores
  - one more durable-product lesson from 2026-04-15: session/config persistence needs the same narrow hardening philosophy as tool/report seams, so the research product now treats blank/invalid persisted JSON as recoverable state and writes session/config files atomically instead of assuming in-place overwrites are always safe
  - one more durable-product lesson from 2026-04-15: per-run tool/model logs were not enough to explain quiet periods above the bounded run, so the research product now also keeps a workspace-level `.dan-research/control-plane-events.jsonl` trace with timestamped CLI/session/orchestrator events while leaving the universal-agent substrate unchanged
  - one more durable-product lesson from 2026-04-15: the research control plane had avoidable single-shot latency above the bounded organ, so the durable research orchestrator now reuses the same conservative delayed-second-attempt hedge pattern as DAN Code for turn/review controller calls, again without changing the universal-agent cell or organ substrate
  - one more durable-product lesson from 2026-04-15: generic current-entity grounding failures belong at the product seam, not in one-off ticker patches, so the research surface now requires authoritative verification when a concrete external entity materially affects the conclusion and refuses to mark low-confidence or weakly-evidenced reports as done, with grounded page reads exposed at the product seam without changing the universal-agent cell or the bounded deep-research organ topology
  - one more durable-product lesson from 2026-04-15: asking the model to “prefer better sources” was not enough on its own, so the research surface now improves the discovery side as well by ranking primary sources above retail aggregators in `web_search`, and the public report membrane now includes a structured verification appendix for critical facts instead of leaving that adjudication implicit in prose
  - one more durable-product lesson from 2026-04-15: after the generic grounding hardening landed, the public DAN Research surface was simpler and clearer if discovery plus authoritative page reads stayed under one visible tool name, so the product now exposes only `web_search` by default and lets that same tool fetch authoritative result pages or a direct `url=` internally without changing the universal-agent cell or bounded organ topology
  - one more durable-product lesson from 2026-04-15: stronger fact verification still was not enough on its own, because the final report could remain logically weak or stale even when its fact appendix looked cleaner, so the public DAN Research membrane now also carries a structured audit appendix for unresolved logic/freshness/authority/method/scope-fit gaps plus explicit `report_readiness` / `readiness_note` fields, and the durable review gate now blocks provisional/blocked reports or blocking audit issues without changing the universal-agent cell or bounded organ topology
  - one more durable-product lesson from 2026-04-16: the next generic hardening step is to standardize process checks instead of adding more domain-specific facts, so the public DAN Research membrane now also requires `quality_gates` for `time_anchor`, `scope_boundary`, `source_authority`, `numeric_reconciliation`, `claim_object_fit`, and `final_status`; the durable review gate blocks missing/failed gates and non-pass states in critical gates, catching historical-vs-current drift, overbroad exclusivity/absence claims, weak existence/status sources, unreconciled numeric conflicts, and thesis-object mismatches without changing the universal-agent cell or bounded organ topology
  - one more durable-product lesson from 2026-04-16: provider prompt filtering belongs at the same thin local-runtime/product seam as tool-shape drift and report normalization, so the shared local runtime now detects prompt-level safety/content-filter rejections, retries once with a tool-free neutral fallback contract, and if the provider still refuses returns a blocked structured result instead of crashing the deep-research or local-organism turn
  - one more durable-product lesson from 2026-04-16: the next speed/observability wins also belonged at the runtime seam rather than in the cell substrate, so grounded `web_search` fetches now run concurrently with bounded deadlines and reuse cache/inflight work even for `fetch_content=true`, while the research CLI now emits sparse heartbeat events during long quiet periods so operators can see which worker/phase/query is still active without changing the bounded deep-research organ topology
  - one more durable-product lesson from 2026-04-16: the next aggressive speed win still stayed at the same seam, not in the universal-agent substrate, so deep-research cells now honor wall-clock budgets through the existing packet membrane, wider retrieval pools allow a small number of timed-out reader failures, and unpinned continuation passes automatically narrow their reader width instead of repeating another full broad fan-out
  - one more durable-product lesson from 2026-04-16: generic time awareness also belonged at the product/runtime seam instead of one-off domain gates, so DAN Research now assigns each bounded run a small temporal frame (`current`, `historical_snapshot`, `trend`, `timeless`) before search starts, threads that frame through the task payload plus reader briefs, persists it on the report, and if review still wants another pass after the supervision limit, the artifact now returns explicitly `incomplete` / `blocked` instead of looking closed
  - one more durable-product lesson from 2026-04-16: improving deep research further did not require another substrate guardrail round; the useful next seam was a thin pre-search intention-breaker above the bounded organ, so DAN Research now decomposes each research objective into small subproblems with `why_it_matters` plus `evidence_to_seek`, feeds that plan into refined objectives/evidence summaries/reader briefs, and narrows continuation passes from unresolved prior-report gaps without changing the universal-agent cell or bounded organ topology
  - one more durable-product lesson from 2026-04-17: the honest incomplete-return seam should not also hard-code a stop after two passes, so DAN Research now defaults the durable continuation loop to unbounded follow-up passes and only returns `incomplete` / `blocked` when an explicit operator cap (`--max-supervision-loops`, workspace config, or `DAN_RESEARCH_MAX_SUPERVISION_LOOPS`) is what actually stops the run
  - one more durable-product lesson from 2026-04-17: stronger pre-search decomposition still needed one cleaner unit of parallelism than a flat brief list, so the same intention-breaker seam now also groups related subproblems into explicit workstreams with `goal`, `why_it_matters`, `subproblem_ids`, and `aggregation_hint`, and the research CLI now uses those streams to size auto-width passes and brief readers without changing the bounded deep-research organ topology
  - one more durable-product lesson from 2026-04-17: the retrieval tissue still needed an internal contract smaller than the public report membrane, so tissue members can now carry an `output_contract_override`, deep-research readers now return compact evidence notes instead of the full public report shape, and the shared read-only local runtime now nudges research-note workers to stop searching and finalize once they already have bounded grounding, which keeps successful search/tool rounds from escalating before lead synthesis
  - one more durable-product lesson from 2026-04-17: stronger continuation looping still needed one more control-plane refinement, because some tasks explicitly authorize “mark the remaining fact unverifiable and finish provisionally” after repeated attempts; the research review seam now honors that narrow objective/report pattern instead of reapplying the generic low-confidence continuation rule forever
- Focused validation in the main workspace:
  - `PYTHONPATH=src python -m py_compile src/dan/worker/organ.py src/dan/worker/__init__.py tests/eval/test_bounded_organ_patterns.py`
  - `PYTHONPATH=src pytest -q tests/eval/test_bounded_organ_patterns.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_model.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_research_conversation.py tests/test_cli/test_research.py tests/test_worker/test_reference_organism.py`
  - `PYTHONPATH=src pytest -q tests/test_cli/test_organism.py tests/eval/test_reference_organism_acceptance.py tests/eval/test_bounded_organ_patterns.py`
  - `PYTHONPATH=src pytest -q tests/test_cli/test_code.py tests/test_worker/test_presets.py tests/test_worker/test_local_organism_runtime.py tests/test_worker/test_coding_conversation.py tests/test_worker/test_coding_organism.py`
  - `PYTHONPATH=src python -m py_compile src/dan/worker/organisms/local_runtime.py src/dan/cli/research_product.py tests/test_worker/test_local_organism_runtime.py tests/test_cli/test_research.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_local_organism_runtime.py tests/test_cli/test_research.py tests/test_worker/test_research_conversation.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_reference_organism.py tests/test_cli/test_organism.py`
  - `PYTHONPATH=src python -m py_compile src/dan/worker/organisms/local_runtime.py tests/test_worker/test_local_organism_runtime.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_local_organism_runtime.py`
