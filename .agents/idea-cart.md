# Idea Cart

Last reviewed: 2026-07-21

## Active Items

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

### IC-026 - Vibe-to-manufacturing compiler
- Status: carted
- Priority: high
- Kind: idea
- Scope: physical-product creation
- Point: Compile conversational product intent into reviewable, process-specific production packages rather than treating a render or generic blueprint as factory-ready.
- Why: Parts, garments, electronics, and food-contact goods require different files, validations, and approval paths.
- Checkout target: research prototype only after product-family validation
- Acceptance: One narrow, non-regulated product family can produce packages that multiple providers quote with no more than one bounded human correction cycle.
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

## Checkout Log

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
