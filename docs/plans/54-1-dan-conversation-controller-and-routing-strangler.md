# 54-1: DAN Conversation Controller And Routing Strangler

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** not-started
**Goal:** Introduce a durable top-level `DANConversationController` that owns routing and user-facing control decisions above specialist organisms, while keeping gateway and CLI surfaces mostly transport-only.

## Tasks

- [ ] 1. Freeze the new top-level DAN-v2 control contract
  - [ ] 1-1. Define the primary turn decision schema (`respond`, `clarify`, `delegate_research`, `delegate_code`, `delegate_workflow`, `delegate_incident`)
  - [ ] 1-2. Define the shared DAN facts/context packet passed to that controller
- [ ] 2. Build the durable controller on `DurableAgentRunner`
  - [ ] 2-1. Add `src/dan/worker/organisms/dan_conversation.py`
  - [ ] 2-2. Reuse structured output contracts and hedge behavior where they already work for `dan code` / `dan research`
- [ ] 3. Add a bounded routing / intent organ or equivalent typed worker seam under the controller
  - [ ] 3-1. Keep route decisions explicit and inspectable rather than hidden inside gateway or concierge glue
  - [ ] 3-2. Keep routing narrow enough that specialist organisms still own actual work
- [ ] 4. Define the transport strangler path
  - [ ] 4-1. Make gateway/router and CLI call the new controller without copying DAN-specific routing logic into each surface
  - [ ] 4-2. Preserve a fallback path while the new route is still incomplete
- [ ] 5. Prove the new controller on a small exact benchmark set
  - [ ] 5-1. Chat/meta/status turn
  - [ ] 5-2. Research delegation turn
  - [ ] 5-3. Coding delegation turn
  - [ ] 5-4. Incident delegation turn stub

## Decisions

- The top-level DAN controller should stay tool-free by default; it routes and synthesizes rather than directly acting on the workspace.
- Routing is a durable-controller responsibility, not a gateway-specific or adapter-specific responsibility.
- The controller should reuse the same structured-output repair philosophy already proven in coding/research controllers.

## Notes

- This is the first cut in the strangler rewrite: move “who should handle this turn?” into one durable universal-agent seam.
- It should explicitly not absorb implementation work that belongs in specialist organisms.
