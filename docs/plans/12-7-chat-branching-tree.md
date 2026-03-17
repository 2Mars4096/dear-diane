# 12-7: Chat Branching & Tree View

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Turn chat branching into a first-class exploration model: safe branch-based edit/regenerate is the foundation, explicit lineage metadata enables the tree view, and branch-oriented affordances make multi-path exploration discoverable.

## Tasks
- [x] 1. Branch-safe turn rewriting foundation
  - [x] 1-1. Use non-destructive branching instead of replacing later turns in the same thread
  - [x] 1-2. Add `Edit & resend` on past user turns
  - [x] 1-3. Add `Regenerate` on assistant/result turns
  - [x] 1-4. Preserve attachments when branching from past turns
  - [x] 1-5. Seed branched threads with inherited pre-branch history and a readable `(branch)` title
  - [x] 1-6. Add focused helper tests for branch target resolution and branch title generation
- [x] 2. Explicit branch lineage model
  - [x] 2-1. Store lineage in thread meta: `parent_thread_id`, `branch_point_message_id`, `branch_type` (`edit | regenerate | explore`)
  - [x] 2-2. Accept lineage fields in `POST /api/chats/{workflow_id}` and persist them on creation
  - [x] 2-3. Return lineage fields in `GET /api/chats/{workflow_id}` thread summaries
  - [x] 2-4. Frontend: extend `createChatThread` API call and `ChatThreadSummary` type to carry lineage
  - [x] 2-5. Frontend: pass lineage through `createBranchedThread` in ChatPanel for edit, regenerate, and explore flows
- [x] 3. Branch-oriented affordances
  - [x] 3-1. Add `getExploreBranchTarget` helper: history includes the assistant message, composer is blank for a new question
  - [x] 3-2. Add "Explore from here" button on assistant results — creates a branch and opens the composer
  - [x] 3-3. Show branch indicator on threads that have a parent (icon or label in thread list)
  - [x] 3-4. Show which branch is currently active when siblings exist
- [x] 4. Chat tree view UI
  - [x] 4-1. Build branch ancestry from lineage summaries (group threads by `parent_thread_id`)
  - [x] 4-2. Render a collapsible tree/graph in full-screen chat sidebar for navigating branch hierarchy
  - [x] 4-3. Keep the existing linear conversation pane as the active-path reader; tree acts as navigation only
  - [x] 4-4. Support opening multiple sibling follow-up branches from the same result while preserving the original path

## Decisions
- Chat history rewriting is **branch-first**, not destructive. DAN chat can mutate workflow state, so preserving provenance matters more than forcing a single linear thread.
- v1 branching shipped via existing thread APIs (`create`, `update`, `send`) without a dedicated backend branch endpoint.
- Lineage is stored in thread `.meta.json` sidecar (not in `ChatThread` model) to stay backward-compatible with existing threads.
- Tree view builds on explicit lineage metadata, not inferred title suffixes.
- Task ordering: lineage (2) → affordances (3) → tree UI (4). You need the data before the actions and the actions before the visualization.

## Notes
- This plan replaces the earlier third-level `12-5-1` follow-up. Branch edit/regenerate shipped first; this follow-up completed the lineage-driven history affordances and tree navigation.
- The core UX insight: **multi-path exploration from a specific answer** with visible siblings and easy backtracking, not just generic "thread branching."
- Frontend lineage/tree derivation lives in `editor/src/lib/chatBranching.ts`, and the full-screen chat history rail now renders a collapsible branch tree while leaving the main conversation pane strictly thread-scoped.
