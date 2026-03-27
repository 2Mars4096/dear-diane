# Benchmark-Prep Frozen Inputs

These fixtures are the first dedicated frozen local inputs for the Phase 33
long-running benchmark-prep slice.

They are intentionally small so they are practical for local iteration, but
they preserve the shape of the three planned workload classes:

- `lr1_corpus/` — local literature-review corpus
- `lr2_data/` — local CSV package plus reference summary
- `lr3_repo/` — local code-migration repo fixture with tests

The prompt pack that targets these fixtures lives at
`tests/eval/benchmark_long_running_prompts.json`.
