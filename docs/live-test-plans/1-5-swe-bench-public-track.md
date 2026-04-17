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
- [x] 5. Close the first live-run control-loop gap
  - [x] 5-1. Treat a validator-passing benchmark candidate as exportable even when the bounded run had to synthesize the candidate payload from tool evidence
  - [x] 5-2. Make the outer DAN Code review path emit final `report.json` / `predictions.jsonl` instead of spinning into another turn after the workspace already contains the accepted fix
- [x] 6. Score a small official SWE-bench Lite batch
  - [x] 6-1. Install the official `swebench==4.1.0` harness in an isolated `/tmp/swebench-venv`
  - [x] 6-2. Run the official Docker-backed harness against the exported `kimi-k2.5` predictions for `marshmallow-code__marshmallow-1359` and `marshmallow-code__marshmallow-1343`
  - [x] 6-3. Record the official run-level and per-instance reports for follow-up comparison
- [ ] 7. Run the full public SWE-bench Lite test split
  - [x] 7-1. Add a resumable batch runner that loads the full split, skips completed prediction rows, and maintains one scorer-compatible combined `predictions.jsonl`
  - [x] 7-2. Add a per-instance wall-clock timeout so one long DAN Code run cannot block the full batch indefinitely
  - [x] 7-3. Launch the full `princeton-nlp/SWE-bench_Lite` `test` split with `kimi-k2.5`
  - [ ] 7-4. Let the 300-instance prediction batch finish
  - [ ] 7-5. Score the completed full-set predictions with the official Docker-backed harness

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
- A later rerun of that same Lite/Kimi instance completed end-to-end and emitted the top-level runner artifacts (`report.json`, `predictions.jsonl`, `stdout.log`, `stderr.log`) plus the per-turn SWE-bench artifacts (`swebench.patch`, `swebench-prediction.json`). The exported patch was the expected one-line `schema.opts` -> `self.root.opts` fix in `src/marshmallow/fields.py`.
- The product-side hardening needed for that clean rerun was broader than the original review seam alone: benchmark-mode review normalization now forces export on completed material-output candidates, the git tools and runner prefer `/usr/bin/git` on this machine, sandboxed `shell_command` now honors `working_directory` and injects workspace-local `PYTHONPATH`, and `file_edit` now tolerates common LLM replace payloads (`old_string`/`new_string`, multiline replace inference, and interior-line newline preservation).
- A follow-on Lite rerun on `marshmallow-code__marshmallow-1343` after the anti-pattern patch stayed on structured line-based `file_read` / `file_edit` calls and avoided the earlier grep/head-style localization drift. That run still entered a repair loop after a too-small `replace` edit and was stopped without export, which narrows the next benchmark boundary to safer edit-shape selection rather than pattern matching.
- The next 1343 probes exposed a separate control-plane seam: benchmark-mode planner bypass inside `coding_conversation.py` was not enough by itself because `code.py` built the project-planner context without forwarding `benchmark_context`, so the planner still saw `facts.benchmark_mode=False` and decomposed the run into a reproduction-only milestone. That threading bug is now fixed, and the latest live rerun (`tests/eval/results/20260416_171457_marshmallow-code__marshmallow-1343/`) starts with one bounded milestone whose objective is the full `schema.py` fix, then proceeds directly into `schema.py` reads instead of a standalone reproduction-script milestone. The run was stopped after confirming the corrected routing so it did not keep running in the background.
- The later 1343 rerun under `tests/eval/results/20260417_020100_marshmallow-code__marshmallow-1343/` completed end-to-end and exported the expected benchmark artifacts after three narrower runtime/tool fixes: repo-relative git-tool paths are now normalized to the benchmark workspace, `file_edit` can reinterpret a common previous-line-plus-target-prefix replace payload as an insert-style edit, and sandboxed `shell_command` now seeds standard system-bin `PATH` entries so simple commands like `cat` work inside the bounded workspace.
- That 1343 run produced a clean scorer-compatible patch in `src/marshmallow/schema.py` adding the early `if data is None: return` guard to `_invoke_field_validators`, and the validator scored the materialized candidate at `0.9875` before the outer review/export seam finalized it into `report.json` and `predictions.jsonl`.
- Focused regression coverage now locks those benchmark hardenings in `tests/test_worker/test_local_organism_runtime.py`, `tests/test_engine/test_tool_executor_integration.py`, and `tests/test_tools/test_shell_command.py`, so the live fixes are backed by repo-local tests instead of only by one-off benchmark evidence.
- A later rerun of `marshmallow-code__marshmallow-1359` exposed another narrow portability seam in validator mode: the model sometimes assumes the checked-out repo lives at `/workspace/...`, as in Dockerized benchmark harnesses. `src/dan/tools/_workspace.py` now maps `/workspace` and `/workspace/...` onto the actual bounded workspace root, and a live validator trace on `tests/eval/results/20260417_021150_marshmallow-code__marshmallow-1359/` confirmed that `list_directory(path="/workspace")` now succeeds instead of failing over to a second guess.
- The official SWE-bench harness smoke score on 2026-04-17 used the two clean DAN Code prediction exports from `tests/eval/results/20260416_164247_marshmallow-code__marshmallow-1359/predictions.jsonl` and `tests/eval/results/20260417_020100_marshmallow-code__marshmallow-1343/predictions.jsonl`, evaluated them with Docker/Colima through `swebench==4.1.0`, and produced `2/2` resolved with `0` errors. The run-level report is `kimi-k2.5.dan_kimi_lite_two.json`, with per-instance official logs under `logs/run_evaluation/dan_kimi_lite_two/kimi-k2.5/`.
- `tests/eval/swebench_batch_runner.py` now wraps the single-instance runner for resumable batch execution: it loads all instances from a local file or Hugging Face split, appends only non-empty unique prediction rows into one combined JSONL, records one `records.jsonl` row per instance attempt, and skips completed ids on rerun. The full Lite `test` split has `300` instances; the earlier smoke-scored `dev` instances are not part of that split.
- The first full Lite `test` batch was launched on 2026-04-17 under `tests/eval/results/swebench_lite_full_test_kimi_20260417/` with `kimi-k2.5`, `thinking-mode disabled`, per-completion timeout `90s`, and per-instance run timeout `900s`. An initial unguarded attempt on `astropy__astropy-12907` showed why the wall-clock guard is needed; the guarded restart exported a prediction for `astropy__astropy-12907` after about 718 seconds and continued to `astropy__astropy-14182`.
- Current full Lite `test` status from the active batch: `7` instance attempts have completed, `5` emitted prediction rows, and `2` timed out without a prediction (`astropy__astropy-14182`, `django__django-10914`). The latest official partial score on the first four exported predictions is `3/4` resolved with `0` scorer errors: resolved `astropy__astropy-12907`, `astropy__astropy-14995`, and `astropy__astropy-6938`; unresolved `astropy__astropy-14365`. The partial scorer report is `tests/eval/results/swebench_lite_full_test_kimi_20260417/kimi-k2.5.dan_kimi_lite_full_test_partial4_20260417.json`.
