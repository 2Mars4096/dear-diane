# 54: DAN Control-Plane Rewrite Around Universal Agents

**Status:** in-progress
**Goal:** Rebuild DAN's top control plane as durable universal-agent controllers plus bounded organisms, replacing concierge-first orchestration with a strangler migration so DAN can serve as one high-trust Mac/operator chat plane with a question-driven recurrent hierarchy instead of a collection of partial brains.

## Problem

The reusable worker / tissue / organ substrate is now materially stronger than DAN's older top-level concierge stack, but the default DAN product path still routes through legacy control-plane layers that have accumulated routing, triage, and execution glue over time.

That creates three problems:

- the best current DAN surfaces (`dan code`, `dan research`) already prove that durable universal-agent controllers plus bounded organisms can work better than the legacy top path
- the top-level DAN experience still depends on concierge / dispatcher / chat-manager era control logic that is harder to reason about, test, and extend with new organisms such as Incident Commander
- the repo already has much broader capability reach (local files, shell, git, web, browser, desktop control, messaging adapters), but the current top path is not organized as one clear general operator plane over those capabilities

This plan rewrites the DAN control plane around the universal-agent substrate already landed under `46-*`, `51-*`, and `52-*`, while keeping the existing engine, tools, search, and runtime adapters intact.

## Scope

In scope:

- a new durable top-level `DANConversationController`
- route / intent decisions expressed through explicit worker output contracts instead of concierge-first heuristics
- a reusable recurrent supervision contract (`supervisor_brief -> worker_report -> review_decision`) for multi-tier loops
- a transport-only migration path for gateway / CLI surfaces
- a general operator lane for local workspace, web/browser, and messaging/desktop actions under explicit approval envelopes
- a first new production-shaped organism (`Incident Commander`) that proves the larger composition model
- a benchmarked use-case matrix for the general Mac/operator surface
- explicit coexistence and cutover rules for legacy concierge paths during migration

Out of scope:

- a full rewrite of the engine, tool layer, search subsystem, or graph runtime
- deleting concierge immediately before the replacement path is proven
- turning every side effect into free-form agent behavior instead of keeping deterministic actuation adapters

## Tasks

- [x] 1. Define the rewrite boundary and migration strategy
  - [x] 1-1. Keep the worker core, durable runner, tools, search, and run manager as reusable substrate rather than rewriting them
  - [x] 1-2. Rewrite the DAN control plane with a strangler migration, not a flag-day reset
  - [x] 1-3. Freeze the recurrent supervision loop as the stable architecture contract rather than hard-coding fixed chains
- [ ] 2. Build the new top-level DAN durable controller via [54-1-dan-conversation-controller-and-routing-strangler](54-1-dan-conversation-controller-and-routing-strangler.md)
- [x] 3. Freeze the broad general-operator use-case matrix and safety envelopes via [54-4-general-mac-operator-use-case-matrix-and-safety-envelopes](54-4-general-mac-operator-use-case-matrix-and-safety-envelopes.md)
- [x] 4. Build the first larger production-shaped organism via [54-2-incident-commander-organism](54-2-incident-commander-organism.md)
- [x] 5. Add shared universal-agent run observability via [54-5-universal-agent-organism-log-v1](54-5-universal-agent-organism-log-v1.md)
- [x] 6. Define and execute the coexistence/cutover path via [54-3-legacy-concierge-cutover-and-surface-migration](54-3-legacy-concierge-cutover-and-surface-migration.md)
- [ ] 7. Exit with one coherent DAN-v2 control-plane story
  - [ ] 7-1. DAN top-level routing is owned by durable universal-agent controllers
  - [ ] 7-2. General Mac/operator tasks route through explicit organisms or operator lanes instead of surface-specific glue
  - [ ] 7-3. Legacy concierge is either removed from the default path or explicitly retained only for still-unmigrated surfaces

## Success Criteria

- top-level DAN routing is expressed through explicit worker/organism contracts instead of concierge-special-case glue
- DAN can handle at least one frozen benchmark in each major general family: ask/research, local workspace mutation, browser/computer assistance, and incident response
- multi-round control loops show `intent down / evidence up` behavior and converge by narrowing uncertainty, requesting a sharper delta, producing clearer artifacts, or stopping honestly
- at least one new high-value organism beyond Code/Research is running on the same substrate
- gateway / CLI / future app surfaces can talk to the same durable DAN controller without bespoke routing logic
- specialist organisms and future DAN-v2 lanes share one universal-agent-backed trace/log contract for LLM calls, tools, contracts, handoffs, and blockers
- the migration preserves current working specialist paths (`dan code`, `dan research`) instead of regressing them into one oversized generic assistant
- the cutover is benchmarked and reversible

## Decisions

- This is a control-plane rewrite, not an engine rewrite.
- `dan code` and `dan research` remain specialist product surfaces and become exemplars for the broader DAN rewrite.
- DAN-v2 should become one high-trust operator plane for Mac/server work, not only a code/research switchboard.
- The stable architecture element is the recurrent supervision contract, not a menu of fixed production chains.
- The strangler path should add a new controller path, not clone every downstream tool/runtime script; both DAN-v1 and DAN-v2 should share the same substrate wherever possible.
- Side effects stay behind deterministic adapters where possible; universal agents own reasoning, routing, review, and synthesis.
- Unsafe side effects such as browser input, desktop input, outbound messaging, or raw OS automation stay behind policy, approval, and audit boundaries instead of becoming unrestricted prompt behavior.
- The first proving extension is `Incident Commander`, not a vague general-purpose super-organism.
- Bounded specialist-product observability should converge on one universal-agent log contract instead of continuing separate Code/Research event dialects.

## Notes

- The current durable primitives (`WorkerCoreExecutor`, `DurableAgentRunner`, tissues, organs, and bounded organisms) are already strong enough to support this rewrite path.
- The rewrite should consume existing specialist organisms wherever they already work well instead of rebuilding them prematurely under a single new top-level shell.
- The broad use-case families to anchor now are: ask/research, local workspace operator, browser/download operator, desktop/messaging operator, and operational incident handling.
- Intent should flow downward as sharper briefs; evidence, blockers, and best-next-questions should flow upward into review decisions.
- [54-5-universal-agent-organism-log-v1](54-5-universal-agent-organism-log-v1.md) owns the shared `organism_log_v1` trace contract for DAN Code, DAN Research, and future DAN-v2 organism lanes.
- A future follow-up can promote workflow-building / rebuilding into a larger organism too, but the first priority is replacing the top-level DAN control path with something simpler and more testable.
- Initial landed slice: `/api/chat/message` now has a real DAN-v1 vs DAN-v2 selector seam, DAN-v2 owns top-level turn triage through `DANConversationController`, and direct DAN-v2 responses can terminate without touching concierge. Delegated execution still hands off into the shared legacy substrate while the lower lanes are migrated.
- Third landed slice: DAN-v2 now has an `incident` lane and a durable Incident Commander controller with frozen incident scenarios, action/approval boundaries, explicit terminal states, and incident session persistence through the same control-plane metadata seam.
- Fourth landed slice: the shared DAN-v2 controller membrane is now richer. DAN facts now carry workspace/platform/approval/adapter/tool-family context, and the worker/review packet pair now preserves `what_changed`, `evidence`, `artifacts`, `confidence`, `best_next_question`, and `next_delta` fields for sharper recurrent review loops.
- Fifth landed slice: `src/dan/worker/organism_log.py` now provides the first shared `organism_log_v1` substrate, and DAN Code / DAN Research run logs now converge on one comparable schema plus backend-side span/blocker projections while keeping their existing workspace file layout stable.
- Nineteenth landed slice: `src/dan/worker/core/executor.py` now emits timed `contract.validation.*` and `contract.repair.*` spans with explicit `validation_phase`, `repair_round`, and `normalization_mode`, `src/dan/worker/organism_log.py` classifies them as nested `output_contract_validation` / `output_contract_repair` spans under the worker span, and the direct DAN Code path now forwards those shared executor events into `.dan-code/runs/.../events.jsonl` through `CallbackEventSink` so real product runs capture the same contract seam.
- Twentieth landed slice: [54-5-universal-agent-organism-log-v1](54-5-universal-agent-organism-log-v1.md) is now complete. Live `CrossCellTraceLog` handoff/signal callbacks stream straight into `organism_log_v1`, durable runner mailbox/session/background lifecycle events now derive `durable_*` spans with mailbox-turn parentage for worker/model/contract spans, DAN Research persists those controller callbacks on `.dan-research/control-plane-events.jsonl`, and the shared analysis/tests now prove product-shaped traces can answer both "what took time?" and "what blocked the next step?" from the unified log.
- Sixth landed slice: the first production-shaped proving organism is now materially complete. Incident Commander can investigate live workflow and adapter failures, execute bounded live `contain` / `pause` / `retry` actions where a safe deterministic server seam exists, synthesize explicit operator-facing closeout summaries, and drive nested DAN Code repair through the default direct coding-runtime path inside DAN-v2 while keeping an explicit disable/fallback escape hatch.
- Seventh landed slice: the migration selector is no longer env-only. `/api/chat/message` now accepts a request-scoped `control_plane_mode` override, reports the selected mode/source back in the immediate response, and persists that choice in thread metadata so internal surfaces can move first while external surfaces still hold on the safer default.
- Eighth landed slice: the migration selector now also has per-surface env levers above the global switch. Remote/local chat clients honor `DAN_CLI_CONTROL_PLANE` / `DAN_CHAT_CONTROL_PLANE`, and adapter bridges honor `DAN_ADAPTERS_CONTROL_PLANE` plus per-surface overrides such as `DAN_TELEGRAM_CONTROL_PLANE`, so internal chat can move onto DAN-v2 ahead of messaging transports without cloning the downstream substrate.
- Ninth landed slice: the server now owns a broader surface-policy tier above the global selector. `/api/chat/message` resolves `DAN_<SURFACE_TYPE>_CONTROL_PLANE` plus `DAN_INTERNAL_CONTROL_PLANE` / `DAN_EXTERNAL_CONTROL_PLANE` before falling back to `DAN_CONTROL_PLANE`, and the remaining direct caller gaps were tightened so editor chat and generic adapter chat both send canonical surface identity into that shared seam.
- Tenth landed slice: direct-post adapter surfaces now also honor the adapter-group seam. The server router treats `DAN_ADAPTERS_CONTROL_PLANE` as the external adapter-group override before the broader external-surface fallback, and the Telegram fleet surface now posts explicit `surface_type` / `surface_id` so bridged and direct adapter paths converge on the same selection policy.
- Eleventh landed slice: the in-process adapter bridge now routes both DAN-v1 and DAN-v2 through `chat_message(..., concierge=True)` instead of branching on the selector itself. That removes one more adapter-owned top-level routing decision and pushes queued legacy follow-up handling onto the shared local chat stream seam.
- Twelfth landed slice: the local in-process chat runtime now routes all chat and `/run` traffic through the shared server chat router too. `src/dan/cli/chat_local.py` no longer keeps its own legacy mode-detection / dispatcher / queued-stream path; it builds one canonical `ChatMessageRequest`, keeps the existing CLI selector overrides, and consumes the shared `iter_local_chat_stream_events(...)` stream for both chat and run channels.
- Thirteenth landed slice: scheduled/background chat dispatch now uses that same seam too. `src/dan/server/startup/__init__.py` no longer drops scheduled freeform actions straight onto concierge/dispatcher `SurfaceMessage`; it builds one canonical `ChatMessageRequest`, routes through `chat_message(..., concierge=True)`, and collapses the shared local stream back into terminal text, with `collect_terminal_content(...)` now tolerant of both object and dict event rows.
- Fourteenth landed slice: the specialist-product cutover proof is now explicit on both lanes. `tests/test_server/test_control_plane_runtime.py` now covers DAN-v2 Research handoff parity next to the existing Code-lane cases, and `tests/test_concierge/test_chat_router.py` now proves that a DAN-v2 Research handoff still reaches the shared legacy execution path with the expected supervisor brief/prompt context instead of silently regressing one product lane while the other stays covered.
- Fifteenth landed slice: the approval envelope now survives specialist handoff too. `src/dan/server/control_plane.py` now threads the request’s `approval_mode` plus tool envelope into the Code/Research lane contexts and direct-code session metadata instead of hardcoding a generic server default, and the router/runtime regressions now prove that a confirm-risky messaging-surface request preserves its browser/desktop/adapter boundary metadata through DAN-v2 shaping and back into the shared legacy execution path.
- Sixteenth landed slice: the cutover/retirement seam is now frozen. `tests/test_concierge/test_chat_router.py` adds a representative routing matrix that proves global/request/surface/internal/adapter selector scenarios keep the same selected mode/source in both the immediate response and persisted thread metadata while still hitting the expected DAN-v1 vs DAN-v2 path, and `src/dan/server/routers/chat.py` now resolves that control-plane selection once per request and reuses it for persistence plus DAN-v2 handoff metadata. That leaves the legacy path as reusable execution substrate instead of a second top-level selector owner and completes [54-3-legacy-concierge-cutover-and-surface-migration](54-3-legacy-concierge-cutover-and-surface-migration.md).
- Seventeenth landed slice: [54-4-general-mac-operator-use-case-matrix-and-safety-envelopes](54-4-general-mac-operator-use-case-matrix-and-safety-envelopes.md) is now real control-plane state instead of only a plan. `src/dan/server/control_plane.py` now classifies each DAN-v2 turn into one frozen operator pack (`knowledge_local_context`, `local_operator`, `browser_download`, `desktop_messaging`, `cross_surface_operator`) plus one frozen safety envelope (`read_only`, `local_mutation`, `external_side_effect`) and supervision policy, then threads those facts into `DANConversationFacts` and the general operator-lane handoff metadata. `tests/test_server/test_control_plane_runtime.py` now proves representative read-only, local-mutation, and cross-surface external-side-effect cases.
- Eighteenth landed slice: [54-4-general-mac-operator-use-case-matrix-and-safety-envelopes](54-4-general-mac-operator-use-case-matrix-and-safety-envelopes.md) is now complete. `src/dan/server/control_plane.py` now freezes the broad operator boundary as structured runtime data too: `operator_execution_target` decides what may stay `inline_or_specialist` versus what must enter the `bounded_operator_lane`, `operator_deterministic_capability_sets` / `operator_deterministic_adapters` name the capability families that stay behind deterministic adapters, `operator_shared_control_membrane` makes the recurrent loop contract explicit, and `operator_non_goals` now records the v1 exclusions around raw AppleScript, unsandboxed system administration, silent outbound messaging, and filler. `tests/eval/operator_plane_benchmark_matrix.py` plus `tests/eval/test_operator_plane_benchmark_matrix.py` now freeze the acceptance family set, the chained cross-surface fixture, and the 2+-loop scoring expectations, while `tests/test_server/test_control_plane_runtime.py` locks the richer runtime facts and operator-lane metadata. The same slice also fixed a classifier bug where naive substring cue matching could misread `latest` as the local-operator `test` cue; `src/dan/server/control_plane.py::_contains_any(...)` now uses word-boundary matching for single-token cues.
