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

## Built-in same-model pilot

```bash
PYTHONPATH=src:. python -m tests.eval.run_builtin_comparison --output /tmp/diane-comparison-UNIQUE
# Add --execute after checking the printed model/provider and limits.
```

Uses the configured `DAN_LLM_MODEL` (or `--model`) and provider credentials without printing secrets. Four synthetic fixtures run sequentially in fresh workspaces, with rotated arm order. `--cases`, `--arms`, and `--timeout` permit bounded pilots. Output directories must not exist.

- `direct`: plain model/tool loop using Diane's local tools.
- `current`: production generic built-in pipeline under the shared pilot tools and provider limits.
- `selective`: keeps request interpretation; skips optional planning and uses public deterministic validation for note/repair. Research/project keep the current pipeline.
- `manifest.json`, per-arm `calls.jsonl`, `events.jsonl` (pipeline), `result.json`, and aggregate `results.json` preserve evidence. Runtime success and external acceptance are separate.
- Existing capability scores are secondary: these frozen fixture variants differ from the original benchmark prompts/output inventories. Use external acceptance and measured usage/latency for arm comparisons. Research checks are mechanical; inspect the actual report before judging semantic quality.
- One trial per case cannot establish a general ranking; no automatic human-intervention or autonomous-recovery success claim is inferred. No production routing is modified.
