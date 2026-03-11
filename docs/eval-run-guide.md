# Phase 33 Eval Run Guide

How to run the workflow generation quality evaluation harness. Requires a running DAN server (`dan-up`).

## Prerequisites

- Server running: `dan up` or `dan-serve`
- Default base URL: `http://localhost:8080` (override with `--base-url`)

## Commands

### Full battery (all prompts)

```bash
PYTHONPATH=src python -m tests.eval --lane build --base-url http://localhost:8080
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

### With durability checks (D1–D4)

```bash
PYTHONPATH=src python -m tests.eval --lane build --durability
```

### Report only (from existing JSONL)

```bash
PYTHONPATH=src python -m tests.eval --report tests/eval/results/YYYY-MM-DD_HHMMSS_run.jsonl
```

## Output

- JSONL: `tests/eval/results/{timestamp}_run.jsonl`
- Report JSON: `tests/eval/results/{timestamp}_run.report.json`
- Graphs (if stored): `tests/eval/results/{timestamp}_run_graphs/`

## Plans

- [33-3](plans/33-3-small-task-battery.md) — Small task battery
- [33-4](plans/33-4-complex-workflow-battery.md) — Complex workflow battery
- [33-5](plans/33-5-analysis-and-fixes.md) — Analysis & fixes
