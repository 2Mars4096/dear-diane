# Archived Desktop Surfaces

The shipped desktop entry point is the Super DAN universal-cell workspace:

- route: `#workspace` (`#work` and `#chunks` normalize to it)
- active surface: `src/components/workspace/ChunkWorkspaceApp.tsx`
- active runtime selector: Super DAN only
- integrated workspace areas: Work and Notes

The earlier multi-mode shell is retained as inactive source history while the
workspace cutover is validated. It is not reachable from `src/App.tsx` and the
bundle gate fails if any of these surfaces leak into a production build:

- `src/components/shell/AppShell.tsx`
- `src/components/v2/ChatV2App.tsx`
- `src/components/modes/OperationsMode.tsx`
- `src/components/GraphCanvas.tsx`
- the legacy Code and Research mode shells

Keeping the inactive source temporarily makes the archive reversible without
shipping the old Operations/network product. Delete or extract it only after
the workspace acceptance battery and migration window are complete.
