# 2: Frontend Hardening

**Status:** not-started
**Goal:** Harden DAN's frontend around mode isolation, chat/history reliability, honest desktop-vs-browser capability gating, clearer Research/Development UX, and durable performance/test guardrails.

## Context

Recent frontend review findings that motivate this plan:

1. **Mounted-but-hidden modes still stay live.** `AppShell` keeps visited modes mounted to preserve per-workspace UI state, but some mode-local shortcuts and listeners still fire while the mode is hidden. The sharpest issue is Research mode shortcut leakage after the user has opened Research once.
2. **Development mode is only partially real outside Electron.** Browser preview still renders most of the IDE shell even though file I/O, git, LSP, ripgrep, file watch, terminal, and debug paths fall back to inert browser stubs.
3. **`ModeChatSidebar` is overloaded.** The shared sidebar mixes local draft persistence, backend thread persistence, tool streaming, file-review capture, terminal handoff, and mode-specific context assembly inside one large component.
4. **Research mode has grown into a monolith.** It owns layout, training/session orchestration, SSE wiring, panel toggles, terminal coupling, and duplicated chat affordances in one large file.
5. **Performance and regression coverage lag the complexity.** The build currently emits oversized chunk warnings, and frontend tests cover the shared sidebar more than the mode shells that depend on it.

This plan is the umbrella for the next hardening pass. Functional correctness and trust come first; performance and coverage follow once the shell boundaries are cleaned up.

## UI Brief

- **Product surface:** DAN desktop shell across Chat, Development, Research, and Operations
- **Audience:** daily users who switch modes frequently and expect reliable muscle memory
- **Primary action:** continue work without relearning controls or wondering which surface is authoritative
- **Dominant concept:** a calm cockpit — one dominant work surface, one clearly secondary support surface, and no decorative chrome pretending to be useful
- **Constraints:** preserve the existing workspace-tabs + mode-bar architecture, stay Electron-first, and avoid turning this into a full visual rebrand
- **Anti-goals:** duplicate chat entry points, hidden background behavior, fake-active browser controls, or generic "cleanup" that ignores user trust

## Visual System

- **Hierarchy:** artifact title and active work state first; panel controls and metadata second; diagnostic/status copy quiet until it matters
- **Palette discipline:** neutral shell by default; blue for conversation/focus, green for live/runtime success, amber only for degraded or desktop-required states
- **Surface treatment:** one dominant pane, at most one secondary rail/drawer, and utility chrome that earns its space
- **Motion:** short, quiet panel transitions; no surprise remounts or layout jumps
- **Control style:** prefer labeled toggles for ambiguous drawers and side panels; reserve icon-only controls for high-confidence, mode-local muscle memory

## No-Change Zones

- Keep the core shell architecture: workspace tabs, mode bar, and distinct layout modes.
- Keep the mode identities distinct; hardening should make Chat, Development, and Research clearer, not blur them together.
- Do not use this plan as a pretext for a broad visual redesign unrelated to the identified trust/usability issues.

## Build Slices

- **Slice 1:** invisible correctness — mode-shell isolation and hidden-mode inactivity
- **Slice 2:** stable sidecar conversation — compact/full chat history and handoff reliability
- **Slice 3:** honest Development surface — desktop/browser capability gating and placeholder removal
- **Slice 4:** scholar's desk refinement — Research-mode UX clarity and structural split
- **Slice 5:** durable speed and safety — performance budgets, lazy-loading, and regression coverage

## Tasks

- [ ] 1. Define the hardening sequence and acceptance gates
  - [ ] 1-1. Treat mode-shell isolation and hidden-mode inactivity as the first gate
  - [ ] 1-2. Treat chat/history reliability and desktop/browser honesty as the second gate
  - [ ] 1-3. Treat performance and expanded test coverage as rollout gates after the functional hardening lands
- [ ] 2. Execute the mode-shell and state-isolation work in [2-1-mode-shell-isolation](2-1-mode-shell-isolation.md)
- [ ] 3. Execute the chat/history decomposition work in [2-2-chat-sidebar-and-history](2-2-chat-sidebar-and-history.md)
- [ ] 4. Execute the Development-mode capability-gating work in [2-3-development-mode-honesty](2-3-development-mode-honesty.md)
- [ ] 5. Execute the Research-mode UX and structure work in [2-4-research-mode-ux-hardening](2-4-research-mode-ux-hardening.md)
- [ ] 6. Execute the performance and regression guardrail work in [2-5-performance-and-regression-coverage](2-5-performance-and-regression-coverage.md)
- [ ] 7. Re-run a focused frontend hardening review after the subplans land
  - [ ] 7-1. Confirm the original review findings are either fixed or explicitly deferred with reasons
  - [ ] 7-2. Verify the shell still feels coherent across Chat, Development, Research, and Operations

## Critique Gate

- **Predictability:** a hidden mode never behaves as if it were still in front of the user
- **Honesty:** desktop-only capabilities look desktop-only; preview surfaces do not impersonate production tools
- **Hierarchy:** every mode has an obvious primary pane and quieter support surfaces
- **Distinctiveness:** Research should feel like a scholar's desk, Development like a serious IDE, and Chat like the conversation home
- **Coherence:** compact and full chat feel like the same product, not parallel implementations

## Decisions

- Keep this as a top-level UI plan (`2-*`) with only one level of subplans.
- Sequence the work from correctness/trust first to performance/test expansion second.
- Treat Electron desktop as the primary capability surface, but make browser preview explicit and truthful rather than silently degraded.

## Notes

- Validation snapshot from the review that motivated this plan: `cd editor && npm test` passed (`38` files / `195` tests), `cd editor && npm run build` passed with the existing Node `20.17.0` vs Vite `20.19+` warning and oversized chunk warnings.
- The highest-priority concrete bug to fix first is Research shortcut leakage after the mode has been opened once.
- The secondary visual goal is restraint: this plan should remove ambiguity and clutter before it adds any new chrome.
