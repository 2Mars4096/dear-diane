# 57: Chat / Agent V2 Control Plane

**Status:** not-started
**Goal:** Build a cleaner V2 product plane with only two user-facing modes, Chat and Agent, while leaving the current editor, modes, and legacy chat/control surfaces cleanly present but unwired from the new path.

## Tasks
- [ ] 1. Define the two-mode product contract via [57-1-chat-agent-product-contract.md](57-1-chat-agent-product-contract.md)
- [ ] 2. Split conversation handling from task execution via [57-2-slim-chat-manager-v2.md](57-2-slim-chat-manager-v2.md)
- [ ] 3. Expose Super-DAN-style Agent runs through a backend control plane via [57-3-agent-run-control-plane.md](57-3-agent-run-control-plane.md)
- [ ] 4. Normalize Agent profiles and hyperparameters via [57-4-agent-profile-policy.md](57-4-agent-profile-policy.md)
- [ ] 5. Decide and implement the organism backend bridge via [57-5-organism-backend-bridge.md](57-5-organism-backend-bridge.md)
- [ ] 6. Wire the lightweight V2 frontend through the UI companion plan [../UI-plans/5-chat-agent-v2-shell.md](../UI-plans/5-chat-agent-v2-shell.md)
- [ ] 7. Keep legacy surfaces present but isolated
  - [ ] 7-1. Leave existing Chat, Research, Development, Content, Operations, and graph editor routes available
  - [ ] 7-2. Avoid routing V2 Agent runs through old mode-specific frontend state
  - [ ] 7-3. Keep old `/api/chat/message` behavior stable until V2 endpoints are proven
- [ ] 8. Add observability, persistence, and rollback gates
  - [ ] 8-1. Persist Agent runs separately from chat messages while linking final results back into the thread
  - [ ] 8-2. Stream task events with reconnect semantics equivalent to chat streams
  - [ ] 8-3. Add a feature flag or alternate route so V2 can be disabled without touching legacy flows
  - [ ] 8-4. Add focused server/frontend regressions before making V2 the default

## Decisions
- The user-facing product modes are only **Chat** and **Agent**.
- Chat is for fast conversation, explanation, and inspection. It should not own worker orchestration.
- Agent is for bounded task execution. It owns planning, worker allocation, parallelism, validation, repair, and final reporting.
- Super DAN is the proving implementation for the first Agent path because its live execution semantics, progress stream, validation pass, and hook/inbox state already behave closest to the desired product.
- Agent knobs should be profile-first (`Fast`, `Balanced`, `Deep`, `Max`) rather than exposing raw agent counts as the primary UI.
- Raw hyperparameters still exist internally: agent count, parallelism cap, worker timeout, tool-call budget, repair rounds, validator count, planner depth, artifact partitioning policy, write permission, and approval policy.
- The current heavy editor remains available. V2 grows beside it and should not require deleting or rewiring existing modes before it proves itself.

## Notes
- This plan follows Plan 56 rather than replacing it. Plan 56 makes organism execution more universal; Plan 57 turns that organism substrate into a simpler product/control-plane boundary.
- Plan 55 scheduler work supplies the reusable agent-count, parallelism, artifact partition, readiness, and validation policy vocabulary that Agent profiles should consume.
- Initial implementation should prefer additive endpoints such as `/api/agent-runs` over changing `/api/chat/message` semantics in-place.
- The organism bridge is intentionally separated from the public Agent API so the first backend can be Super DAN while the long-term backend migrates toward universal organism facades.
- The desired request shape is:
  `user turn -> ChatManagerV2 -> chat response OR AgentControlPlane task run -> final result linked back to chat`.
- Legacy mode surfaces may still inspect Agent logs later, but they should not be required for the V2 happy path.
