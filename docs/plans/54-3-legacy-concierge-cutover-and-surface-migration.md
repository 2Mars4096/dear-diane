# 54-3: Legacy Concierge Cutover And Surface Migration

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Define the coexistence, cutover, and retirement path for legacy concierge-first DAN routing as top-level surfaces migrate onto durable universal-agent controllers.

## Tasks

- [x] 1. Inventory the current top-level DAN control seams that still depend on concierge / dispatcher / chat-manager routing
  - [x] 1-1. Gateway entry path
  - [x] 1-2. CLI / chat entry paths
  - [x] 1-3. Messaging adapters and external surfaces
  - [x] 1-4. Surface-specific fallback or adapter-owned routing
- [x] 2. Define migration phases by surface
  - [x] 2-1. Run DAN-v2 in shadow or opt-in mode first
  - [x] 2-2. Add one reversible controller selector (`DAN-v1` vs `DAN-v2`) for the surfaces under active migration; begin with a temporary `.env` switch such as `DAN_CONTROL_PLANE=v1|v2`, then add a frontend dev/operator toggle only if it still pays for itself
  - [x] 2-3. Keep both DAN-v1 and DAN-v2 working against the shared substrate while the selector exists; migration is not complete if either side silently rots
  - [x] 2-4. Promote internal CLI/desktop/chat surfaces before external messaging or higher-risk computer-control surfaces
  - [x] 2-5. Keep clear fallback rules when DAN-v2 cannot yet handle a route
- [x] 3. Define the cutover criteria
  - [x] 3-1. Benchmark parity or better on frozen routing scenarios
  - [x] 3-2. No regression on specialist delegation to `dan code` / `dan research`
  - [x] 3-3. No regression on approval/audit boundaries for browser, desktop, and messaging actions
  - [x] 3-4. Honest observability and rollback path
  - [x] 3-5. One-step rollback to DAN-v1 at the controller-selection seam without needing to revert shared substrate changes
  - [x] 3-6. Explicit test coverage or acceptance checks prove both selector targets still function until DAN-v1 is formally retired
- [x] 4. Define what remains concierge-owned versus what is retired
  - [x] 4-1. Remove duplicated routing logic once DAN-v2 owns it
  - [x] 4-2. Retain only the parts that still serve as reusable infrastructure rather than top-level brain logic

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
- Landed third slice: `/api/chat/message` now accepts a request-scoped `control_plane_mode=v1|v2` override in addition to the global env selector, returns the actually selected mode plus whether it came from `env` or `request`, and persists the selected mode in thread metadata. That makes internal chat surfaces and future frontend toggles able to promote or roll back per surface without flipping every migrated surface at once.
- Landed fourth slice: remote/local chat clients now honor `DAN_CLI_CONTROL_PLANE` / `DAN_CHAT_CONTROL_PLANE` and forward that choice as a request-scoped override, while adapter entry paths now honor `DAN_ADAPTERS_CONTROL_PLANE` plus surface-specific env keys such as `DAN_TELEGRAM_CONTROL_PLANE` or `DAN_WECHAT_CONTROL_PLANE`. That gives `54-3` a real internal-first migration lever instead of one repo-wide flip.
- Landed fifth slice: the server chat router now owns a broader surface-policy tier above the global selector. `/api/chat/message` resolves `DAN_<SURFACE_TYPE>_CONTROL_PLANE` plus `DAN_INTERNAL_CONTROL_PLANE` / `DAN_EXTERNAL_CONTROL_PLANE` before falling back to `DAN_CONTROL_PLANE`, and now persists both `selected_mode` and `selected_mode_source` in thread metadata. The remaining direct caller gaps were also narrowed: the editor `ChatPanel` now posts canonical `editor:*` surface identity, and `src/dan/cli/adapter.py` now canonicalizes adapter chat posts to `surface + surface_type + surface_id` so surface rollout stays server-owned instead of being split across ad hoc caller behavior.
- Landed sixth slice: direct-post external adapters now converge on the same selector stack as the in-process adapter bridge. `/api/chat/message` now also treats `DAN_ADAPTERS_CONTROL_PLANE` as the adapter-group override for external surfaces before the broader `DAN_EXTERNAL_CONTROL_PLANE` fallback, and `src/dan/adapters/telegram_fleet.py` now posts explicit `surface_type="telegram"` plus `surface_id=bot.name` instead of relying only on the composite `surface` string. That closes the last known mismatch where bridged adapters honored `DAN_ADAPTERS_CONTROL_PLANE` but some direct adapter posts did not.
- Landed seventh slice: the in-process adapter concierge bridge no longer owns a separate v1-vs-v2 routing branch. `src/dan/server/routers/adapters.py::_run_adapter_concierge(...)` now always builds one canonical chat request and routes it through `chat_message(..., concierge=True)`, then relays the shared local chat stream for both DAN-v1 and DAN-v2. That removes one more adapter-owned top-level decision seam and lets queued legacy replies flow through the same `iter_local_chat_stream_events(...)` path as direct DAN-v2 replies.
- Landed eighth slice: `src/dan/cli/chat_local.py::LocalChatRuntime` no longer carries its own local legacy routing engine or separate `/run` stream plumbing. It now always builds one canonical `ChatMessageRequest`, routes local chat through `chat_message(..., concierge=True)`, and follows both `chat-*` and `run-*` channels through the same `iter_local_chat_stream_events(...)` seam as the server router. That removes another duplicated top-level routing branch from the local surface while keeping the local CLI selector/env overrides intact.
- Landed ninth slice: scheduled/background chat dispatch no longer constructs `SurfaceMessage` directly. `src/dan/server/startup/__init__.py` (and the mirrored `src/dan/server/startup.py`) now build one canonical scheduled `ChatMessageRequest`, route it through `chat_message(..., concierge=True)`, and collapse the shared local stream back into terminal text. The same `collect_terminal_content(...)` helper now accepts both legacy event objects and router-style dict events so scheduled actions can reuse the shared router seam without changing the scheduler contract.
- Landed tenth slice: specialist delegation now has explicit DAN-v2 regression coverage on both product lanes. `tests/test_server/test_control_plane_runtime.py` now covers Research-lane handoff parity alongside the existing Code-lane cases, and `tests/test_concierge/test_chat_router.py` now proves that a DAN-v2 Research handoff still reaches the shared legacy execution path with the expected supervisor brief/prompt context. That closes the previously uncovered half of cutover criterion `3-2`.
- Landed eleventh slice: the approval/audit envelope now survives DAN-v2 specialist handoff too. `src/dan/server/control_plane.py` no longer hardcodes `approval_mode="server"` or empty tool availability inside the Code/Research lane contexts; it now carries the request’s approval mode plus tool envelope into both specialist controllers and the direct-code runner/session metadata. `tests/test_server/test_control_plane_runtime.py` and `tests/test_concierge/test_chat_router.py` now also prove that a confirm-risky messaging-surface request preserves its browser/desktop/adapter boundary metadata through DAN-v2 Research/Code shaping and back into the shared legacy execution path, which closes criterion `3-3`.
- Landed twelfth slice: the remaining cutover proof and retirement boundary are now explicit. `tests/test_concierge/test_chat_router.py` adds a frozen routing matrix that proves representative global/request/surface/internal/adapter selector scenarios persist the same selected mode/source they returned and still hit the expected DAN-v1 vs DAN-v2 path, which closes criterion `3-1`. `src/dan/server/routers/chat.py` now resolves the selected control plane once per request and reuses that same object for immediate response metadata, thread persistence, and DAN-v2 handoff metadata, so the shared router keeps one top-level selector branch while the legacy runtime remains only a reusable execution substrate. That closes `4-1` and `4-2`.
