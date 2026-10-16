# Idea Cart

Last reviewed: 2026-10-16

## Priority and expected gain

Ranked for the next bounded step after the remaining runtime and paired Sticky Todo acceptance. Source consolidation and v0.1.6 publication are complete according to the release receipt; live steering is user-confirmed. User workflow benefit, dependencies and effort determine priority; uncertainty determines whether to explore. No deadlines or commercial gains are assumed.

| Order | Item | Priority / impact | Next action and completion criterion |
|---|---|---|---|
| 1 | IC-033 — Hybrid paper search | Next / high | Exact/concept query fixtures and a local retrieval comparison. Stop with relevance/latency evidence; user chooses embedding disclosure tradeoffs. The result-cap fix alone is checked out/completed. |
| 2 | IC-044 — Floating command bar | Next / medium–high | Prototype summon, explicit destination and retained drafts; show value beyond Sticky Todo. |
| 3 | IC-038 — Selective undo | Next / high potential | Disposable-repo experiment preserving unrelated edits and exposing overlap conflicts; no live rollback. |
| 4 | IC-030 — Task-specific views | Later / high potential | Compare one research evidence view against chat; retain only demonstrated improvements. |
| 5 | IC-029 — Adaptive blueprints | Later / uncertain increment | Audit current substrate against real-work failures before proposing new orchestration. |
| 6 | IC-043 — Configurable panes | Later / medium | Two-pane prototype with unambiguous shared-composer routing and draft retention. |
| 7 | IC-039 — Voice input | Later / medium | Trial existing OS dictation; identify an unmet need before building capture. |
| 8 | IC-025 — Meeting Mode | Deferred / uncertain | Draft one shared-session workflow; user decides whether multi-user work becomes a product priority. |
| 9 | IC-027 — Factory broker | Deferred / speculative | Assess one product family's providers/responsibilities/economics; stop with go/no-go recommendation. |
| 10 | IC-026 — Manufacturing compiler | Dependency-gated | Only after IC-027: one offline reviewable production package. |
| Gate | IC-028 — Physical-order approvals | Required if manufacturing starts | Prepare specific disclosure/design/order terms for the user's consequential decisions. |
| 11 | IC-045 — Tauri | Deferred / low current benefit | Revisit only after a measured Electron limitation. |

IC-031/032 remain checked out into [Plan 7](../docs/plans/7-phone-personal-agent.md); implementation has not started. The 12 previously ranked ideas above remain exploratory except the explicitly authorized IC-033 result-cap subtask. IC-043/044 share input-routing concerns; IC-029/030 require auditing the implemented substrate rather than assuming it is absent.

Review notes:
- October 16: IC-047 captures the deferred intent router. Active count is 14; prune/merge deferred ideas at the next cart review before further expansion.
- October 15: IC-046 adds natural-language scheduling, bringing the active cart to 13. Prune or merge deferred ideas at the next review before further expansion; none are removed by this capture.
- Close current commitments before opening several exploratory tracks. The cart is at the preferred 12-item ceiling; select one candidate and keep manufacturing deferred rather than adding more ideas.
- The historical Plan 59 link in the manufacturing vision predates cutover. Verify current code before checkout.
- Full IC-033 checkout, external embeddings, live rollback, manufacturing orders and shell migration are not authorized by the low-risk batch.

## Active Items

### IC-047 - Context-aware meta-layer intent routing
- Status: carted
- Priority: high
- Kind: idea
- Scope: message admission above native leads
- Point: Interpret ordinary messages using the full sentence, current conversation and active work, identifying both intended action and target before routing.
- Why: First-word matching confuses conversational requests such as “stop using that library” with run controls and loses contextual continuation.
- Constraints: Keep only explicit slash commands deterministic for now. Do not implement the semantic router until checkout. Ambiguous intent/target goes to the lead or a focused clarification; never silently stop or redirect unrelated work. Coordinate with IC-046 scheduling.
- Checkout target: scoped intent-routing design and adversarial acceptance cases.
- Acceptance: Distinguish run controls from task content, preserve conversational resume/history and user-selected lead settings, respect thread/workspace boundaries, and demonstrate safe ambiguity behavior without adding composer modes.
- Source: October 16 user decision; implementation deferred explicitly.


### IC-046 - Natural-language scheduling owned by Diane’s meta layer
- Status: carted
- Priority: medium
- Kind: idea
- Scope: message intake and durable task orchestration, above Codex/Claude leads
- Point: Detect explicit scheduling intent in ordinary messages and let Diane’s meta layer persist and dispatch one-time or recurring tasks through the existing run/queue system.
- Why: Schedule work naturally without another composer mode or relying on a lead to keep timers alive.
- Constraints: No Schedule mode; show a compact editable/cancellable receipt. Mere date mentions must not schedule work. Clarify ambiguous timing/timezones. Preserve target session, execution settings and permission boundaries; leads execute due tasks, not manage schedules.
- Checkout target: scheduling design and implementation plan when selected.
- Acceptance: Cover delay/date/recurrence, edit/cancel, duplicate prevention, restart recovery, busy-session handling and explicit sleep/offline catch-up policy. Define ambiguous timing and missed-run behavior before implementation.
- Source: October 15 discussion; user explicitly places ownership above native leads. Capture only; no scheduling implementation authorized.


### IC-045 - Consider Tauri only if Electron becomes a limitation
- Status: carted
- Priority: low
- Kind: idea
- Scope: desktop shell
- Point: Keep Electron for now; revisit Tauri only if a concrete benefit justifies migration, since the current Electron app works well for the user.
- Why: Potentially reduce desktop packaging overhead without sacrificing existing features or workflows.
- Constraints: Preserve all user-visible features, behavior, and existing data; parity is a requirement to verify, not an established migration result. No migration or implementation is authorized by this capture.
- Checkout target: desktop-shell feasibility assessment if selected later.
- Acceptance: Inventory Electron/Node integrations, assess Tauri equivalents and backend packaging, and verify workflow/data parity and rendering on target platforms before deciding whether to migrate.
- Source: 2026-10-10 discussion; user explicitly deferred the idea because Electron works fine.

### IC-043 - Configurable panes with one shared composer
- Status: carted
- Priority: medium
- Kind: idea
- Scope: workspace layout and input routing
- Point: Arrange work panes like terminal splits, including one row/two columns and two rows/two columns, while keeping one shared bottom composer.
- Why: Compare and follow parallel sessions or documents without duplicating the input controls.
- Constraints: The shared composer must clearly identify its target project/session; retain drafts when switching panes. Pane arrangement should not change session ownership or running work.
- Checkout target: pane-layout and composer-routing design when selected.
- Acceptance: Users can split, resize, focus, and close panes; the single composer reliably addresses the intended session with its existing run controls.
- Source: 2026-10-02 user idea, Ghostty-style pane management. Capture only; no implementation authorized.

### IC-044 - Floating Diane command bar
- Status: carted
- Priority: medium
- Kind: idea
- Scope: quick access and cross-session task control
- Point: Offer a movable floating composer, summonable with a shortcut such as double-tap Option, to ask or assign work from one compact Spotlight-like bar.
- Why: “Diane in the pocket”: one bar controls work without requiring the user to navigate every intermediate view.
- Constraints: Show brief project/session and task context; include project switching and the session wheel, plus existing input/run controls. Keep the destination explicit before sending; preserve drafts when hiding or moving the bar. Assess in-app versus system-wide invocation and shortcut conflicts at checkout.
- Checkout target: floating-composer interaction prototype when selected; share input routing with IC-043.
- Acceptance: Summon, move, switch destination, send/queue/steer, and dismiss from the bar without losing context or input; detailed work remains accessible on demand.
- Source: 2026-10-02 user idea, Spotlight and double-tap Option inspiration. Capture only; no implementation authorized.

### Workflow preferences for remaining IC-038–039
- Optimize for the user’s smooth personal workflow; uniqueness is not a goal. Build on existing orchestration and parallel work.
- Preserve conversations, sidebar, and Team panel; no Kanban requirement.
- IC-034–037 are checked out into [Plan 4-11](../docs/plans/4-11-workflow-friction.md); IC-038/039 remain carted.





### IC-038 - Undo a particular agent change
- Status: carted
- Priority: medium
- Kind: idea
- Scope: change recovery
- Point: Preview and undo a selected agent change while preserving unrelated edits.
- Why: Make experimentation reversible.
- Checkout target: Tracked changes and rollback design at checkout.
- Acceptance: Undo one iteration without losing unrelated work; overlapping changes require conflict resolution. Message edit/resend already exists but does not reverse filesystem effects.

### IC-039 - Voice input
- Status: carted
- Priority: medium
- Kind: idea
- Scope: composer input
- Point: Hold a shortcut to dictate, release to edit the transcription, then send.
- Why: Capture detailed ideas with less typing.
- Checkout target: Composer voice-input plan at checkout.
- Acceptance: Clear start/stop/cancel states and editable drafts; never auto-send. Evaluate existing OS dictation before adding infrastructure; built-in voice capture was not found.

### IC-033 - Responsive hybrid paper search
- Status: carted
- Priority: high
- Kind: idea
- Scope: Dear Diane Cmd+K paper search
- Point: Combine immediate keyword results with asynchronous semantic retrieval over titles, abstracts, and note sections, keeping exact title/citation matches first and showing useful matching excerpts.
- Why: Both Simchi-Levi papers are indexed correctly but lack the word “resilience”; lexical matching misses their related robustness and disruption-mitigation concepts.
- Constraints: Precompute and locally cache embeddings, refresh changed files only, debounce query embedding, reuse repeated queries, and ignore stale responses; retain keyword fallback. Choose local versus API embeddings explicitly based on latency and text-disclosure tradeoffs; no vector database or answer-generation call needed for the current catalogue.
- Checkout target: semantic-search evaluation/implementation plan when selected. Result-cap subtask completed in Plan 4-7 on October 12; remaining semantic work stays carted.
- Acceptance: “Resilience” retrieves both relevant Simchi-Levi papers; representative exact and conceptual queries retain useful ranking; measure cold/warm semantic latency while keyword results remain responsive, including provider failure.
- Source: 2026-09-29 search diagnosis and latency discussion. Semantic retrieval remains deferred; only the result-cap subtask was authorized in the October 12 low-risk batch.

### IC-030 - Fluid task surfaces
- Status: carted
- Priority: medium
- Kind: idea
- Scope: adaptive interaction design
- Point: Project the current task graph, artifact, and decision point into task-appropriate typed views and request-input controls within a stable conversational shell.
- Why: Users should be able to understand and steer different kinds of work without translating everything into generic chat or navigating a different arbitrary application for every task.
- Checkout target: future interaction-design research brief
- Acceptance: Several task families demonstrate clearer comprehension and lower-friction input through a trusted component grammar, with free-form chat, accessibility, provenance, and user override preserved.
- Links: `docs/business/meeting-mode-to-manufacturing-vision.md`

### IC-029 - Adaptive task-native blueprints
- Status: carted
- Priority: medium
- Kind: idea
- Scope: orchestration and task representation
- Point: Compile each request into the smallest useful, revisionable semantic task graph for its goal, evidence, risk, and artifact; treat plan-execute-validate as one reusable topology rather than a mandatory shell.
- Why: Debugging, research, design, meetings, direct actions, and manufacturing need different branches, loops, parallel work, decisions, and review gates.
- Checkout target: future orchestration research brief
- Acceptance: Representative task families can use visibly different graph topologies while preserving stable goal, permission, evidence, status, provenance, and completion contracts.
- Links: `docs/business/meeting-mode-to-manufacturing-vision.md`

### IC-025 - Meeting Mode and group vibe coding
- Status: carted
- Priority: medium
- Kind: idea
- Scope: collaborative product surface
- Point: Let multiple people and agents talk and perform visible work in one live room, with parallel workstreams, provenance, and explicit decision and merge gates.
- Why: Conversation should be able to guide work while it is happening instead of becoming a transcript that must be converted into tasks afterward.
- Checkout target: future product-discovery brief
- Acceptance: Real group sessions demonstrate a clearer decision record and faster reviewed output than ordinary chat plus separate project tools.
- Links: `docs/business/meeting-mode-to-manufacturing-vision.md`

### IC-027 - Existing-factory broker layer
- Status: carted
- Priority: medium
- Kind: follow-up
- Scope: manufacturing-as-a-service
- Point: Explore DAN as the orchestration and brokerage layer above existing instant-quote services, RFQ marketplaces, and manufacturing concierges instead of owning factory capacity first.
- Why: The market already supports one-off digital fabrication and low-volume electronics, but finished-product assembly and cross-category ordering remain fragmented.
- Checkout target: provider and unit-economics research
- Acceptance: A provider map identifies quote/order interfaces, geographic coverage, responsibility boundaries, and viable prototype economics for one narrow wedge.
- Links: `docs/business/meeting-mode-to-manufacturing-vision.md`

### IC-026 - Vibe-to-manufacturing compiler
- Status: carted
- Priority: low
- Kind: idea
- Scope: physical-product creation
- Point: Compile conversational product intent into reviewable, process-specific production packages rather than treating a render or generic blueprint as factory-ready.
- Why: Parts, garments, electronics, and food-contact goods require different files, validations, and approval paths.
- Checkout target: research prototype only after product-family validation
- Acceptance: One narrow, non-regulated product family can produce packages that multiple providers quote with no more than one bounded human correction cycle.
- Links: `docs/business/meeting-mode-to-manufacturing-vision.md`

### IC-028 - Human-gated physical orders
- Status: carted
- Priority: high
- Kind: constraint
- Scope: blueprint release and manufacturing orders
- Point: Never release a generated design or place a paid physical-production order without process-specific DFM, applicable compliance review, and explicit human approvals.
- Why: Manufacturability does not prove function or safety, and physical products introduce IP, quality, liability, importer, warranty, and recall responsibilities.
- Checkout target: future safety and approval contract
- Acceptance: Any future prototype flow has named approval gates for supplier disclosure, design release, payment, sample acceptance, and production scale-up.
- Links: `docs/business/meeting-mode-to-manufacturing-vision.md`

## Closed and transferred ideas

| Item | Status | Completion or remaining boundary |
|---|---|---|
| IC-034 | Done | Worker history, scoped interaction and recovery — [4-11](../docs/plans/4-11-workflow-friction.md). Adapter-specific live checks remain separately tracked. |
| IC-035 | Done | Quiet completion/attention notifications — [4-11](../docs/plans/4-11-workflow-friction.md). Native notification delivery remains an acceptance limitation. |
| IC-036 | Done | Changes review and anchored feedback — [4-11](../docs/plans/4-11-workflow-friction.md). Selective filesystem undo is separate IC-038. |
| IC-037 | Done | Delegated Git isolation and reviewed integration — [4-11](../docs/plans/4-11-workflow-friction.md). |
| IC-040 | Done | Independent session views/windows — [4-12](../docs/plans/4-12-session-actions.md); later unified navigation retained. |
| IC-041 | Done | Optional persistent session pins — [4-12](../docs/plans/4-12-session-actions.md). |
| IC-042 | Done | Shared session actions menu — [4-12](../docs/plans/4-12-session-actions.md). |
| IC-033 result cap | Done, subtask only | All lexical matches reachable — [4-7](../docs/plans/4-7-paper-library.md). Semantic retrieval remains carted. |
| IC-031/032 | Transferred, not implemented | [Plan 7](../docs/plans/7-phone-personal-agent.md) owns the phone/personal-agent backlog. |

All 14 current Active Items remain unresolved; none is silently marked complete because an adjacent feature shipped. Historical IC-001–024 receipts below retain their original implemented/committed distinction.

## Checkout Log

### 2026-10-13
- Explicitly close IC-034–037 and IC-040–042 from completed/installed plans; close only IC-033’s result-cap subtask. Retain IC-031/032 as transferred, not delivered. Preserve every historical receipt.
- Runtime update: user confirmed live Codex steering delivery; continue-after-current, refresh, Regenerate/Fork, Team, reader Ask and paired Todo remain acceptance work. Recent installations/publication supersede the earlier prepared-only receipt below.

### 2026-10-12
- IC-033 -> partial checkout to [Plan 4-7](../docs/plans/4-7-paper-library.md): remove silent 12-result cap and verify all matches are reachable. Semantic retrieval remains active/carted.
- Reviewed all 12 active items using decision-triage; no other checkout. Manufacturing and Tauri remain deferred; no historical receipts deleted.

### 2026-10-01
- IC-040 -> implemented in [Plan 4-12](../docs/plans/4-12-session-actions.md): session tabs share the existing row; independent native/browser windows preserve selection and text drafts through reload. Closing views leaves running work intact.
- IC-041 -> implemented in Plan 4-12: persistent optional Pin/Unpin, separate from automatic Cmd/Ctrl+1–9 recent visits.
- IC-042 -> implemented in Plan 4-12: shared right-click/ellipsis menu with rename, move, unread/read, fork, copy ID/text, archive/restore, and archived-only deletion. Desktop/phone interaction checks pass. All three removed from active items.

### 2026-09-30
- IC-034–037 -> [Plan 4-11](../docs/plans/4-11-workflow-friction.md), authorized for sequential implementation with minimal changes and reuse of the existing team mechanism. Removed from active cart. Browser-style session tabs added to the same plan by follow-up request. IC-038/039 remain carted.

### 2026-09-29
- IC-031 -> plan: [7-phone-personal-agent](../docs/plans/7-phone-personal-agent.md), both iPhone and Android, browser first; native share/device access deferred. Removed from active cart.
- IC-032 -> plan: same staged plan covers personal records, durable follow-through, approvals, Google first, common provider contracts and rollout, then claims/returns/goals. [Muse connection research](../docs/business/muse-app-connections.md) informs the design. Planning complete; implementation not started. Removed from active cart.

### 2026-07-21
- IC-029 and IC-030 -> carted from the adaptive task-blueprint and fluid-frontend discussion; plan-execute-validate remains available as one graph pattern, while the stable contracts and typed-interface boundary are preserved in `docs/business/meeting-mode-to-manufacturing-vision.md`.

### 2026-07-14
- IC-025 through IC-028 -> carted from the Meeting Mode, group vibe coding, vibe blueprinting, and gig-factory discussion; supporting market scan and boundaries are preserved in `docs/business/meeting-mode-to-manufacturing-vision.md`.

### 2026-05-13
- IC-024 -> checked out into Plan 58 async core and board work: `58-2-durable-task-board-and-run-ledger`, `58-5-surface-and-narrator-integration`, and `58-6-tui-background-board`. Removed from active items.
- IC-001 through IC-023 -> pruned from active items because they were already committed to plans or implemented; retained receipts below rather than keeping completed work in the active cart.

### 2026-05-12
- IC-024 -> carted from the Hermes comparison follow-up: DAN already has status/narrator/board-ish state, but needs a visible durable operator board projection distinct from active-run narration.
- IC-022 and IC-023 -> checked out into `57-16` and `57-17` from Super DAN TUI intent-gate and neutral feedback discussion.
- IC-021 -> implemented in the Super DAN TUI conversation projection with a bottom `Working` / `Elapsed` footer and marked done.
- IC-021 -> carted from Super DAN TUI feedback requesting elapsed working time in the live panel.

### 2026-05-11
- IC-017 through IC-020 -> implemented in `src/dan/cli/super_tui.py`; marked done after tests covered conversational timeline, raw event mode, transcript replay, assistant summaries, and reset-state transcript preservation.
- IC-017 and IC-018 -> checked out into `57-14-super-dan-tui-conversational-timeline`.
- IC-019 and IC-020 -> checked out into `57-15-super-dan-tui-session-transcript`.
- IC-017 through IC-020 -> carted from Super DAN TUI feedback about flowing conversational progress and Codex-like transcript continuity across turns until reset.
- IC-001 through IC-016 -> checked out into Plan 57 follow-up docs: `57-10-active-run-operator-steering`, `57-11-progress-and-checkpoint-ux`, `57-12-super-dan-terminal-tui`, and `57-13-skill-mention-ux`.
- IC-015 and IC-016 -> originally carted from skill invocation UX discussion.
- IC-014 -> originally carted from planner/executor progress UX discussion.
- IC-001 through IC-013 -> originally carted from Super DAN UX/message-queue review.

## October 13 execution receipt
- Authorized priorities 1/3/4/5: startup error/diagnostic fix and targeted cleanup prepared; runtime regressions and current Todo fixtures pass; companion already merged/installed. Idle installation, full runtime live battery and paired authenticated handoff remain open in their existing plans.
- User follow-ups implemented in source: account-specific multi-provider Usage, Enter steer / Cmd+Enter queue, remove queue-status text and duplicate header Stop. No new exploratory idea checkout.
- Preserve concurrent Plan mode work; reviewed snapshot is prepared separately, not committed/published or installed over the active run.
