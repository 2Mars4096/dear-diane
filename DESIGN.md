# Workbench design

- Work palettes are selectable in Dear Diane Settings: Neutral, Slate, Midnight/Daylight blue, Purple, Teal, Warm charcoal/ivory (default), Graphite, and OLED/minimal. Preserve the operator-supplied hex values for background, surface, border, primary text, muted text, and accent in both modes. Share palette tokens across the workbench, controls, popovers, and dialogs. Light/Dark/System is independent of the palette and Notes texture/tone.

- Conversation first: centered readable transcript, anchored composer, one thin navigation bar.
- Neutral paper/ink surfaces, system typography, restrained borders, no dashboard tiles in the main conversation.
- Project carousel: focused project in front; adjacent projects recede. Arrow keys or scroll preview; Enter commits; Escape cancels.
- Session wheel: eight fixed compass slots, newest eight by creation. Preserve surviving slots when a new session enters. Number keys commit; arrows and WASD support simultaneous diagonal chords; Q/E/Z/C select diagonals directly. Selection persists after release.
- Panels reveal files, task activity, and previews on demand. Notes retain their existing editing tools.
- Motion communicates focus and arrival, uses opacity/transform, and respects reduced motion.
- Message selection can be quoted into the composer; sending remains explicit.
- Agent identity comes from recorded backend/worker metadata. Diane, Codex, and Claude use the same event disclosure design; never infer that an unconnected worker is running.
- Accessible dialogs trap focus, restore it on close, expose visible shortcuts, and support narrow screens without horizontal overflow.

- Workspace navigation uses one sidebar list for sessions, PDFs, and open files, with single-line titles, no leading item icons, and project context in the header and hover text. Search opens from the magnifier; the project selector shares Cmd/Ctrl+Shift+E, and Cmd/Ctrl+Shift+0 restores all projects. Pins mark existing rows. Cmd/Ctrl+1–9 targets recent items and displays frozen number hints while held. No duplicate top tab row. Carousel and wheel remain optional accelerators. Project creation/editing uses a focused name/folder dialog.

- Folder drop affordance is visible before interaction: dashed outline, folder icon, centered instruction, browse action, and an editable path inside the boundary.

- Project and session menus share the palette surface role, border, shadow, typography, spacing, and hover/focus treatment.

- Appearance uses dropdowns with one shared preview of all selected colors and font roles; no per-option cards. Neon adds a dark navy/mint terminal palette. System, Humanist, Bookish and Monospace are local font sets with independent normal, heading/emphasis and saved-highlight quote roles. Original document typography is preserved.

- The appearance preview scrolls with its dropdown controls in normal flow; do not pin it or give it a separate scrolling layer.

- Plan mode uses `/plan` and shows an active-only composer status. Planning lives inside assistant chat responses, with one bounded question at a time: square checkboxes for both exclusive and compatible choices, labeled recommendations without preselection, and explicit Submit answer. Show the completed plan and Start this plan inline. No Plan side tab, section editing, Discuss actions, or separate review workspace. Preserve scope approval separately from skills and execution permissions.

- Planning follow-up: show up to three MCQs together within the existing session question budget. Every question has Other with a custom answer and optional comments. Submit all answers together. Empty Plan mode adds no greeting to the transcript. `questions` is the batch field; legacy `question` remains readable.

- Plan choices use bordered clickable rows with checked/focus states. Other and its comment input share one horizontal row; Recommended is inline with answer text. Comments also supply the custom answer when Other is selected.

- Choice mode uses a concise Choose one / Choose multiple label without a secondary selection hint. Single-choice inputs have rounded check-mark selectors and radio semantics; multiple-choice inputs are square checkboxes. Typing comments selects Other, replacing the prior single answer or preserving multiple selections.

- Team information has three levels: compact status counts in the conversation header, per-run summaries inside the transcript, and inspectable worker detail in the Team pane. Avoid repeating provider/activity chains across all three.
- Show worker availability separately from assignment outcomes; a stopped assignment must not be presented as completed. Work disclosures use one label and elapsed time, without a second Details caption. Session IDs belong in session options.
