# 54-20: Super DAN 100-Cell Showcase

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Add a first deterministic Super DAN organism showcase that proves organized 100-cell coordination before any live 100-agent execution.

## Tasks
- [x] 1. Define the first showcase boundary
  - [x] 1-1. Keep the raw universal worker core unchanged
  - [x] 1-2. Put organism-specific behavior in a separate deterministic showcase layer
  - [x] 1-3. Label outputs as deterministic demo artifacts rather than live factual audits
- [x] 2. Build the 100-cell organism substrate
  - [x] 2-1. Add fixed 100-cell organ distribution: brain, scout, claim, immune, memory, experiment, synthesis
  - [x] 2-2. Add board signals for heartbeat, state delta, resource request, reallocation, and completion
  - [x] 2-3. Add claim graph, evidence refs, activity waves, and reallocation decisions
  - [x] 2-4. Enforce active-cell wave caps so 100 logical cells do not imply 100 live concurrent calls
- [x] 3. Expose the prototype
  - [x] 3-1. Add `dan-super-organism` / `dansuperorganism`
  - [x] 3-2. Add unified `dan super-organism`
  - [x] 3-3. Support JSON and text reports plus optional JSON output path
- [x] 4. Add focused regression coverage
  - [x] 4-1. Cover default/scaled distributions and active-cell cap behavior
  - [x] 4-2. Cover claim graph, signal counts, reallocations, and CLI output
- [x] 5. Fix scenario routing after first operator smoke
  - [x] 5-1. Route product/website build prompts to a delivery-plan organism instead of truth-audit claims
  - [x] 5-2. Add `--scenario auto|truth-audit|website-build`
  - [x] 5-3. Cover website-build reports and CLI text output
- [x] 6. Make the no-argument showcase match the sales demo
  - [x] 6-1. Default `dan super-organism` to the universal-agent objective contract
  - [x] 6-2. Preserve explicit truth-audit mode through `--scenario truth-audit`
  - [x] 6-3. Cover default CLI and worker behavior
- [x] 7. Align the top-level contract with DAN Code / DAN Research
  - [x] 7-1. Add `universal_agent` as the main scenario
  - [x] 7-2. Treat the prompt as an objective before selecting specialist contracts
  - [x] 7-3. Preserve `truth-audit` and `website-build` as explicit demos

## Decisions
- The first slice is deterministic and logical. It proves organization, not live external truth.
- The raw `WorkerCoreExecutor` / `WorkerDefinition` primitive stays untouched.
- The next live version should route only selected cells through real DAN Code / DAN Research / WorkerCore lanes behind a scheduler cap.
- Scenario-specific behavior belongs in the showcase layer, not in the raw universal worker core.
- The main command should be objective-first, like DAN Code and DAN Research. Hardcoded scenarios are examples, not the universal-agent path.

## Notes
- The first use case is a truth-organism style credibility audit: scouts gather, claim cells atomize, memory clusters, immune cells challenge, experiment cells probe, brain cells reallocate, and synthesis cells produce a traceable verdict.
- A website-build prompt now produces delivery nodes for narrative, visual direction, section architecture, motion, implementation handoff, copy/proof input, and QA instead of nonsensical benchmark/adoption claims.
- `dan super-organism "<request>"` now defaults to a universal-agent objective contract; use `--scenario truth-audit` or `--scenario website-build` for the older demos.
- The report intentionally carries `mode="deterministic_demo"` plus a caveat so showcase outputs are not mistaken for sourced live evaluations.
