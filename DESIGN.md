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

- Project/chat management defaults to a conventional sidebar with compact folder rows and indented chats. No oversized current-project card, scope tabs, or redundant settings bar. Carousel and wheel are optional accelerators. Project creation/editing uses a focused name/folder dialog.

- Folder drop affordance is visible before interaction: dashed outline, folder icon, centered instruction, browse action, and an editable path inside the boundary.
