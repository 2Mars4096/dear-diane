# Evaluation Run Guide

## Deterministic suites

```bash
pytest -q tests/eval/test_super_diane_capability_benchmark.py
pytest -q tests/eval/test_super_diane_flagship_acceptance.py
pytest -q tests/eval/test_super_diane_human_assist_acceptance.py
```

Real-browser and live-provider checks are opt-in and must use disposable workspaces unless the operator explicitly authorizes a repository workspace.

## Score an existing trace

```bash
PYTHONPATH=src:. python -m tests.eval.super_diane_capability_benchmark \
  /absolute/workspace/.dan-super/runs/turn-XX/events.jsonl \
  --case-id short-note-create
```

## Flagship runner

Start with the runner's dry-run/help output and review its provider-disclosure and workspace arguments before enabling a live call:

```bash
PYTHONPATH=src:. python tests/eval/run_super_diane_flagship.py --help
```

Acceptance requires external artifact evidence; a model completion event alone cannot pass a case.
