# 50-8: Frontend Sink Reduction and Pruning

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** completed
**Goal:** Reduce oversized frontend sinks, pin ownership for chat/run/graph state, and prune redundant code in touched areas without hurting responsiveness, reconnect behavior, or bundle budgets.

## Dependencies

- **50-1** defines the key frontend scripts and guardrails.
- Prefer backend seam stabilization from **50-2** through **50-7** before moving UI ownership around the same contracts. 50-6 and 50-7 may change backend APIs that frontend stores consume (workflow-generation phases, graph-mutator surface, Worker execution defaults).

## Tasks

- [x] 1. Narrow `ChatPanel.tsx`
  - [x] 1-1. Separate presentation from stream lifecycle, reconnect/handoff logic, and thread persistence coordination. Landed extraction targets: `useChatPanelTransport.ts` (chat/run stream lifecycle, reconnect, handoff, disconnect recovery) and `useChatPanelThreadPersistence.ts` (thread persistence coordination plus direct save helper).
  - [x] 1-2. Keep `ChatPanel` focused on composition/rendering and local UI decisions.
- [x] 2. Narrow `useGraphStore.ts`
  - [x] 2-1. Move non-graph concerns (chat/run/history/tab/session spillover) into more natural store/helper boundaries. Landed helper boundaries: `graphSessionState.ts` (tab snapshots, persisted tab-session state, repeated reset slices) and `graphPanelFocus.ts` (chat/panel focus and run-log hydration helpers).
  - [x] 2-2. Keep `useGraphStore` focused on graph/editor/workflow-tab state.
- [x] 3. Evaluate one more justified frontend hotspot
  - [x] 3-1. Use the 50-1 inventory to choose one additional frontend or Electron bridge sink only if the ownership improvement is real. Decision: stop after the `ChatPanel.tsx` and `useGraphStore.ts` ownership reductions; `ConfigPanel.tsx` / `useMessagingStore.ts` stay untouched because the incremental payoff did not justify more churn.
- [x] 4. Prune redundant code
  - [x] 4-1. Remove stale helpers, duplicate state bridges, or compatibility code that becomes unnecessary after the splits.
  - [x] 4-2. Prefer deletion and tighter store ownership over adding more cross-store wrapper logic.
- [x] 5. Guard performance and UX
  - [x] 5-1. Revalidate bundle budgets (`npm run bundle:check`), Vitest coverage, and stream reconnect/thread persistence behavior.
  - [x] 5-2. Check that the splits do not regress render responsiveness or startup/restore behavior.
  - [x] 5-3. Audit Zustand selector boundaries after store splits to confirm that new store boundaries do not introduce unnecessary React re-renders or break existing memoization patterns. The split stayed helper-based: no new Zustand store was introduced, the public `useGraphStore` state shape stayed intact, and `ChatPanel` transport/persistence moved into hooks without adding extra mirrored state.

## Primary Files

- `editor/src/components/ChatPanel.tsx`
- `editor/src/store/useGraphStore.ts`
- additional frontend/Electron hotspot selected by the 50-1 inventory

## Success Criteria

- at least two oversized frontend owners are materially narrower and easier to reason about
- chat/run reconnect behavior and thread persistence remain correct
- touched areas lose redundant code rather than gaining more indirection
- bundle/performance guardrails remain green
- no observable re-render regressions from store splits (verified by profiler spot-check)

## Decisions

- The frontend should gain clearer ownership, not more state mirrors.
- A third split target is optional and should only happen if the structural payoff is obvious.
- This pass stops after the two required ownership reductions; `ConfigPanel.tsx` / `useMessagingStore.ts` stay untouched because the current payoff did not justify more churn without a working frontend test/build toolchain in the worktree.

## Notes

- This plan is intentionally later in the sequence because frontend ownership is easier to stabilize once the backend seams stop moving underneath it.
- Candidate component/hook/store names in tasks 1 and 2 are starting points; final names may change at execution time, but the responsibility boundaries should match.
- The isolated child worktree could not validate because `editor/node_modules` was missing there, so the final verification happened in the main workspace instead.
- 2026-04-07: the main-workspace follow-up validated the landed split with `npm --prefix editor run test -- --run src/store/__tests__/graphPanelFocus.test.ts src/store/__tests__/runLogLoading.test.ts src/store/__tests__/useMessagingStore.test.ts` (`16 passed`) and `npm --prefix editor run build:verify` (passed). The production build emitted the existing Vite chunk-size warnings plus a Node-version advisory (`20.17.0` vs Vite's recommended `20.19+`), but bundle budgets still passed and the extracted editor slices remained under budget.
