# 54-1: DAN Conversation Controller And Routing Strangler

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** not-started
**Goal:** Introduce a durable top-level `DANConversationController` that owns routing and user-facing control decisions above specialist organisms and operator lanes, while keeping gateway and CLI surfaces mostly transport-only and making the recurrent supervision loop explicit.

## Tasks

- [ ] 1. Freeze the new top-level DAN-v2 control contract
  - [ ] 1-1. Define the primary turn decision schema (`respond`, `clarify`, `delegate_research`, `delegate_code`, `delegate_workflow`, `delegate_incident`, `delegate_operator`)
  - [ ] 1-2. Define the shared DAN facts/context packet passed to that controller
  - [ ] 1-3. Include current surface, workspace root, platform, approval mode, and available adapters/tool families in that packet
  - [ ] 1-4. Define `SupervisorBrief` fields such as `why_now`, `requested_delta`, `success_test`, `avoid`, and `stop_and_ask_if`
  - [ ] 1-5. Define `WorkerReport` fields such as `what_changed`, `evidence`, `artifacts`, `blockers`, `confidence`, and `best_next_question`
  - [ ] 1-6. Define `ReviewDecision` fields such as `continue`, `sharpen`, `redirect`, `escalate`, or `stop`, plus the next brief delta when continuing
- [ ] 2. Build the durable controller on `DurableAgentRunner`
  - [ ] 2-1. Add `src/dan/worker/organisms/dan_conversation.py`
  - [ ] 2-2. Reuse structured output contracts and hedge behavior where they already work for `dan code` / `dan research`
  - [ ] 2-3. Persist prior brief/report/decision triples so resumed loops inherit direction instead of restarting vaguely
- [ ] 3. Add a bounded routing / intent organ or equivalent typed worker seam under the controller
  - [ ] 3-1. Keep route decisions explicit and inspectable rather than hidden inside gateway or concierge glue
  - [ ] 3-2. Keep routing narrow enough that specialist organisms or operator lanes still own actual work
  - [ ] 3-3. Add a review/sequencing seam that can sharpen, redirect, escalate, or stop after each bounded run
- [ ] 4. Define the transport strangler path
  - [ ] 4-1. Make gateway/router and CLI call the new controller without copying DAN-specific routing logic into each surface
  - [ ] 4-2. Preserve a fallback path while the new route is still incomplete
  - [ ] 4-3. Add one controller-selection seam so supported surfaces can target DAN-v1 or DAN-v2 without forking the downstream substrate
  - [ ] 4-4. Start with one temporary `.env` / config selector for testing (for example `DAN_CONTROL_PLANE=v1|v2`) before deciding whether a visible frontend toggle is still useful later
- [ ] 5. Prove the new controller on a small exact benchmark set
  - [ ] 5-1. Chat/meta/status turn
  - [ ] 5-2. Research delegation turn
  - [ ] 5-3. Coding delegation turn
  - [ ] 5-4. Local operator delegation turn (files/shell/git)
  - [ ] 5-5. Browser or desktop/messaging delegation turn
  - [ ] 5-6. Incident delegation turn stub
  - [ ] 5-7. At least one 2-3 loop recurrent benchmark where each pass asks for a sharper delta and the controller stops honestly when no additional leverage remains

## Decisions

- The top-level DAN controller should stay tool-free by default; it routes and synthesizes rather than directly acting on the workspace.
- Routing is a durable-controller responsibility, not a gateway-specific or adapter-specific responsibility.
- The controller should reuse the same structured-output repair philosophy already proven in coding/research controllers.
- General local/web/browser/messaging work should route through an explicit operator lane or organism, not through ad hoc top-level tool picks hidden inside the controller.
- "Motivating" supervisory guidance should be concrete direction-setting (`why this matters now`, `what exact delta to produce`, `when to stop`) rather than generic encouragement.
- The DAN-v1 vs DAN-v2 switch should live at one routing seam near the surface/controller boundary, not as duplicated logic spread across gateway, CLI, adapters, and worker runtime code.
- The first selector can be a temporary `.env` switch so migration/testing stays simple before adding any stronger UX around it.

## Notes

- This is the first cut in the strangler rewrite: move “who should handle this turn?” into one durable universal-agent seam.
- It should explicitly not absorb implementation work that belongs in specialist organisms or bounded operator lanes.
- The desired loop shape is `intent down -> bounded work -> evidence up -> review decision -> sharper next brief`.
