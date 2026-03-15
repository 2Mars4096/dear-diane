# 1-6: Research Workbench Refinement

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** completed
**Goal:** Refine Research mode's UI into a function-first layout with configurable paper/note roots, progressive disclosure, and a clear surface for long-running recipe training. Use straightforward one-word labels in the UI; keep the stove metaphor in icons, microcopy, and branding.

## Naming Strategy

Primary nav labels use **plain words** for instant comprehension. The stove metaphor lives in **icons, tooltips, empty-state copy, and branding** — not as the sole label a new user must decipher.

If the stove metaphor appears in product copy, use **one-word** terms only:

- `Cooktop` for the active working surface
- `Pantry` for stored PDFs/files/notes
- `Board` for planning and decomposition
- `Furnace` for long-running recipe training

Retire older mixed or multi-word stove phrases from new planning docs.

| Function | UI Label | Icon hint | Stove metaphor (copy/brand) |
|---|---|---|---|
| Active code, writing, analysis | **Desk** | pen/flame | `Cooktop` |
| PDFs, files, note roots, references | **Library** | folder/box | `Pantry` |
| Outline, planning, decomposition | **Plan** | layout/scissors | `Board` |
| Long-running recipe training | **Training** | flame/flask | `Furnace` |

## Layout

Inside Research mode:

```text
┌─ Workspace Tabs ─────────────────────────────────────────────────────────────┐
│ [Empirical OM] [Supply Chain Notes] [+]                                     │
├───────────────┬──────────────────────────────────────┬──────────────────────┤
│ Left Rail     │ Center (Desk)                        │ Right Drawer         │
│               │                                      │                      │
│ 📂 Library    │ PDF Reader / Note / Draft / Code    │ Metadata             │
│ ✏️ Plan       │ one dominant surface at a time       │ Notes                │
│ 🔥 Training   │                                      │ Extracted Signals    │
├───────────────┴──────────────────────────────────────┴──────────────────────┤
│ Bottom Dock: downloads | verification queue | training jobs | logs          │
└──────────────────────────────────────────────────────────────────────────────┘
```

Default behavior:

- Show only the left rail and center desk by default.
- Right drawer opens on demand (metadata, notes, signals).
- Bottom dock hidden until a job, download, or issue appears.
- Workspace names from the active root or corpus, never generic "Scratch".

## Tasks

### 1. Configurable paper/note roots
- [x] 1-1. Add `research.library.pdfRoots` and `research.library.noteRoots` to `useSettingsStore` (user defaults)
- [x] 1-2. Add workspace-level overrides in `useWorkspaceStore`
- [x] 1-3. Surface current roots in the Library section of the left rail
- [x] 1-4. Resolve paper identity through configured roots (`paper_id = bibtex_id`, paths derived from roots)
- [x] 1-5. Wire acquisition, verification, note linking, and recipe ingredient docs through these configured roots

### 2. Function-first left rail
- [x] 2-1. Replace the current generic ResearchNav with `Library` / `Plan` / `Training` sections
- [x] 2-2. Library shows papers from configured roots with read/unread/annotated status
- [x] 2-3. Plan shows outline, section planning, and review surfaces
- [x] 2-4. Training shows active and past furnace sessions with progress
- [x] 2-5. Keep topic/domain in workspace name and recipe metadata, not in the rail split

### 3. Progressive disclosure
- [x] 3-1. Default to left rail + center desk only (no right drawer, no bottom dock)
- [x] 3-2. Right drawer opens via click or shortcut, remembers per-workspace
- [x] 3-3. Bottom dock auto-shows when a training job, download, or verification issue exists
- [x] 3-4. Pipeline progress only visible when a pipeline is active

### 4. Workspace naming
- [x] 4-1. Default workspace name from active root folder, corpus topic, or last-opened content
- [x] 4-2. Remove fallback to "Scratch" for research workspaces

## Decisions

- Use one-word plain labels (`Desk`, `Library`, `Plan`, `Training`) as primary labels.
- If the stove metaphor is surfaced, use the one-word set `Cooktop`, `Pantry`, `Board`, `Furnace`.
- Topic/domain should define *what* a workspace or recipe is about; function defines *how* users navigate.
- Configurable roots are required. No hardcoded path assumptions.
- This does not rename top-level modes. The stove branding can evolve later after these functional surfaces prove themselves.

## Notes

- Backend recipe-training, memory schema, and distillation pipeline are tracked separately in [36-recipe-distillation-spec](../plans/36-recipe-distillation-spec.md).
- Paper acquisition should reuse the existing workflow engine plus narrow missing tools (`browser_download`, absolute-path file tools), not a new subsystem.
- The existing `recipeModel.ts` already has versioned recipe support; this plan focuses on the UI surface, not the artifact schema.
