# 50-8: Frontend Sink Reduction and Pruning

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** not-started
**Goal:** Reduce oversized frontend sinks, pin ownership for chat/run/graph state, and prune redundant code in touched areas without hurting responsiveness, reconnect behavior, or bundle budgets.

## Dependencies

- **50-1** defines the key frontend scripts and guardrails.
- Prefer backend seam stabilization from **50-2** through **50-7** before moving UI ownership around the same contracts. 50-6 and 50-7 may change backend APIs that frontend stores consume (workflow-generation phases, graph-mutator surface, Worker execution defaults).

## Tasks

- [ ] 1. Narrow `ChatPanel.tsx`
  - [ ] 1-1. Separate presentation from stream lifecycle, reconnect/handoff logic, and thread persistence coordination. Expected extraction targets: `useChatStream.ts` (stream lifecycle, SSE/WebSocket handling, reconnect), `useChatReconnect.ts` (reconnect/handoff logic, stale-stream detection), `useThreadPersistence.ts` (thread persistence coordination, save/restore), `ChatMessageList.tsx` (message rendering, virtualization, scroll management).
  - [ ] 1-2. Keep `ChatPanel` focused on composition/rendering and local UI decisions.
- [ ] 2. Narrow `useGraphStore.ts`
  - [ ] 2-1. Move non-graph concerns (chat/run/history/tab/session spillover) into more natural store/helper boundaries. Candidate destination stores: `useChatSessionStore.ts` (chat session state, thread selection, active conversation), `useRunHistoryStore.ts` (run history, run status tracking, log references), `useWorkspaceTabStore.ts` (tab/workspace session state that is not graph-specific).
  - [ ] 2-2. Keep `useGraphStore` focused on graph/editor/workflow-tab state.
- [ ] 3. Split one more justified frontend hotspot
  - [ ] 3-1. Use the 50-1 inventory to choose one additional frontend or Electron bridge sink only if the ownership improvement is real. Leading candidates at plan start: `ConfigPanel.tsx` (2982 lines — the 2nd-largest frontend file, mixes node/edge config editing, schema validation, and form rendering) and `useMessagingStore.ts` (1424 lines — messaging/adapter state that may spill into graph-store concerns).
- [ ] 4. Prune redundant code
  - [ ] 4-1. Remove stale helpers, duplicate state bridges, or compatibility code that becomes unnecessary after the splits.
  - [ ] 4-2. Prefer deletion and tighter store ownership over adding more cross-store wrapper logic.
- [ ] 5. Guard performance and UX
  - [ ] 5-1. Revalidate bundle budgets (`npm run bundle:check`), Vitest coverage, and stream reconnect/thread persistence behavior.
  - [ ] 5-2. Check that the splits do not regress render responsiveness or startup/restore behavior.
  - [ ] 5-3. Audit Zustand selector boundaries after store splits to confirm that new store boundaries do not introduce unnecessary React re-renders or break existing memoization patterns. Verify with React DevTools Profiler or equivalent spot-checks on the chat/graph hot paths.

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

## Notes

- This plan is intentionally later in the sequence because frontend ownership is easier to stabilize once the backend seams stop moving underneath it.
- Candidate component/hook/store names in tasks 1 and 2 are starting points; final names may change at execution time, but the responsibility boundaries should match.
