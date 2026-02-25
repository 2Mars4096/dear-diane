# 10-2: `@` Mention & Co-Navigation

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** not-started
**Goal:** Add a Cursor-style `@` mention system to the chat panel. Users type `@` to reference specific nodes, saved workflows, or sub-graphs. Mentions create bidirectional links between chat and canvas — click a mention to navigate, select a node to suggest a mention.

## Tasks

- [ ] 1. `@` trigger detection
  - [ ] 1-1. In the chat input textarea, detect when user types `@` (not preceded by a word character, to avoid triggering mid-word)
  - [ ] 1-2. After `@`, capture the search query (characters typed after `@` until space, Enter, or Escape)
  - [ ] 1-3. Debounce filtering: 100ms debounce on keystroke to avoid excessive re-filtering
  - [ ] 1-4. Escape or clicking outside dismisses the autocomplete without inserting a mention

- [ ] 2. Autocomplete dropdown component
  - [ ] 2-1. `MentionAutocomplete.tsx`: floating dropdown positioned below/above the cursor in the textarea (use `getCaretCoordinates` or a positioning library)
  - [ ] 2-2. Three sections in the dropdown, each with a header label:
    - **Nodes** — all nodes in the current graph (including drilled-in sub-graph), searchable by name and type. Icon + name + type badge per row.
    - **Workflows** — all saved workflows from `graphList`, searchable by name. Folder icon + name per row.
    - **Sub-graphs** — composite/loop nodes that contain sub-graphs, searchable by name. Nested-icon + name per row.
  - [ ] 2-3. Fuzzy matching: filter by substring match on name (case-insensitive). Highlight matching characters.
  - [ ] 2-4. Keyboard navigation: arrow keys to move selection, Enter to insert, Escape to dismiss. Max 8 visible items with scroll.
  - [ ] 2-5. Empty state: "No matches for @query" with subtle text

- [ ] 3. Mention chips in chat input
  - [ ] 3-1. When user selects an item from autocomplete, replace `@query` with a styled mention chip (inline element in the textarea). Chip shows icon + name, styled with a colored background (blue for nodes, green for workflows, amber for sub-graphs).
  - [ ] 3-2. Implementation options: (a) ContentEditable div with inline chip spans, (b) textarea with chip rendering via overlay, (c) use a rich-text input library (e.g., Slate, TipTap). Prefer the simplest approach that supports inline chips + plain text.
  - [ ] 3-3. Mention data model: `{ type: "node" | "workflow" | "subgraph", id: string, name: string }`. Stored in message alongside plain text. Serialized as `@[name](type:id)` in the message string sent to backend.
  - [ ] 3-4. Backspace on a chip deletes the entire chip (atomic delete)
  - [ ] 3-5. Multiple mentions per message supported

- [ ] 4. Mention rendering in message history
  - [ ] 4-1. In `ChatMessage.tsx`, parse `@[name](type:id)` tokens and render as styled chips (same visual as input chips but non-editable)
  - [ ] 4-2. Chips in messages are clickable (triggers co-navigation, task 5)
  - [ ] 4-3. If the referenced node/workflow no longer exists (deleted since message was sent), render chip with strikethrough style and "(deleted)" suffix

- [ ] 5. Click mention → canvas navigation
  - [ ] 5-1. Clicking a node mention chip: select the node on canvas (`setSelectedNode`), center viewport on it (`fitView` with padding focused on the node), flash/highlight the node briefly (CSS pulse animation, 1s)
  - [ ] 5-2. If the mentioned node is inside a sub-graph (not at current layer), auto-drill-in to the correct layer first, then select
  - [ ] 5-3. Clicking a workflow mention: open the workflow in a new tab (via `openTab`) or switch to existing tab if already open
  - [ ] 5-4. Clicking a sub-graph mention: drill into that sub-graph (`drillIn` action)

- [ ] 6. Canvas → chat mention suggestion
  - [ ] 6-1. When a node is selected on the canvas AND the chat panel is open AND the chat input is focused, show a subtle hint chip above the input: "Mention @NodeName" (clickable to insert)
  - [ ] 6-2. When multiple nodes are selected (multi-select), show chips for all selected nodes
  - [ ] 6-3. Right-click context menu on a node: add "Mention in chat" action. Inserts `@[NodeName](node:id)` into the chat input at cursor position and focuses the input.
  - [ ] 6-4. Optional: drag a node from canvas onto the chat input to create a mention (stretch goal)

- [ ] 7. Mention resolution for backend
  - [ ] 7-1. When sending a message to the backend, resolve mentions into structured context. Each `@[name](node:id)` is expanded into the full node definition (type, model, prompt, input/output ports, connected edges) and injected into the LLM context as a structured block.
  - [ ] 7-2. Workflow mentions: inject the workflow's node list and description as context
  - [ ] 7-3. Multiple mentions: each resolved independently and included as separate context blocks. Dedup if the same entity is mentioned twice.
  - [ ] 7-4. Context budget: mentioned entities get priority in the graph context serialization. Non-mentioned nodes get compressed representation if total context exceeds budget.

- [ ] 8. Tests
  - [ ] 8-1. Mention parsing: `@[name](type:id)` → structured mention object, round-trip serialization
  - [ ] 8-2. Fuzzy matching: test substring filter, case insensitivity, empty query shows all
  - [ ] 8-3. Mention resolution: test full node context expansion, missing node handling, budget enforcement
  - [ ] 8-4. Frontend: verify autocomplete appears on `@`, keyboard navigation works, chip insertion/deletion works (component test or manual)

- [ ] 9. Docs sync
  - [ ] 9-1. `architecture.md`: document MentionAutocomplete component, mention serialization format
  - [ ] 9-2. `changelog.md`: implementation entry

## Decisions

- (to be filled during execution: rich text input approach, mention serialization format, autocomplete positioning strategy)

## Notes

- Cursor's `@` mention is the gold standard for UX. Key qualities: instant response, clear visual distinction, seamless keyboard flow (type `@`, filter, Enter, keep typing).
- ContentEditable is powerful but complex. A textarea with an overlay for chip rendering might be simpler and more reliable. Evaluate during implementation.
- The mention system is reusable beyond chat — future phases could use it in node prompt editors, condition expressions, or markdown flow notation.
- Mention resolution is the bridge between natural language and structured graph operations. The richer the context injected for mentions, the better the LLM can reason about requested changes.
