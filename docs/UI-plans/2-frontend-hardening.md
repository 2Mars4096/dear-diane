# 2: Frontend Hardening

**Status:** in-progress
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

- [x] 1. Define the hardening sequence and acceptance gates
  - [x] 1-1. Treat mode-shell isolation and hidden-mode inactivity as the first gate
  - [x] 1-2. Treat chat/history reliability and desktop/browser honesty as the second gate
  - [x] 1-3. Treat performance and expanded test coverage as rollout gates after the functional hardening lands
- [ ] 2. Execute the mode-shell and state-isolation work in [2-1-mode-shell-isolation](2-1-mode-shell-isolation.md)
- [ ] 3. Execute the chat/history decomposition work in [2-2-chat-sidebar-and-history](2-2-chat-sidebar-and-history.md)
- [ ] 4. Execute the Development-mode capability-gating work in [2-3-development-mode-honesty](2-3-development-mode-honesty.md)
- [ ] 5. Execute the Research-mode UX and structure work in [2-4-research-mode-ux-hardening](2-4-research-mode-ux-hardening.md)
- [x] 6. Execute the performance and regression guardrail work in [2-5-performance-and-regression-coverage](2-5-performance-and-regression-coverage.md)
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
- First implementation round landed on 2026-04-02: active-mode window listeners now share a dedicated hook, Research shortcut leakage is fixed, Development browser preview now routes preview-only panels through an honest handoff surface, Workflow/Furnace sidebars in Development are now real data-backed panels instead of placeholders, Research and Development both use quieter status strips instead of duplicate top-bar chat buttons, and compact mode chat now has clearer sidecar labeling/empty-state copy.
- Second implementation slice also landed on 2026-04-02 under `2-2`: compact mode chat now scopes local draft/session persistence by `workspace + mode + workflow`, switches sidecar state cleanly when the active workflow changes, keeps legacy fallback only for `_scratch`, and carries explicit workflow labeling in history/empty states so the sidecar feels like a trustworthy slice of the same conversation system.
- Third implementation slice landed on 2026-04-02 across `2-3`, `2-4`, and `2-5`: Development shell support now lives in smaller files (`DevelopmentModeShell.tsx`, `useDevelopmentModeShortcuts.ts`, `developmentModeChatContext.ts`, `useDebugEvents.ts`), Research extracted its shortcut/status/chat-context support into dedicated modules, Development and Research both gained focused mode-level tests, and the old DebugPanel static-vs-dynamic import conflict is gone.
- Fourth implementation slice also landed on 2026-04-02 under `2-4`: the Research shell panels now live in `ResearchModeShell.tsx`, so `FunctionRail`, `PipelineProgress`, `PrimaryPanel`, and `ContextPanel` are no longer embedded inside `ResearchMode.tsx`. Focused shell regressions now cover those extracted panels directly.
- Fifth implementation slice landed on 2026-04-02 across `2-4` and `2-5`: `useResearchFurnaceSessions.ts` now owns Research-mode backend session polling, SSE stream ownership, and reconnect scheduling so that live training state is no longer wired inline inside `ResearchMode.tsx`, and the extracted hook has focused reconnect coverage in `useResearchFurnaceSessions.test.ts`.
- Sixth implementation slice landed on 2026-04-02 under `2-2`: `useModeChatSidebarNativeActions.ts` now owns Development-only shell-command handoff and reviewable file-edit capture, so `ModeChatSidebar.tsx` no longer keeps those native side effects inline. Compact chat also only shows shell `Run` affordances when that Development adapter is actually active.
- Seventh implementation slice landed on 2026-04-02 under `2-2`: `useModeChatSidebarTransport.ts` now owns the compact sidecar's send/stream lifecycle, queued-message injection, request-side context assembly, and polling fallback, while `modeChatSidebarTypes.ts` holds the shared compact-chat transport types. `ModeChatSidebar.tsx` is now materially thinner and more shell-focused, and compact-chat regressions now include workspace-scoped restore in addition to workflow-scoped restore and full-chat handoff.
- Eighth implementation slice landed on 2026-04-02 under `2-4`: `researchFurnaceSessionStatus.ts` now gives Furnace cards an explicit lifecycle vocabulary (`Ready`, `Live`, `Paused`, `Reconnecting`, `Completed`, `Failed`) instead of raw status text, and `researchFurnaceSessionStatus.test.ts` adds focused coverage for those semantics alongside the existing Research shell/session tests.
- Ninth implementation slice landed on 2026-04-02 under `2-4`: `ResearchFurnaceSessionCards.tsx` now owns the Furnace family/session card surface, including tag editing, recipe reveal, and per-session action wiring, so `ResearchMode.tsx` no longer carries that entire session-card block inline. Focused coverage now also includes `ResearchFurnaceSessionCards.test.ts`.
- Tenth implementation slice landed on 2026-04-02 across `2-4` and `2-5`: `ResearchFurnacePanel.tsx` now owns the full Furnace desk, `ResearchMode.tsx` is down to shell-layout composition plus a lazy-loaded Furnace entry point, and Research session syncing now sits at the mode level with an explicit `activeMode === "research"` gate so editor/reader/furnace switches keep live state coherent without leaving hidden-mode work running. The new Furnace desk also gives the composer and session desk visibly different lanes, clearer first-use copy, and dedicated tests in `ResearchFurnacePanel.test.ts`.
- Eleventh implementation slice landed on 2026-04-02 under `2-5`: `editor/scripts/check-bundle-budgets.mjs` now enforces explicit post-build size ceilings for the main heavy frontend chunks, and `editor/package.json` now exposes `npm run bundle:check` plus `npm run build:verify` so the current Research/Code/Monaco payload expectations are measurable guardrails instead of only build-log warnings.
- Twelfth implementation slice landed on 2026-04-02 under `2-5`: `editor/vite.config.ts` now splits PDF and KaTeX vendor code into separate chunks instead of one shared `research` blob, so Research mode no longer loads both toolchains together by default. `bundle:check` now tracks `pdf` and `katex` directly, and the build now leaves the remaining warning pressure mostly on Monaco and its workers rather than the Research surface.
- Thirteenth implementation slice landed on 2026-04-02 under `2-2`: `useModeChatSidebarHistory.ts` now owns compact-chat thread list loading, thread restore, new-chat reset, clear/reset semantics, and explicit compact-to-full-chat handoff preparation. That removes the last large history/handoff block from `ModeChatSidebar.tsx`, leaving the shared sidecar much closer to a true shell plus composer surface.
- Fourteenth implementation slice landed on 2026-04-03 across `2-1`, `2-4`, and `2-5`: the review-follow-up patch fixed the ConfigPanel/edge-lint test typing so `build` and `build:verify` are green again, `PortRow` now commits JSON schemas on blur instead of every valid keystroke, `useModeScopedWindowEvent.ts` now keeps a ref-backed latest callback so inline mode-shell listeners stay stable across renders, `ResearchMode.tsx` / `CodeMode.tsx` / `OperationsMode.tsx` now pass stable chat/context callbacks, `DebugConsole.tsx` is split out of `DebugPanel.tsx`, and `check-bundle-budgets.mjs` now warns on missing chunks while still failing true budget regressions.
- Current validation after the latest pass: `cd editor && npm test` passed (`50` files / `231` tests), `cd editor && npm run build` passed, and `cd editor && npm run bundle:check` passed. The Node `20.17.0` warning and the intentionally heavy Monaco/worker payloads remain as follow-up work.
- Review-follow-up validation on 2026-04-03: `cd editor && npm run test -- --run src/components/__tests__/ConfigPanel.test.ts src/hooks/__tests__/useModeScopedWindowEvent.test.ts`, `cd editor && npm run build`, and `cd editor && npm run build:verify` all pass. The Node `20.17.0` warning still remains, but the frontend hardening follow-up itself is green.
