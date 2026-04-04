# 48-5: Generated Workflow First-Run Policy

**Parent:** [48-workflow-continuity-and-control-plane-hardening](48-workflow-continuity-and-control-plane-hardening.md)
**Status:** not-started
**Goal:** Keep generated workflows safe on first run without letting heuristic lint or policy gates make the product feel arbitrarily brittle.

## Dependencies
- **48-1** for stable workflow revision/fingerprint: first-run detection depends on knowing whether a workflow revision is genuinely new vs a rerun of an existing revision.
- **48-2** for continuation context: the follow-up lane carries revision state that execution uses to decide whether leniency applies.
- Optional cross-link to **48-3**: if a mutation significantly restructures a workflow, the resulting revision may re-qualify as a "first generated run" -- coordinate the definition of "structurally significant generated edit" (task 1-3).
- Builds on [47-agent-output-linter](47-agent-output-linter.md) foundations (tiers, rule protocol, severity, `LintConfig`); does not change the linter core design.

## Tasks
- [ ] 1. Define first-run policy tiers
  - [ ] 1-1. Keep structural graph validity and hard schema-safety rules blocking.
  - [ ] 1-2. Define which semantic/intent heuristics should start as warning-only on a workflow's first generated run. Map to existing linter tiers: Tier 1 (structural) stays blocking; Tier 2 (semantic similarity, contradiction) and Tier 3 (intent/judge) rules with heuristic-only autogen severity (`warning`) are candidates for first-run leniency. Inventory the current rule IDs and classify each as blocking/lenient.
  - [ ] 1-3. Define exactly what counts as a generated first run: new workflow creation, save-as forks, and structurally significant generated edits versus ordinary reruns.
  - [ ] 1-4. Define graduation criteria so first-run leniency expires after a clear event instead of lingering indefinitely. Candidate triggers (pick one default, document alternatives): (a) first successful run completion, (b) explicit user acknowledgement/save after reviewing warnings, (c) next revision bump after the first run, (d) fixed time window (e.g. 24h). If the first run fails for a non-lint reason (timeout, provider error), leniency persists until a successful run or explicit user action.
- [ ] 2. Wire first-run policy into execution
  - [ ] 2-1. Detect generated-first-run state cleanly.
  - [ ] 2-2. Downgrade or defer heuristic gates only where the policy explicitly allows it.
  - [ ] 2-3. End leniency when the workflow revision graduates and restore the normal heuristic policy on later runs of that revision.
- [ ] 3. Preserve observability
  - [ ] 3-1. Emit telemetry when a heuristic gate was softened because the run is a first generated run.
  - [ ] 3-2. Keep the diagnostics visible so users can still see what needs hardening later. Surface in: UI diagnostics panel, run logs, and worker reply context. Include `graph_id` / revision in telemetry events for correlation.
- [ ] 4. Add regressions
  - [ ] 4-1. Test first-run success under warning-only heuristic conditions.
  - [ ] 4-2. Test that structural failures still block immediately.
  - [ ] 4-3. Test that after graduation, heuristic gates revert to normal enforcement on subsequent runs.
  - [ ] 4-4. Test telemetry emission when a heuristic gate is softened on first run.

## Decisions
- Structural correctness is non-negotiable.
- Heuristic semantic/intent checks should help users converge on a stable workflow, not stop them from ever reaching a first clean execution.
- "First run" should be scoped to a workflow revision or materially regenerated structure, not to a user forever and not to every rerun of the same stable workflow.
- "Warning-only" (not "canary"): softened heuristics log a warning-severity lint result visible in diagnostics and telemetry, but do not block execution. This is not a sampled/canary rollout -- the policy applies deterministically to all generated first runs. The feature is gated by an env flag (`DAN_FIRST_RUN_POLICY=enabled`) for rollout control.
- Scope is generated workflows only; hand-authored and template-imported workflows follow normal lint policy from the start.

## Notes
- This sub-plan builds on the [47-agent-output-linter](47-agent-output-linter.md) foundations rather than changing their core design.
- Fork/save-as: a fork starts a new first-run window; it does not inherit the parent's graduation state.
