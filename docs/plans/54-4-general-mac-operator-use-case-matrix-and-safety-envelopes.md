# 54-4: General Mac Operator Use-Case Matrix And Safety Envelopes

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Freeze the first broad DAN-v2 use-case matrix and the safety envelopes that let one durable chat plane handle local files, shell/git, web, browser, desktop, and messaging work on macOS without collapsing into one unsafe giant tool loop or one brittle fixed-chain library.

## Tasks

- [x] 1. Freeze the first broad DAN-v2 use-case packs
  - [x] 1-1. Knowledge and local-context asks: repo/docs/PDF/spreadsheet answers plus current web facts
  - [x] 1-2. Local operator tasks: create/edit files, run shell/test/build flows, inspect git state, branch, and commit
  - [x] 1-3. Browser and download tasks: navigate pages, extract text, capture screenshots, submit forms, and download artifacts
  - [x] 1-4. Desktop and messaging tasks: focus apps, use clipboard/file dialogs, send notifications, and prepare or deliver email / WeChat / Telegram follow-ups
  - [x] 1-5. Cross-pack chained tasks that may span multiple families without hard-coding one exact route
- [x] 2. Define the safety and approval envelopes
  - [x] 2-1. Read-only envelope for local/web asks
  - [x] 2-2. Local mutation envelope for file/shell/git work
  - [x] 2-3. External-side-effect envelope for browser input, desktop input, outbound messaging, and account-affecting actions
  - [x] 2-4. Define when the supervisor may keep looping versus when it must stop, escalate, or ask for approval
- [x] 3. Decide the organism boundary for broad operator work
  - [x] 3-1. What the top controller handles inline versus what routes to a bounded operator organism or lane
  - [x] 3-2. Which capabilities stay deterministic adapters instead of free-form agent behavior
  - [x] 3-3. Share one recurrent control membrane across operator lanes and specialist organisms rather than inventing a different loop style per family
- [x] 4. Freeze an acceptance matrix that proves end-to-end generality
  - [x] 4-1. One frozen benchmark each for ask/research, local mutate, browser/download, and desktop/messaging assistance
  - [x] 4-2. One cross-surface chained benchmark (for example: find information -> download artifact -> patch local file -> send summary)
  - [x] 4-3. At least one benchmark per family must require 2+ supervision loops and score whether higher-tier guidance narrows uncertainty, requests the right delta, and avoids busywork
  - [x] 4-4. Treat benchmark chains as benchmark families or fixtures, not as hard-wired production routes
- [x] 5. Define explicit non-goals for v1
  - [x] 5-1. No raw unrestricted AppleScript surface
  - [x] 5-2. No unsandboxed system-administration autonomy or silent outbound messaging
  - [x] 5-3. No pseudo-motivational filler as a substitute for concrete direction

## Decisions

- DAN-v2 generality should be expressed as a portfolio of bounded use-case packs, not as “one model can do anything.”
- Browser, desktop, and messaging actions require explicit policy, approval, and audit boundaries.
- Raw AppleScript may exist as a backend implementation detail or tightly scoped adapter, not as a default public agent power.
- The stable architecture element is the supervision loop contract; individual chains are only benchmarks or examples.
- The first frozen operator packs are `knowledge_local_context`, `local_operator`, `browser_download`, `desktop_messaging`, and `cross_surface_operator`.
- The first frozen safety envelopes are `read_only`, `local_mutation`, and `external_side_effect`, with matching supervision policies `continue_with_evidence`, `continue_with_local_guards`, and `approval_gate_for_external_side_effects`.
- Broad operator work now freezes one explicit execution boundary: `knowledge_local_context` may stay `inline_or_specialist`, while the mutation/browser/desktop/cross-surface packs route to one `bounded_operator_lane`.
- Deterministic capability families are part of the contract surface now (`local_context_readers`, `grounded_web_readers`, `workspace_mutation`, `shell_git`, `browser_navigation`, `artifact_downloads`, `desktop_control`, `messaging_adapters`) and raw AppleScript remains an implementation detail, not a public agent power.
- The shared recurrent membrane for broad operator work is now named explicitly as `supervisor_brief_worker_report_review_v1`.

## Notes

- This plan makes the rewrite aim at a general Mac/operator plane without losing composability.
- A later follow-up can split communications, scheduler, or program-manager organisms out of the broader operator lane once the use-case matrix is proven.
- "Motivating" high-tier guidance means `why this matters now`, `what exact delta to produce next`, and `when to stop or ask back`, not vague encouragement.
- Landed first slice: `src/dan/server/control_plane.py` now classifies every DAN-v2 turn into one frozen operator pack plus one safety envelope and supervision policy, then threads that matrix into `DANConversationFacts` as `operator_use_case_pack`, `operator_safety_envelope`, `operator_supervision_policy`, and `operator_stop_conditions`. The initial matrix is deterministic instead of heuristic prose: `knowledge_local_context`, `local_operator`, `browser_download`, `desktop_messaging`, and `cross_surface_operator` map onto `read_only`, `local_mutation`, or `external_side_effect`, and the last class now freezes the control-plane rule that DAN-v2 must stop for approval or a sharper target before browser input, desktop input, or outbound messaging. `tests/test_server/test_control_plane_runtime.py` now proves representative read-only, local-mutation, and cross-surface external-side-effect cases.
- Landed second slice: the operator boundary and acceptance matrix are now frozen too. `src/dan/server/control_plane.py` now derives `operator_execution_target`, `operator_deterministic_capability_sets`, `operator_deterministic_adapters`, `operator_shared_control_membrane`, and `operator_non_goals` next to the pack/envelope tuple, and threads that richer contract into `DANConversationFacts` plus the generic operator-lane handoff metadata/prompt context. `tests/eval/operator_plane_benchmark_matrix.py` now holds one frozen family fixture for ask/research, local mutate, browser/download, desktop/messaging, and one chained cross-surface case, while `tests/eval/test_operator_plane_benchmark_matrix.py` locks the runtime classification, the 2+-loop scoring requirement, and the rule that these chains stay benchmark fixtures instead of production routes.
