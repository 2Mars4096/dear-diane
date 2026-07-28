# 3: Super DAN Flagship Acceptance

**Status:** completed
**Goal:** Prove that the Super DAN universal cell can build, accept mid-run direction, validate, repair, and deliver two demanding browser artifacts through the durable Work control plane.

## Tasks
- [x] 1. Freeze two original flagship cases
  - [x] 1-1. Premium product-launch site with top-tier craft, responsive behavior, accessibility, and CTA instrumentation
  - [x] 1-2. Playable browser RTS with resources, base growth, unit production, combat, restart, and deterministic state instrumentation
  - [x] 1-3. Keep protected brands and assets out of the delivered artifacts
- [x] 2. Make live execution reproducible
  - [x] 2-1. Add a dry-run-first local control-plane runner
  - [x] 2-2. Require an explicit provider-disclosure acknowledgement before execution
  - [x] 2-3. Inject the frozen steering message after the first persisted artifact change
  - [x] 2-4. Export durable Agent events for steering and completion evidence
  - [x] 2-5. Require a concrete mutated-path reference and a non-terminal event snapshot before injection
- [x] 3. Add artifact acceptance gates
  - [x] 3-1. Require exact files, semantic/responsive/accessibility structure, local-only assets, and clean originality checks
  - [x] 3-2. Run desktop and phone Chromium checks for overflow, console/page errors, screenshots, and visible interaction changes
  - [x] 3-3. Exercise the full RTS start/build/train/attack/restart state loop
  - [x] 3-4. Require an independent six-dimension visual rubric with desktop and phone evidence
  - [x] 3-5. Prove steering was queued after material work, injected before terminal settlement, and acknowledged after model delivery
- [x] 4. Validate the harness offline
  - [x] 4-1. Pass deterministic unit, scorer, dry-run, schema, compile, and diff checks
  - [x] 4-2. Pass both artifact contracts in real headless Chrome at desktop and phone sizes
  - [x] 4-3. Keep the real-browser pytest opt-in so default CI remains deterministic
  - [x] 4-4. Pass the unified CLI gate from artifact files, rubric JSON, and exported Agent JSONL through one authoritative report
- [x] 5. Run the authenticated flagship cases
  - [x] 5-1. Obtain explicit approval for the disclosed Moonshot provider payload scope
  - [x] 5-2. Run the premium-site case in a disposable isolated workspace
  - [x] 5-3. Run the browser-RTS case in a disposable isolated workspace
  - [x] 5-4. Run independent screenshot review, repair each rejection once or more, and freeze the honest final evidence
  - [x] 5-5. Score both event logs for delivery, time, tokens, validation, artifact acceptance, and steering

## Decisions
- A run cannot pass from a completion event or self-reported validation alone. Static, real-browser, independent-rubric, and steering-trace sections must all pass.
- The product site and RTS are capability probes, not new product modes. Both run through the same Super DAN universal-cell Work control plane.
- Live execution remains opt-in and loopback-controlled. The runner refuses provider execution without the explicit disclosure acknowledgement flag.
- Browser artifacts must be original. Quality references set a craft bar; they do not authorize copying brand language, assets, characters, maps, or UI.

## Notes
- Frozen cases and artifact gate: `tests/eval/super_dan_flagship_acceptance.py`.
- Dry-run-first live runner: `tests/eval/run_super_dan_flagship.py`.
- Focused regression suite: `tests/eval/test_super_dan_flagship_acceptance.py`.
- Offline result on 2026-07-24: 13 focused flagship tests passed with 3 opt-in browser tests skipped; the flagship-plus-scorer slice passes 24 deterministic tests. The final browser audit passed all three opt-in cases—including the unified CLI gate—in 9.24 seconds. The broader flagship/scorer/Chat V2 control-plane slice passes 94 tests with 3 browser tests skipped.
- The first Chromium attempt rejected both reference artifacts for a favicon 404. The contract now requires a local or data-URL favicon; the clean rerun passed.
- The event-mapping audit found that completed file mutations and shell workspace diffs were previously normalized as generic tool activity. They now emit deduplicated artifact refs, and the runner refuses to inject if a terminal event is already present in the same poll.
- The older capability scorer now understands exported Chat V2 Agent events (`type`, direct artifact refs, token deltas, and timestamps/validation nested in raw payloads), so the external acceptance report can enforce delivery/time/token scoring instead of producing a separate advisory score.
- Authenticated result on 2026-07-25: the premium-site case passed the unified gate after independent repairs. Static, desktop/phone browser, steering, and capability sections all passed; the independent visual rubric averaged 4.15/5. The initial live trace completed in 1,013 seconds and used 676,526 tokens, producing a capability score of 0.8424.
- The browser-RTS case passed every deterministic and behavioral section after repair: all 30 static checks, all 26 desktop/phone browser checks including representative live-battle screenshots, all 5 steering checks, and capability score 0.8313. Its independent visual rubric averaged 3.60/5, below the required 4.0, so the authoritative result is **rejected**. It is a working original micro-RTS, not a Red Alert 2-quality game.
- Live testing surfaced production defects that self-validation missed: false terminal detection on recoverable worker failures, unrevealed offscreen sections in screenshots, duplicate CTA/state nodes, mismatched CSS-art classes, narrow-range whole-document duplication, binary screenshot readback contaminating terminal output, and explicit edits being misclassified read-only because later checks were described as read-only. Focused regressions now cover the corrected harness, file-edit, source-recovery, and intent-policy paths.
- The final RTS visual-repair attempt improved terrain, faction zones, labeled bases, silhouettes, damage feedback, and responsive canvas rendering, but repeated provider completion timeouts prevented the internal validator from settling within the 20-minute outer limit. The run was stopped after its last materialized artifact and judged only by the independent harness.
