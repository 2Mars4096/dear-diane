# 1-5: SWE-bench Public Track

**Parent:** [1-dan-code-live-capability-battery](1-dan-code-live-capability-battery.md)
**Status:** in-progress
**Goal:** Add a boringly rerunnable public-code benchmark path for DAN Code so SWE-bench-style issue-resolution runs can be executed from real repo checkouts with scorer-compatible patch artifacts.

## Tasks
- [x] 1. Decide the benchmark track placement
  - [x] 1-1. Keep SWE-bench work under `docs/live-test-plans/` as a DAN Code live-evidence track rather than mixing it into workflow-generation eval plans
  - [x] 1-2. Treat the first target as public SWE-bench instances (`Verified`, `Lite`, and `SWE-bench Pro` public split), not held-out/commercial sets
- [x] 2. Patch `dan code` for single-instance benchmark runs
  - [x] 2-1. Accept a SWE-bench-compatible instance file and synthesize the coding objective from it when no free-form objective is supplied
  - [x] 2-2. Carry instance metadata into run artifacts so `instance_id`, repo, tests, and supporting notes are inspectable after the run
  - [x] 2-3. Emit scorer-compatible prediction artifacts from the workspace git diff after the run
- [x] 3. Document the operator flow
  - [x] 3-1. Show how to prepare a repo checkout at the benchmark base commit before invoking `dan code`
  - [x] 3-2. Show how to append predictions into a JSONL file for the official SWE-bench harness
- [x] 4. Decide what graduates into automation
  - [x] 4-1. Keep heavyweight public-benchmark runs operator-invoked at first
  - [x] 4-2. Extract a thin operator-driven loop runner under `tests/eval/` that can prepare a public repo checkout and invoke the single-instance `dan code` path
- [ ] 5. Close the first live-run control-loop gap
  - [ ] 5-1. Treat a validator-passing benchmark candidate as exportable even when the bounded run had to synthesize the candidate payload from tool evidence
  - [ ] 5-2. Make the outer DAN Code review path emit final `report.json` / `predictions.jsonl` instead of spinning into another turn after the workspace already contains the accepted fix

## Decisions
- Use the live-test track because this work is still about trustworthy DAN Code capability evidence, even though the downstream scorer is a public benchmark harness.
- Aim first at the public, contamination-exposed splits that are actually runnable from a local checkout; do not plan around the non-public SWE-bench Pro partitions.
- Patch `dan code` itself instead of building a separate one-off wrapper first, so the product shell owns its own benchmark artifacts and operator workflow.
- The first automation slice can live under `tests/eval/` as an operator-invoked adapter that prepares repo worktrees and shells out to the real `dan code` product path; full benchmark-set orchestration and scorer automation can stay separate.

## Notes
- SWE-bench scoring expects a prediction object with `instance_id`, `model_name_or_path`, and `model_patch`; DAN Code should be able to emit that directly from the workspace git diff after a run.
- The benchmark harness already evaluates patches inside Docker, so DAN Code does not need to reimplement scoring. The product-side job is to solve one checked-out instance cleanly and emit the patch artifact.
- Kimi-backed runs should prefer the current DAN Code compatibility defaults (`thinking-mode disabled` when needed for Kimi/OpenAI-compatible tool use), but the benchmark path should stay model-agnostic.
- `tests/eval/swebench_runner.py` now provides the first thin public runner: it can load one instance from local JSON/JSONL or a Hugging Face parquet split, clone/cache the benchmark repo, create a detached worktree at `base_commit`, and invoke `python -m dan.cli.code` with the benchmark flags wired through.
- The first live smoke run used `princeton-nlp/SWE-bench_Lite` instance `marshmallow-code__marshmallow-1359` with `kimi-k2.5`. DAN Code materialized the correct source diff in `src/marshmallow/fields.py`, then a second turn added `test_datetime_container_fix.py` and executed it successfully, but the outer product loop still did not emit `report.json` / `predictions.jsonl` before manual stop because it continued into another turn after a validator-passing result.
