# Idea Cart

Last reviewed: 2026-10-02

## Priority and expected gain

Ranked for the next useful discovery or validation step, using expected user benefit and learning value, then effort, dependencies, and confidence. These are qualitative judgments from project records; commercial gains are unvalidated. Items remain carted.

| Order | Item | Priority | Expected gain | Effort for next step | Next useful step / dependency |
|---|---|---|---|---|---|
| 1 | IC-030 — Fluid task surfaces | High | High potential for clearer decisions and easier input across current workflows | Medium | Audit existing typed views, then compare one task-specific view with the current interface. Start with existing task contracts. |
| 2 | IC-029 — Adaptive task-native blueprints | High | High reuse potential across task families; incremental benefit uncertain | Medium | Audit the current substrate and test contrasting task families; identify missing behavior before adding orchestration machinery. |
| 3 | IC-025 — Meeting Mode | Medium | Medium near-term confidence; potential team coordination gains | Medium–high | Validate one shared-session workflow and its decision record before investing in multi-user infrastructure. |
| 4 | IC-027 — Factory broker layer | Medium | Medium learning value: establish whether a narrow manufacturing business is viable | Medium | Research one product family, provider interfaces, responsibilities, and unit economics before IC-026. |
| 5 | IC-026 — Manufacturing compiler | Low for now | Potentially high long-term benefit; low confidence and high delivery effort | High | Defer prototype work until IC-027 supports a viable product family; apply IC-028 before any disclosure or physical order. |
| Gate | IC-028 — Human-gated physical orders | High when manufacturing starts | Risk reduction and explicit responsibility | Scoped with manufacturing | Mandatory prerequisite for manufacturing execution; define gates alongside IC-027/026. |

IC-031 and IC-032 are checked out into [Plan 7](../docs/plans/7-phone-personal-agent.md). The remaining cart stays exploratory; reassess IC-030/029 against that plan before further checkout.

Review notes:
- Plan 7 combines IC-031 mobile delivery and IC-032 personal-agent capabilities. International positioning remains a discovery question in that plan, not a validated market claim.
- IC-029/030 overlap with the historical implemented substrate described in `docs/business/meeting-mode-to-manufacturing-vision.md`. Its Plan 59 link predates the current cutover; inspect current code before deciding what is complete or still missing. Keep only unresolved acceptance work at checkout.
- IC-026 remains low priority for near-term work. IC-028 retains high priority as a conditional gate.

## Active Items

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
- Priority: medium
- Kind: idea
- Scope: Dear Diane Cmd+K paper search
- Point: Combine immediate keyword results with asynchronous semantic retrieval over titles, abstracts, and note sections, keeping exact title/citation matches first and showing useful matching excerpts.
- Why: Both Simchi-Levi papers are indexed correctly but lack the word “resilience”; lexical matching misses their related robustness and disruption-mitigation concepts.
- Constraints: Precompute and locally cache embeddings, refresh changed files only, debounce query embedding, reuse repeated queries, and ignore stale responses; retain keyword fallback. Choose local versus API embeddings explicitly based on latency and text-disclosure tradeoffs; no vector database or answer-generation call needed for the current catalogue.
- Checkout target: paper-search implementation plan when selected; also address the silent 12-result cap.
- Acceptance: “Resilience” retrieves both relevant Simchi-Levi papers; representative exact and conceptual queries retain useful ranking; measure cold/warm semantic latency while keyword results remain responsive, including provider failure.
- Source: 2026-09-29 search diagnosis and latency discussion. Deferred by user; implementation not started.

### IC-030 - Fluid task surfaces
- Status: carted
- Priority: high
- Kind: idea
- Scope: adaptive interaction design
- Point: Project the current task graph, artifact, and decision point into task-appropriate typed views and request-input controls within a stable conversational shell.
- Why: Users should be able to understand and steer different kinds of work without translating everything into generic chat or navigating a different arbitrary application for every task.
- Checkout target: future interaction-design research brief
- Acceptance: Several task families demonstrate clearer comprehension and lower-friction input through a trusted component grammar, with free-form chat, accessibility, provenance, and user override preserved.
- Links: `docs/business/meeting-mode-to-manufacturing-vision.md`

### IC-029 - Adaptive task-native blueprints
- Status: carted
- Priority: high
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

## Checkout Log

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
