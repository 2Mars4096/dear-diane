# 4: Super DAN Human-Assist Verticals

**Status:** in-progress
**Goal:** Keep game, academic, and market work as core Super DAN proof tracks at realistic human-assist targets, with executable gates that distinguish a useful draft/prototype from an unsupported category-leading claim.

## Tasks
- [x] 1. Freeze realistic target levels
  - [x] 1-1. Indie game: a coherent playable vertical slice with deterministic interaction checks and a human playtest handoff
  - [x] 1-2. Academic work: a complete first manuscript draft with claim/source, reproducibility, unresolved-evidence, and human-review layers
  - [x] 1-3. Market work: a falsifiable stock-selection or prediction-market framework with point-in-time inputs, leakage controls, walk-forward testing, costs, paper testing, and human approval
- [x] 2. Preserve strict claims without deferring the domains
  - [x] 2-1. Report functional-prototype and showcase-quality outcomes separately for flagship browser artifacts
  - [x] 2-2. Keep the original 4.0/5 showcase gate fixed instead of lowering it after observing the RTS result
  - [x] 2-3. Add all three human-assist cases to the permanent Super DAN capability matrix
- [x] 3. Harden workspace mutation before widening live work
  - [x] 3-1. Make structured file writes/edits atomic
  - [x] 3-2. Reject direct in-place shell text mutators in the Super DAN runtime
  - [x] 3-3. Roll back failed or catastrophically shrinking shell changes to inspected/named workspace files
- [x] 4. Add domain-specific acceptance
  - [x] 4-1. Indie vertical slice reuses the full browser gameplay gate and exposes prototype vs showcase results
  - [x] 4-2. Academic draft gate: manuscript structure, evidence ledger integrity, citation/result uncertainty, reproducibility packet, and human checklist
  - [x] 4-3. Market framework gate: point-in-time schema, leakage sentinels, walk-forward splits, costs/slippage, failure criteria, paper-trading boundary, and human checklist
  - [x] 4-4. Independent agent review hardened the gates: required artifact paths must match, academic claim/source refs must resolve, market tests must be substantive and execute successfully, the capability score requires the domain gate, and flagship rubric screenshot refs must resolve to real local evidence files
- [x] 5. Close mutation-safety review findings
  - [x] 5-1. Make move rollback exemptions source-path-specific instead of command-global
  - [x] 5-2. Block Git restore/checkout/reset/clean workspace restoration variants
  - [x] 5-3. Preserve normal umask-derived permissions for new atomic files
  - [x] 5-4. Protect short files and recognizable paths embedded in shell scripts from catastrophic content loss
- [ ] 6. Run authenticated live cases
  - [ ] 6-1. Freeze one bounded academic input packet with supplied sources/data and no unsupported journal-readiness claim
  - [ ] 6-2. Freeze one bounded stock or prediction-market input packet using point-in-time data and no live trading authority
  - [ ] 6-3. Obtain explicit provider-disclosure approval for these new prompt/context payloads before outbound execution
  - [ ] 6-4. Run both cases through the same Super DAN Work control plane with one durable mid-run steer
- [ ] 7. Conduct human-assisted evaluation
  - [ ] 7-1. Human playtest records usability, fun/readability, defects, and continue/stop decision
  - [ ] 7-2. Researcher reviews every major claim, citation, method choice, result, and unresolved marker before treating the draft as usable
  - [ ] 7-3. Human reviews data lineage, leakage tests, economic assumptions, stability, and paper results before any market experiment advances

## Decisions
- These are core validation tracks, not separate product modes. They run through the same universal cell, Work workspace, Notes, evidence, steering, and preview surfaces.
- The product claim is human leverage: produce a testable vertical slice, first full draft, or falsifiable strategy framework and make the remaining human decisions explicit.
- “AAA-quality game,” “top-journal paper,” and “profitable strategy” remain prohibited automatic guarantees. They are aspirational external outcomes that require specialist judgment, iteration, and evidence beyond one agent run.
- Passing a functional/prototype gate never silently implies passing the stricter showcase/publication/investment gate.

## Notes
- The 2026-07-25 RTS already passes the functional indie-prototype layer: 30/30 static checks, 26/26 browser interactions, durable steering, and capability delivery. Its 3.60/5 visual score still fails the fixed 4.0/5 showcase layer.
- Existing outbound approval names only the premium-site and RTS payloads. Academic and market live prompts require a new explicit disclosure approval before they can be sent to a provider.
- The 2026-07-28 independent agent review is complete and is recorded as engineering evidence only. It does not substitute for the qualified human decisions in task 7.
