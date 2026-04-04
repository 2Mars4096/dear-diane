# Phase 33 Eval Run Guide

How to run the workflow generation quality evaluation harness. Requires a running DAN server (`dan-up` or `dan-serve`) plus live provider credentials/network for real LLM-backed workflow generation.

## Prerequisites

- Server running: `dan up` or `dan-serve`
- Default base URL: `http://localhost:8000` (override with `--base-url`)
- Live model access configured in `.env` / environment variables if you want real provider-backed results
- Benchmark-grade runs should use the server path, not local fallback behavior

## Benchmark-grade bootstrap

Verify the backend from the same shell namespace you will use for evals:

```bash
# Terminal 1
PYTHONPATH=src python -m dan.server --no-reload

# Terminal 2
curl -f http://127.0.0.1:8000/health
env | rg '^DAN_(OPENAI|ANTHROPIC|GOOGLE|LLM)'
```

`/health` should report the expected provider readiness. If the backend is missing required provider config, fix that first; do not treat fallback behavior as a valid benchmark run.

The `PYTHONPATH=src` prefix is a repo-local bootstrap bridge for this checkout because the package is not installed into the active environment. It is not part of the workflow-loader contract; once DAN is installed normally, the prefix should disappear from benchmark runbooks.

## Commands

### Workflow result similarity benchmark (plan 45-4)

```bash
PYTHONPATH=src:. python -m tests.eval.workflow_result_similarity_benchmark \
  --env-file .env \
  --judge off
```

This harness compares:
- a hand-authored reference workflow built in-process with canonical `Worker` nodes
- a DAN-generated workflow created through build mode on the running server
- the final outputs of both workflows on the same long-form task input

The current fixture catalog contains 10 long-form tasks:
- equity daily brief
- incident postmortem
- product launch brief
- vendor risk review
- feedback synthesis
- literature review digest
- operations weekly brief
- policy compliance assessment
- meeting-to-project-plan
- hiring interview packet

Each case writes:
- reference graph summary and validation
- generated graph summary and validation
- execution status and primary outputs for both workflows
- deterministic similarity metrics (`section_coverage`, `keyword_overlap`)
- optional LLM-judge scores when `--judge auto|required` is enabled

Reports are written under `tests/eval/results/` as `*_workflow_result_similarity_benchmark.json`.

Use `--case <fixture_id>` to run a single fixture while iterating:

```bash
PYTHONPATH=src:. python -m tests.eval.workflow_result_similarity_benchmark \
  --case meeting_to_project_plan \
  --env-file .env \
  --judge off
```

### Worker / Linter rollout benchmark (plans 46/47)

```bash
PYTHONPATH=src:. python -m tests.eval.worker_lint_benchmark --repeats 5
```

This in-process harness writes a JSON report under `tests/eval/results/` and covers:
- Worker parity for a linear code→tool chain
- retained specialized `for_each` parity with Workerized body compute
- retained specialized `goal_loop` parity with Workerized body compute
- retained specialized `parallel_subagents` parity with Workerized branch compute
- retained specialized `agent_team` parity with Workerized member compute
- retained specialized `orchestrator` parity for both static-fanout and deterministic LLM-driven coordinator paths
- blocking lint failure
- structural autofix/truncate
- provider-less semantic-tier graceful skip
- deterministic contradiction blocking for obvious same-subject semantic conflicts
- non-blocking Tier 3 partial-warning handling with completeness follow-up
- retry-with-feedback carrying missing intent requirements plus a preview of the rejected output

Deterministic mode is the quickest repeatable 46/47 rollout check. For live Tier 2/3 coverage from the worktree, load an env file and opt into provider-backed cases:

```bash
DAN_ENABLE_LOCAL_EMBEDDINGS=1 \
DAN_DEFAULT_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2 \
DAN_LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2 \
PYTHONPATH=src:. python -m tests.eval.worker_lint_benchmark \
  --env-file /Volumes/data/Dropbox/Projects/deep-agent-network/.env \
  --live-provider auto \
  --repeats 1
```

Live mode adds:
- semantic pass/fail with a real embedding backend
- intent pass/fail with a real judge backend

`--live-provider auto` records explicit skips when backends are not configured. `--live-provider required` turns those skips into a failing benchmark.

### Worker dispatch-overhead benchmark (plan 46-5 gate)

```bash
PYTHONPATH=src python -m tests.eval.worker_dispatch_benchmark --warmup 20 --repeats 200 --strict
```

This in-process harness measures `WorkerExecutor` dispatch overhead against the equivalent legacy compute nodes for:
- `code_only`
- `tool_only`
- `llm_only`
- `code -> tool -> llm` chain
- `body_graph` input/output mapping
- sub-worker `last_write_wins` merge

The harness alternates legacy and Worker runs in an interleaved pairwise order to reduce timing drift from two separate measurement phases inside one Python process. It writes a JSON report under `tests/eval/results/` and fails in `--strict` mode when the Worker median runtime exceeds the configured `--max-overhead-ratio` (default `1.25`) for any case.

### Semantic Tier-2 latency benchmark (plan 47-3 gate)

```bash
PYTHONPATH=src python -m tests.eval.semantic_lint_benchmark --repeats 500 --strict
```

This in-process harness measures Tier 2 semantic lint latency with a mock embedder and fails in `--strict` mode if the median runtime exceeds the configured `--max-median-ms` (default `10.0`).

### Structural Tier-1 latency benchmark (plan 47-2 gate)

```bash
PYTHONPATH=src python -m tests.eval.structural_lint_benchmark --repeats 1000 --strict
```

This in-process harness measures Tier 1 structural lint latency on a realistic structured agent payload and fails in `--strict` mode if the median runtime exceeds the configured `--max-median-ms` (default `1.0`).

### Workflow smoke battery (recommended first live check)

```bash
PYTHONPATH=src python -m tests.eval --smoke-workflows --execute --lane build --delay 0.5
```

This runs the small live workflow battery in [`tests/eval/workflow_smoke_prompts.json`](../tests/eval/workflow_smoke_prompts.json): add-two-numbers, slugify, conditional, loop, and `for_each`.

### Full battery (all prompts)

```bash
PYTHONPATH=src python -m tests.eval --lane build
```

### Pilot subset (~10 prompts)

```bash
PYTHONPATH=src python -m tests.eval --pilot --lane build
```

### Small task battery (T1–T3, T2R, T5)

```bash
PYTHONPATH=src python -m tests.eval --tier T1 --tier T2 --tier T2R --tier T3 --tier T5 --lane build
```

### Complex battery (T4 + multi-turn m1/m2/m3)

```bash
PYTHONPATH=src python -m tests.eval --complex --lane build
```

### With execution testing

```bash
PYTHONPATH=src python -m tests.eval --tier T4 --execute --lane build
```

### Single ad-hoc messy user prompt

```bash
PYTHONPATH=src python -m tests.eval --prompt "can you make a tiny workflow that adds 3 and 4 and run it" --execute --lane build
```

### Custom prompt file

```bash
PYTHONPATH=src python -m tests.eval --prompts-file tests/eval/workflow_smoke_prompts.json --execute --lane build
```

### With durability checks (D1–D4)

```bash
PYTHONPATH=src python -m tests.eval --lane build --durability
```

### Report only (from existing JSONL)

```bash
PYTHONPATH=src python -m tests.eval --report tests/eval/results/YYYY-MM-DD_HHMMSS_run.jsonl
```

### Execution path (plan 32-7)

```bash
# Force inline (direct build) path — start server with DAN_DIRECT_BUILD=only
PYTHONPATH=src python -m tests.eval --execution-path inline --lane build

# Force codegen path — start server with DAN_DIRECT_BUILD=off
PYTHONPATH=src python -m tests.eval --execution-path codegen --lane build

# Auto (default) — server chooses based on DAN_DIRECT_BUILD
PYTHONPATH=src python -m tests.eval --execution-path auto --lane build
```

The flag sets `DAN_EVAL_EXECUTION_PATH` and stores `execution_path_requested` in records. For the server to honor it, start the server with matching `DAN_DIRECT_BUILD` (e.g. `DAN_DIRECT_BUILD=off` for codegen).

### Overnight full run (all prompts + execution + durability + 3 runs for flakiness)

```bash
PYTHONPATH=src python -m tests.eval --lane build --execute --durability --runs 3 2>&1 | tee tests/eval/results/overnight.log
```

This runs every prompt 3 times, attempts execution on valid graphs, and runs durability checks. Takes 30-60 minutes depending on LLM API speed.

### Practical real-world tasks only

```bash
PYTHONPATH=src python -m tests.eval --lane build --tag practical
```

### Filter by tag (repeatable)

```bash
PYTHONPATH=src python -m tests.eval --lane build --tag multi_turn --tag practical
```

## Output

- JSONL: `tests/eval/results/{timestamp}_run.jsonl`
- Report JSON: `tests/eval/results/{timestamp}_run.report.json`
- Graphs (if stored): `tests/eval/results/{timestamp}_run_graphs/`

## Plans

- [33-3](plans/33-3-small-task-battery.md) — Small task battery
- [33-4](plans/33-4-complex-workflow-battery.md) — Complex workflow battery
- [33-5](plans/33-5-analysis-and-fixes.md) — Analysis & fixes
