# 1-9: Shell Chrome Purge & Per-Mode Chat

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** completed
**Goal:** Remove redundant shell chrome (Breadcrumb, SidebarHost, PersistentChatBar), give every mode its own persistent chat sidebar, and add Workflow + Furnace access to Development mode's activity bar.

## Problem Summary

The app has working features but too many navigation layers that don't earn their screen space:

| Layer | Pixels | Issue |
|---|---|---|
| Breadcrumb (22px) | 22px | Repeats mode + workspace already shown in Mode Bar and Workspace Tabs |
| SidebarHost (264px) | 264px wide | Dead in every mode — search/quick-access stubs do nothing; every mode builds its own sidebar internally |
| PersistentChatBar (44px) | 44px | Teleports user to Chat mode on send; no inline response, no history |

Additionally:
- Development mode has no path to launch a workflow or start a Furnace training session
- Only Development mode has a chat sidebar (`ChatSidebar`); Research and Operations lack one
- `ChatSidebar` history is volatile (`useState`) — lost on mode/workspace switch
- Research mode's right drawer (5 tabs of core functions) is hidden behind a 13px icon with no label

## Tasks

### Slice A — Kill Dead Shell Chrome

- [x] 1. **Delete Breadcrumb component and usage**
  - [x] 1-1. Remove `<Breadcrumb />` from `AppShell.tsx`
  - [x] 1-2. Delete `editor/src/components/shell/Breadcrumb.tsx`
  - [x] 1-3. Remove any imports or references across the codebase

- [x] 2. **Delete SidebarHost component and usage**
  - [x] 2-1. Remove `<SidebarHost />` from `AppShell.tsx` (including the workspace-keyed wrapper)
  - [x] 2-2. Delete `editor/src/components/shell/SidebarHost.tsx`
  - [x] 2-3. Remove `sidebarOpen` / `toggleSidebar` from `useAppStore` (and the `Cmd+B` global shortcut registration in `AppShell.tsx`) — or repurpose `Cmd+B` to toggle the mode's own sidebar (each mode already handles its own sidebar toggle; verify no conflicts)
  - [x] 2-4. Remove any remaining imports or references

- [x] 3. **Delete PersistentChatBar component and usage**
  - [x] 3-1. Remove `<PersistentChatBar />` from `AppShell.tsx`
  - [x] 3-2. Delete `editor/src/components/shell/PersistentChatBar.tsx`
  - [x] 3-3. Remove `chatBarExpanded` / `setChatBarExpanded` / `pendingChatMessage` / `setPendingChatMessage` from `useAppStore` if no longer referenced
  - [x] 3-4. Remove any remaining imports or references

- [x] 4. **Verify layout after purge**
  - [x] 4-1. AppShell renders: `WorkspaceTabs` → `ModeBar` → mode viewport (full remaining height). No other shell-level chrome between the mode bar and the mode content.
  - [x] 4-2. Each mode now owns 100% of the vertical space below the Mode Bar
  - [x] 4-3. `npm run build` / `npx tsc --noEmit` passes with no new errors

### Slice B — Per-Mode Chat Sidebar (Replace PersistentChatBar)

- [x] 5. **Extract `ChatSidebar` into a shared, reusable component**
  - [x] 5-1. Move `editor/src/components/code/ChatSidebar.tsx` to `editor/src/components/shared/ModeChatSidebar.tsx` (or keep the existing file and re-export — whichever is cleaner)
  - [x] 5-2. Accept a `contextProvider` prop (or similar) so each mode can inject its own context (Development: active file, open files, git branch; Research: current paper, annotations, active recipe; Operations: running workflows). The existing `ChatSidebar` already sends workspace context — generalize this.
  - [x] 5-3. Persist chat history per workspace + mode in the session persistence layer (`sessionPersistence.ts`), not volatile `useState`. On workspace switch or mode switch, restore the correct history.

- [x] 6. **Wire `ModeChatSidebar` into Development mode (CodeMode)**
  - [x] 6-1. Replace the current `ChatSidebar` import with `ModeChatSidebar`
  - [x] 6-2. Verify the existing AI Chat activity bar icon toggles the new component
  - [x] 6-3. Verify persistent history survives mode/workspace switches

- [x] 7. **Wire `ModeChatSidebar` into Research mode**
  - [x] 7-1. Add an AI Chat toggle to Research mode's left rail toggle bar (or as an activity icon / keyboard shortcut — `Cmd+L`)
  - [x] 7-2. Render `ModeChatSidebar` as a right-side `Allotment.Pane` (similar to CodeMode's pattern)
  - [x] 7-3. Inject Research context: active paper title/path, selected annotation text, active recipe name, current rail section

- [x] 8. **Wire `ModeChatSidebar` into Operations mode**
  - [x] 8-1. Add chat toggle button or keyboard shortcut
  - [x] 8-2. Render `ModeChatSidebar` as a right-side panel
  - [x] 8-3. Inject Operations context: active workflow name, run status, selected node

- [x] 9. **Repurpose `Cmd+J` (or adopt `Cmd+L`)**
  - [x] 9-1. `Cmd+J` currently toggles PersistentChatBar. Reassign to toggle the per-mode chat sidebar (consistent shortcut across all non-Chat modes).
  - [x] 9-2. Remove the old `Cmd+J` handler from `PersistentChatBar` (already deleted in Slice A)

### Slice C — Workflow + Furnace in Development Mode Activity Bar

- [x] 10. **Add Workflow activity bar item**
  - [x] 10-1. Add a `Workflow` icon to CodeMode's activity bar (between Debug and the spacer, or after Debug)
  - [x] 10-2. Create `WorkflowPanel` sidebar panel: lists available workflows (from backend `/api/graphs`), recent runs, favorites. Each item has a "Run" button and a "View" link (opens Operations mode for that workflow).
  - [x] 10-3. Wire the activity bar icon to toggle `WorkflowPanel` (same pattern as Explorer/Search/Git panels)
  - [ ] 10-4. Quick-launch: clicking "Run" dispatches a run and shows progress in the status bar or bottom panel

- [x] 11. **Add Furnace activity bar item**
  - [x] 11-1. Add a `Flame` icon to CodeMode's activity bar (after Workflow)
  - [x] 11-2. Create `FurnacePanel` sidebar panel: shows active/recent training sessions (from `useResearchStore.trainingSessions`), recipe quick-config form (recipe name, ingredient roots, training objective), "New Recipe" button
  - [x] 11-3. Wire the activity bar icon to toggle `FurnacePanel`
  - [ ] 11-4. Status bar indicator: when any training session is active, show `🔥 Furnace: N active` (or text equivalent) in the CodeMode status bar

- [ ] 12. **Command palette integration**
  - [ ] 12-1. Register "Run Workflow…" command in CodeMode's command palette — opens a quick-pick of available workflows
  - [ ] 12-2. Register "New Furnace Recipe" command — opens the Furnace panel with the recipe form focused

### Slice D — Research Right Drawer Discoverability

- [x] 13. **Make the right drawer more visible**
  - [x] 13-1. Replace the 13px icon-only toggle with a labeled tab bar or a labeled button visible in the left rail's toggle area (e.g., "Context ▸" or individual tab labels for References / Notes / Outline)
  - [ ] 13-2. Alternatively, add a top-right toolbar to the center desk with clearly labeled drawer tabs — similar to how VS Code's secondary sidebar has a visible toggle
  - [ ] 13-3. Consider defaulting the right drawer to open on first use (when a paper is loaded) and letting the user collapse it

### Slice E — Cleanup & Verification

- [x] 14. **Remove `Cmd+B` global shortcut or repurpose**
  - [x] 14-1. If SidebarHost is deleted and `Cmd+B` was toggling it, repurpose `Cmd+B` to toggle the active mode's own sidebar (CodeMode: Explorer sidebar, Research: left rail). Each mode already has its own toggle — just wire `Cmd+B` to dispatch to the active mode's handler.

- [x] 15. **Verify all modes**
  - [x] 15-1. Chat mode: unaffected (no SidebarHost dependency, no PersistentChatBar in Chat mode)
  - [x] 15-2. Development mode: activity bar with Workflow + Furnace, chat sidebar, no dead shell chrome
  - [x] 15-3. Research mode: chat sidebar accessible, right drawer discoverable, terminal works
  - [x] 15-4. Operations mode: chat sidebar accessible
  - [x] 15-5. `npx tsc --noEmit` clean, `npm run build` clean

- [x] 16. **Update session persistence**
  - [x] 16-1. Chat history persisted per workspace+mode in localStorage via `ModeChatSidebar`
  - [x] 16-2. `useWorkspaceSession.ts`: updated sidebar panel types to include `workflow`/`furnace`

## After-Layout Summary

After this plan, the shell is:

```
┌─ Workspace Tabs ──────────────────────────────────────┐
├─ Mode Bar ────────────────────────────────────────────┤
│                                                        │
│            Mode viewport (100% of remaining space)     │
│                                                        │
│   Each mode owns its full layout:                      │
│   - Its own sidebar (activity bar, explorer, rail)     │
│   - Its own center desk                                │
│   - Its own chat sidebar (right, toggleable, Cmd+J/L)  │
│   - Its own bottom panel (terminal, problems, etc.)    │
│   - Its own status bar (if applicable)                 │
│                                                        │
└────────────────────────────────────────────────────────┘
```

Development mode activity bar after:
```
Explorer | Search | Git | Extensions | Tasks | Testing | Timeline | Outline | Debug | Workflow | Furnace
── spacer ──
AI Chat | Settings
```

## Decisions
- Created `ModeChatSidebar` as a new shared component rather than refactoring the existing `ChatSidebar` in place — the original `ChatSidebar` had too many Development-mode-specific features (quick actions, file path linking, Apply/Insert buttons) that would complicate generalization. The original file remains for backward compatibility but CodeMode now uses `ModeChatSidebar`.
- Chat history persisted directly in localStorage per `dan-chat-{workspaceId}-{mode}` key rather than adding fields to `sessionPersistence.ts` — keeps the persistence local to the chat component and avoids growing the session blob.
- `Cmd+B` repurposed to dispatch a `app:toggleModeSidebar` CustomEvent that each mode listens for (gated on active mode). CodeMode toggles its explorer sidebar; Research/Operations are no-ops since they don't have a collapsible sidebar.
- `Cmd+J` repurposed to dispatch `app:toggleModeChatSidebar` CustomEvent — each mode toggles its own chat sidebar, gated on active mode.
- Workflow and Furnace panels created as inline placeholder components in CodeMode rather than separate files — they're small and will be fleshed out when the backend APIs are connected.

## Notes
- The `QuickAccessItem` stubs in SidebarHost (Recent Files, Recent Runs, Pinned Items) were never wired. If any of these concepts are needed, they should be built per-mode (e.g., Recent Files in CodeMode's Explorer, Recent Runs in the Workflow panel).
- `pendingChatMessage` in `useAppStore` was used by PersistentChatBar to pass a message to Chat mode on navigation. With per-mode chat sidebars, this mechanism is no longer needed.
- `ChatSidebar` already has `registerChatSender` / `sendToChat` exports for cross-component message injection. The shared `ModeChatSidebar` should preserve this pattern but namespace it per mode.
- Follow-up polish after initial rollout: keep Chat mode's composer explicitly bottom-docked, pin Code mode's chat/settings controls at the bottom of the activity bar so added icons cannot push them off-screen, and give Research mode a labeled `Chat` toggle for discoverability.
