# Phase 33 Eval Run Guide

How to run the workflow generation quality evaluation harness. Requires a running DAN server (`dan-up` or `dan-serve`) plus live provider credentials/network for real LLM-backed workflow generation.

## Prerequisites

- Server running: `dan up` or `dan-serve`
- Default base URL: `http://localhost:8000` (override with `--base-url`)
- Live model access configured in `.env` / environment variables if you want real provider-backed results

## Commands

### Workflow smoke battery (recommended first live check)

```bash
# Terminal 1
PYTHONPATH=src python -m dan.server --no-reload

# Terminal 2
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
