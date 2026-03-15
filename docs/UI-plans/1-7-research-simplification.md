# 1-7: Research Workspace Simplification

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** completed
**Goal:** Simplify the Research mode by replacing the redundant bottom dock (Code/Figures/Data) with the shared terminal panel from Development mode, and adding a dedicated Furnace center-desk tab for recipe training definition and ingredient management.

## Motivation

The current bottom dock duplicates Development mode features (code cells, data browser). Research mode should own reading/writing/learning and delegate coding to Development mode's full-featured environment. The terminal panel gives researchers shell access for LaTeX compilation, scripts, and other tools without a toy code-cells interface.

The Furnace (recipe training) currently lives as a shallow left-rail tab. It needs a proper center-desk surface to define recipes, manage ingredients, and monitor distillation runs.

## Layout

```text
┌─ Workspace Tabs ─────────────────────────────────────────────┐
│ [Supply Chain OM] [+]                                        │
├──────────────┬───────────────────────────────────────────────┤
│ Left Rail    │ Center Desk                                   │
│              │                                               │
│ 📂 Library   │ [Editor] [Reader] [Furnace]                   │
│ ✏️ Plan      │                                               │
│ 🔥 Training  │ (one dominant surface at a time)              │
│              │                                               │
│──────────────│                   + Right Drawer (on-demand)  │
│ Domain Prof. │                                               │
│ Panel toggles│                                               │
├──────────────┴───────────────────────────────────────────────┤
│ Terminal (shared with Development mode via TerminalPanel)     │
└──────────────────────────────────────────────────────────────┘
```

## Tasks

### 1. Replace bottom dock with terminal
- [x] 1-1. Remove BottomDock component (Code/Figures/Data tabs) from ResearchMode
- [x] 1-2. Import TerminalPanel from code/TerminalPanel and render in the bottom Allotment pane
- [x] 1-3. Use useCodeStore terminal state (showTerminal, toggleTerminal) for Research mode's bottom panel
- [x] 1-4. Replace showSecondary/toggleSecondary with showTerminal/toggleTerminal in Research mode
- [x] 1-5. Update keyboard shortcut (Cmd+`) to toggle terminal in Research mode
- [x] 1-6. Update bottom panel toggle icon in the left rail

### 2. Add Furnace as a primary center-desk tab
- [x] 2-1. Add "furnace" to the PrimaryTab type in useResearchStore
- [x] 2-2. Create FurnacePanel component with recipe definition, ingredient list, and distillation progress
- [x] 2-3. Add Furnace tab button in PrimaryPanel alongside Editor and Reader
- [x] 2-4. Wire Training rail section clicks to open the Furnace tab
- [x] 2-5. Wire "Learn 100 Papers" quick-start to open the Furnace tab

### 3. Update session persistence
- [x] 3-1. Remove secondaryTab from ResearchSession (no longer needed)
- [x] 3-2. Ensure showTerminal state is persisted per workspace (already handled by Code store)

## Decisions

- Reuse `TerminalPanel` and `useCodeStore` terminal state rather than duplicating terminal management in the research store.
- Remove `showSecondary`, `toggleSecondary`, `secondaryTab`, `setSecondaryTab` from research store usage in the layout (the state can remain for backward compat but is no longer rendered).
- The Furnace panel is a primary tab, not a secondary/bottom panel, because recipe definition and ingredient management deserve the full center area.

## Notes

- The Code/Figures/Data components (CodeCells, FigureGallery, DataBrowser) still exist as lazy imports and can be used elsewhere if needed. They are not deleted, just not rendered in the default Research layout.
- The terminal in Research mode uses the same terminal instances as Development mode — switching modes preserves open terminals.
