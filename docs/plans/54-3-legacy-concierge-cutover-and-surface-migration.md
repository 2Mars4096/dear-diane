# 54-3: Legacy Concierge Cutover And Surface Migration

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** in-progress
**Goal:** Define the coexistence, cutover, and retirement path for legacy concierge-first DAN routing as top-level surfaces migrate onto durable universal-agent controllers.

## Tasks

- [ ] 1. Inventory the current top-level DAN control seams that still depend on concierge / dispatcher / chat-manager routing
  - [ ] 1-1. Gateway entry path
  - [ ] 1-2. CLI / chat entry paths
  - [ ] 1-3. Messaging adapters and external surfaces
  - [ ] 1-4. Surface-specific fallback or adapter-owned routing
- [ ] 2. Define migration phases by surface
  - [x] 2-1. Run DAN-v2 in shadow or opt-in mode first
  - [x] 2-2. Add one reversible controller selector (`DAN-v1` vs `DAN-v2`) for the surfaces under active migration; begin with a temporary `.env` switch such as `DAN_CONTROL_PLANE=v1|v2`, then add a frontend dev/operator toggle only if it still pays for itself
  - [x] 2-3. Keep both DAN-v1 and DAN-v2 working against the shared substrate while the selector exists; migration is not complete if either side silently rots
  - [ ] 2-4. Promote internal CLI/desktop/chat surfaces before external messaging or higher-risk computer-control surfaces
  - [x] 2-5. Keep clear fallback rules when DAN-v2 cannot yet handle a route
- [ ] 3. Define the cutover criteria
  - [ ] 3-1. Benchmark parity or better on frozen routing scenarios
  - [ ] 3-2. No regression on specialist delegation to `dan code` / `dan research`
  - [ ] 3-3. No regression on approval/audit boundaries for browser, desktop, and messaging actions
  - [ ] 3-4. Honest observability and rollback path
  - [x] 3-5. One-step rollback to DAN-v1 at the controller-selection seam without needing to revert shared substrate changes
  - [x] 3-6. Explicit test coverage or acceptance checks prove both selector targets still function until DAN-v1 is formally retired
- [ ] 4. Define what remains concierge-owned versus what is retired
  - [ ] 4-1. Remove duplicated routing logic once DAN-v2 owns it
  - [ ] 4-2. Retain only the parts that still serve as reusable infrastructure rather than top-level brain logic

## Decisions

- Concierge should be strangled from the top-level “brain” role first, not ripped out of the repo before replacements are proven.
- Surface migration should be benchmark-gated, not faith-based.
- DAN-v2 should own routing and delegation; legacy concierge paths should become fallback or infrastructure only.
- External adapters such as WeChat should stay thin surface transports rather than each carrying their own top-level brain logic.
- Switching between DAN-v1 and DAN-v2 should be one routing choice at the surface/controller boundary, not a second copy of every downstream backend script.
- While the selector exists, both targets are first-class migration paths and must stay working; temporary dual-path support is intentional, not accidental drift.

## Notes

- This subplan exists to keep the rewrite disciplined. Without an explicit coexistence/cutover plan, the repo will accumulate two partial brains instead of replacing one with another.
- The general-chat-plane goal only works if every surface converges on the same controller contract instead of inventing its own routing personality.
- The reversible selector is primarily a migration/testing seam. It does not imply duplicating tools, runtimes, search, adapters, or organism internals once those are shared.
- The temporary `.env` selector should be documented as migration scaffolding and removed or hidden once DAN-v2 is the default and DAN-v1 is retired.
- Landed first slice: the server chat surface now reads `DAN_CONTROL_PLANE`, DAN-v2 can answer directly or hand off with a supervisor brief, and focused router tests prove both selector targets still work on that seam.
- Landed second slice: the local in-process chat CLI and the in-process adapter concierge bridge now honor the same selector, so the migration seam is no longer frontend-only even though delegated work still shares the legacy substrate below it.
