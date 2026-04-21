# 54-16: Research Task-Lane Override For PDF Latency A/B

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Add a DAN Research CLI override for the fast/deep task lane so bounded PDF review runs can be compared fairly with and without the new planner/review bypass.

## Tasks
- [x] 1. Add an explicit DAN Research lane override
  - [x] 1-1. Accept `--task-lane auto|fast|deep` at the CLI layer
  - [x] 1-2. Apply the override after classification so telemetry still records the final lane cleanly
- [x] 2. Improve observability for latency experiments
  - [x] 2-1. Surface the selected task lane in `--show-config`
  - [x] 2-2. Include lane and override metadata in run-start telemetry
- [x] 3. Add focused regression coverage
  - [x] 3-1. Prove a review-style document prompt still uses planner/review calls when `--task-lane deep` is forced
  - [x] 3-2. Cover the parser/config defaults for the new option
- [x] 4. Update docs and validation notes
  - [x] 4-1. Document the override and the `pdf_read` tool requirement for local PDF review
  - [x] 4-2. Revalidate the focused DAN Research CLI basket

## Decisions
- Keep the first override CLI-only and research-only so PDF latency A/B can ship without widening the scope to every product shell.
- Apply overrides after heuristic classification so the same seam can still emit the original classifier-compatible telemetry shape.
- Treat `fast` as “use deterministic planner/review fallbacks” and `deep` as “keep the full control shell,” without changing bounded-worker budgets by itself.

## Notes
- This closes the measurement gap from the initial fast-lane rollout: `--depth deep` alone is not a clean A/B because it also changes tool/runtime budgets.
- For local PDF runs, operators still need `--tool pdf_read` because the default DAN Research tool basket does not include that tool.
