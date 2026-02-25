# 10-4: Graph Diff & Confirmation UX

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** not-started
**Goal:** Before applying LLM-generated graph mutations, show users a clear visual diff of proposed changes and let them accept, reject, or partially accept. Prevents unwanted modifications and builds trust in the conversational authoring flow.

## Tasks

- [ ] 1. Graph diff computation
  - [ ] 1-1. `computeGraphDiff(before: GraphDict, after: GraphDict) -> GraphDiff` utility function in a new `editor/src/lib/graphDiff.ts`
  - [ ] 1-2. `GraphDiff` type: `{ added_nodes, removed_nodes, modified_nodes, added_edges, removed_edges, modified_edges, summary: string }`
  - [ ] 1-3. Node diff: compare by node_id. Added = in `after` but not `before`. Removed = in `before` but not `after`. Modified = same id but different properties (name, prompt, model, ports, config). For modified nodes, compute a field-level diff (which fields changed, old vs. new values).
  - [ ] 1-4. Edge diff: compare by `(source_id, source_port, target_id, target_port)` tuple. Added/removed/modified (type change, port change).
  - [ ] 1-5. Summary generation: "Add 2 nodes, remove 1 node, modify 1 edge" — concise one-liner for the collapsed diff view.

- [ ] 2. Diff preview dialog component
  - [ ] 2-1. `GraphDiffPreview.tsx`: modal/drawer that shows proposed changes before applying. Triggered when chat produces a mutation plan.
  - [ ] 2-2. Layout: three columns or sections — Added (green), Removed (red), Modified (yellow). Each section lists affected nodes/edges.
  - [ ] 2-3. Node cards: show node name, type icon, and key properties (prompt preview, model, port list). For modified nodes, show old→new values with highlighted diffs.
  - [ ] 2-4. Edge rows: show source→target with port names. For modified edges, show what changed.
  - [ ] 2-5. Summary bar at top: "This change will add 2 nodes, remove 1 node, and modify 3 edges."
  - [ ] 2-6. Canvas mini-preview (stretch): a small React Flow canvas showing the before/after graph side-by-side with color-coded additions/removals. This is a stretch goal — start with the list-based diff.

- [ ] 3. Accept / reject / partial-accept controls
  - [ ] 3-1. "Apply All" button: applies the full mutation plan, pushes to undo stack, closes preview
  - [ ] 3-2. "Reject All" button: discards the mutation plan, posts a "rejected" message in chat, LLM can respond with alternative
  - [ ] 3-3. Per-operation checkboxes: each operation in the diff has a checkbox (default: checked). User can uncheck individual operations to partially accept. "Apply Selected" button applies only checked operations.
  - [ ] 3-4. "Edit and Apply": opens a text editor with the mutation plan as JSON/YAML, user can manually tweak operations before applying. Power-user feature.
  - [ ] 3-5. Keyboard shortcuts: Enter = Apply All, Escape = Reject, Tab = move between operations

- [ ] 4. Undo stack integration
  - [ ] 4-1. Before applying mutations, push a single undo snapshot (existing `pushSnapshot` in `useGraphStore`). All operations in the mutation plan are a single undo step.
  - [ ] 4-2. The undo snapshot includes a label: "Chat: [first 50 chars of user message]" for identification in any future undo history UI.
  - [ ] 4-3. `Ctrl+Z` after a chat mutation reverts the entire batch. This is consistent with manual editing undo behavior.
  - [ ] 4-4. Multiple sequential chat mutations create separate undo snapshots (one per chat message that produces mutations).

- [ ] 5. Post-apply feedback
  - [ ] 5-1. After applying mutations, animate the changes on the canvas: new nodes fade in (green flash), removed nodes fade out (red flash), modified nodes pulse (yellow flash). Use CSS transitions, 500ms duration.
  - [ ] 5-2. Auto-layout: if new nodes were added, trigger dagre auto-layout to prevent overlapping. Optionally only layout the affected region (new nodes + their neighbors).
  - [ ] 5-3. Post-apply toast: "Applied 3 changes from chat" with an "Undo" link in the toast.
  - [ ] 5-4. Chat message update: the "proposed changes" block in the assistant message changes to "Changes applied ✓" (or "Partially applied: 2 of 3" for partial accept).

- [ ] 6. Conversation-level rollback
  - [ ] 6-1. Each chat message that produces mutations records a `graph_snapshot_id` (pointer to the undo stack position before the mutation).
  - [ ] 6-2. In the chat message history, a "Revert to here" action on any assistant message with mutations: rolls the graph back to the state before that message's mutations (using the undo stack).
  - [ ] 6-3. Revert cascades: reverting message N also reverts messages N+1, N+2, etc. if they also produced mutations. Show a confirmation: "This will also undo changes from 2 later messages."
  - [ ] 6-4. Visual indicator: messages whose mutations are active show a green dot; reverted messages show a gray dot with strikethrough on the "Changes applied" label.

- [ ] 7. Tests
  - [ ] 7-1. `computeGraphDiff` unit tests: node add/remove/modify, edge add/remove/modify, empty diff, full replacement
  - [ ] 7-2. Partial accept: apply subset of operations, verify graph state
  - [ ] 7-3. Undo integration: apply mutations, undo, verify graph reverts
  - [ ] 7-4. Conversation rollback: apply 3 messages with mutations, rollback to message 1, verify graph state
  - [ ] 7-5. Frontend: verify diff preview renders correctly, accept/reject buttons work, animations fire (component test or manual)

- [ ] 8. Docs sync
  - [ ] 8-1. `architecture.md`: document GraphDiffPreview component, diff computation, undo integration
  - [ ] 8-2. `changelog.md`: implementation entry

## Decisions

- (to be filled during execution: list-based vs. canvas-based diff preview, partial accept UX details, rollback cascade confirmation)

## Notes

- The diff preview is directly inspired by Cursor's code diff view and GitHub's PR diff. Users should feel the same level of confidence reviewing graph changes as they do reviewing code changes.
- Partial accept is important because LLMs sometimes get most of a complex request right but make one mistake. Users should be able to accept the good parts and reject the bad.
- Conversation-level rollback is a powerful feature that most chat-based tools lack. It turns the chat into a reversible timeline of graph evolution.
- The "Edit and Apply" escape hatch exists for power users who want to tweak the LLM's output. Most users will use Accept/Reject.
- Canvas mini-preview (stretch goal) would be the most intuitive diff format but is complex to implement (two React Flow instances, color-coded nodes). Start with the list-based diff and iterate.
