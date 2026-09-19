# Deep Agent Network - Claude Code Instructions

This file mirrors the always-on rules in `.cursor/rules/project-tracking.mdc` so Claude Code follows the same project tracking workflow.

## Project Tracking Documents

All tracking docs live in `docs/`. Standard implementation plans live in `docs/plans/`, and specialized plan tracks may live in sibling folders under `docs/` (currently `UI-plans/`, `benchmark-plans/`, `live-test-plans/`, and `search-infra-plans/`).

### Document Inventory

| File | Purpose | When to Read | When to Update |
|------|---------|--------------|----------------|
| `docs/todo.md` | High-level task list grouped by phase, links to plan files | Start of session to pick up work | After completing or discovering tasks |
| `docs/plans/N-name.md` | Detailed breakdown of a buildable unit with nested sub-tasks | When working on that plan | Check off sub-tasks as you complete them |
| `docs/live-test-plans/N-name.md` | Dedicated live-test plans for DAN product capability batteries | When working on live/manual evaluation tracks | Check off sub-tasks as you complete them |
| `docs/search-infra-plans/N-name.md` | Dedicated plans for the separately named Beacon Search infrastructure track | When working on search-infrastructure extraction or rollout | Check off sub-tasks as you complete them |
| `docs/changelog.md` | Log of completed work, **latest first** (descending order) | Before starting work (to avoid re-doing) | After every meaningful change — end of session at latest |
| `docs/architecture.md` | Tech stack, directory layout, conventions, patterns | Before writing any code | When adding new modules, changing patterns, or introducing dependencies |
| `docs/bugs.md` | Known issues, failed approaches, workarounds | Before debugging or proposing solutions | When discovering bugs or when an approach fails |
| `docs/development-plan.md` | Vision, roadmap, research landscape | Start of session | Only when scope, goals, or roadmap change |
| `README.md` | User-facing project overview, quick start, feature summary | Before adding new user-facing features | When adding features, changing setup steps, adding CLI commands, or updating the roadmap |
| `docs/llm-api-guide.md` | LLM-facing API reference — teaches callers how to use builder DSL, node types, edges, engine | Before writing example code or API usage instructions | When adding/changing node types, edge types, builder methods, engine API, executors, context enums, or examples |

### Session Start Protocol

1. Read `docs/todo.md` — identify what to work on and which plan is active
2. Read the active plan file in its plan folder (`docs/plans/`, `docs/UI-plans/`, `docs/benchmark-plans/`, `docs/live-test-plans/`, or `docs/search-infra-plans/`) — get detailed sub-tasks for current work
3. Read `docs/changelog.md` (last 3–5 entries) — understand recent context
4. Read `docs/architecture.md` — respect existing patterns before writing code
5. Read `docs/development-plan.md` only if the task touches roadmap-level decisions

### After Each Modification

1. **Active plan file** — check off completed sub-tasks (`[x]`), add new sub-tasks if discovered
2. **`docs/todo.md`** — mark completed high-level items `[x]`, add newly discovered tasks as `[ ]`
3. **`docs/changelog.md`** — add a new entry at the **top** (descending order: latest first):
   ```
   ## YYYY-MM-DD
   - [category] Brief description of what changed and why
   ```
   Categories: `feat`, `fix`, `refactor`, `docs`, `test`, `infra`. Never delete entries.
4. **`docs/architecture.md`** — update if you introduced new files, changed directory structure, added dependencies, or established new patterns
5. **`docs/bugs.md`** — update if you found a bug, worked around one, or tried an approach that failed (record what and why)
6. **`docs/development-plan.md`** — update only if a roadmap milestone is completed or scope changed
7. **`README.md`** — update if you added a user-facing feature, changed setup/install steps, added new CLI commands or API endpoints, or completed a roadmap milestone. Keep it concise — this is for users, not internal tracking.
8. **`docs/llm-api-guide.md`** — update if you added or changed node types, edge types, builder methods, engine APIs, executors, context enums, or examples. This is the LLM-facing API reference — keep it accurate so LLM callers can construct valid workflows.

### Plan Files

Standard implementation plans live in `docs/plans/` with **two-level hierarchical numbering** that mirrors the task tree without creating third-level plan files:

```
plans/
  1-name.md          # top-level plan
  1-1-name.md        # sub-plan of 1
  1-2-name.md        # another sub-plan of 1
  2-name.md          # next top-level plan
  2-1-name.md        # sub-plan of 2
```

The number prefix encodes the hierarchy. Sub-plans are created just-in-time — only when you're about to start working on that piece. Do not create `N-N-N` files; put deeper breakdowns inside the nearest `N-N` plan as nested tasks, notes, or sections. Future items stay as one-liners in `todo.md` marked `(not yet planned)`.

Specialized plan folders under `docs/` may also keep their own local numbering. For example, `docs/live-test-plans/1-name.md` can coexist with `docs/plans/1-name.md`; numbering is local to each folder, not global across every plan track.

#### Plan file format

```markdown
# 1-1: Sub-Plan Title

**Parent:** [1-name](1-name.md)
**Status:** not-started | in-progress | completed
**Goal:** One sentence describing what this plan achieves.

## Tasks
- [ ] 1. Task
  - [ ] 1-1. Sub-task
  - [ ] 1-2. Sub-task
- [ ] 2. Task

## Decisions
- (filled in during execution)

## Notes
- (filled in during execution)
```

Top-level plans omit the **Parent** field. Sub-plans link back to their parent.

#### todo.md format

High-level items grouped by phase. Each links to its plan file.

```markdown
# Todo

## Phase 0 — Phase Name
- [x] [1-name](plans/1-name.md) — brief description
  - [x] [1-1-name](plans/1-1-name.md)
  - [ ] [1-2-name](plans/1-2-name.md)
- [ ] 2: Description → (not yet planned)

## Backlog (unphased)
- [ ] Task description
```

### Rules

- Keep each document concise. Bullet points over paragraphs.
- **Changelog:** Update in **descending order** — newest entries at the top. Never delete entries.
- In `bugs.md`, never remove a failed approach — it prevents retry loops.
- Plan numbering is capped at two file levels: top-level (1, 2, 3) and sub-plans (1-1, 1-2). Do not create sub-sub-plan files such as `1-1-1`; merge that detail into the nearest `N-N` plan.
- When unsure whether to update a doc, update it. The cost of a stale doc is higher than a redundant entry.
