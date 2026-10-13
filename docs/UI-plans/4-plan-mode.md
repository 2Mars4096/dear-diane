# 4: Plan mode review experience

**Status:** completed
**Goal:** Make scope, specifications, and next steps easy to understand and refine together before execution, with few questions.

## Tasks
- [x] Inspect existing plan presentation and official Codex/Claude Code planning documentation.
- [x] Read upstream grill-me/grilling and adapt its decision-first approach to a small question budget.
- [x] Draft the proposed layout, interaction rules, first-release scope, and acceptance checks.
- [x] User approved the direction and requested implementation, explicitly separate from skills; implement directly and validate the real UI with synthetic browser data.
- [x] Implement the reviewed experience using existing sessions, side panes, and V2 admission/execution, with a durable review document handed to the selected runtime.
- [x] Verify persistence, revision/execution boundaries, section and step editing, keyboard-operable controls, and narrow layouts.

- [x] Replace composer Plan button with `/plan` entry and conditional Plan mode status; verify slash suggestions, entry/exit and review flow.

## User direction
- Primarily UI/UX: help us understand next steps, scope, and specs.
- Learn from Codex, Claude Code, and grill-me without dozens of questions.
- October 13: user approved the proposed steps and requested implementation, with Plan mode distinct from skills.

## Reference findings
- [Codex planning](https://developers.openai.com/blog/run-long-horizon-tasks-with-codex): reviewable steps before changes, follow-up questions when necessary. [Current command reference](https://learn.chatgpt.com/docs/reference/slash-commands): `/plan` toggles planning. Borrow explicit entry and a reviewable approach.
- [Claude Code plan review](https://code.claude.com/docs/en/permission-modes#review-and-approve-a-plan): review the proposed plan, keep planning with feedback, edit directly, or approve and enter execution. Borrow the clear handoff and revision loop; keep Diane's permission policy separate from approval of scope.
- [grill-me](https://github.com/mattpocock/skills/blob/main/skills/productivity/grill-me/SKILL.md) currently delegates to [grilling](https://github.com/mattpocock/skills/blob/main/skills/productivity/grilling/SKILL.md). It orders questions by decision dependencies, recommends answers, and researches discoverable facts. Its exhaustive interview is deliberately not adopted here.
- Research is based on fetched documentation and local source, not hands-on visual verification of competitor apps.

## Existing Diane foundation
- `editor/src/components/workspace/ChunkWorkspaceApp.tsx` already contains `BlueprintContractPlate`, `PlanChecklistPreview`, revision handling, and task/dependency views.
- Contract facets include constraints, non-goals, risks, and acceptance criteria, but the inspected contract plate exposes much of their content through count chips and tooltips.
- `planPreviewBody` includes graph versions, frontier IDs, and queue metadata. Keep these available in technical details; the review surface should lead with readable work and behavior.
- Native worker adapters already recognize plan permissions. A permission setting alone does not provide a shared, editable plan review experience.

## Proposed experience

### Entry and layout
- `/plan` toggles planning for the current conversation; `/plan <request>` enters planning with a task. Discover commands through `/` suggestions, independently of `$skill` mentions. No persistent Plan button.
- Show a noninteractive `Plan mode` status only while planning is active. Sending a message refines the plan; it does not start implementation.
- Open a resizable plan document beside chat using the existing pane system. A collapsed `Plan · Draft` entry reopens it without losing the chat or document position.
- On narrow windows, use Chat / Plan views in the same surface; preserve draft replies and scroll positions. Do not squeeze two unreadable columns together.
- Use existing theme/font settings, flat sections, and ordinary text. Important scope and specs must be readable without hover.

```text
Conversation                     Plan · Draft 2
                                 Improve paper search
Your request and discussion      Outcome
                                 Find all matching papers and read their titles.
[Scope: Discuss this]            Scope
Could we include filters?        Includes: result list and title layout
                                 Later: semantic search and new filters
Diane updates the affected
section, explains the change.    Behavior / Done when
                                 • All matching results are reachable.
                                 • Long titles wrap in narrow windows.
                                 • Keyboard selection stays usable.

                                 Next steps
                                 1. Inspect search and selection behavior
                                 2. Update result limits and title layout
                                 3. Verify many results and narrow layouts

                                 Assumptions · 1        [Review]
Plan mode · Refine this plan…        [Start this plan]
```

### Content hierarchy
- **Outcome:** one or two sentences describing the intended result.
- **Scope:** short Includes and Later/out-of-scope lists; explicit boundaries, not a wall of caveats.
- **Behavior / Done when:** observable specifications and examples. Expand detail when the task needs it, including research outputs and evidence requirements for non-code work.
- **Next steps:** an ordered list with concrete deliverables. Expand a step for dependencies, affected areas, and its check. Use a simple sequence by default; reveal a graph only on request.
- **Decisions and assumptions:** proposed defaults, confirmed choices, and unresolved blockers are visibly distinct. Surface blockers near the start action; routine assumptions can stay folded.
- Avoid empty sections on small plans. A small change may need only an outcome, two scope bullets, and three steps.

### Refining the plan
- Each section and step offers `Edit` and `Discuss this` on focus/hover, with keyboard access.
- `Discuss this` adds a removable section reference to the existing composer; it does not create a separate conversation or send immediately.
- A response updates the affected section and shows a short change summary, for example `Filters moved to Later; verification step updated`.
- Keep one current plan visible. Earlier revisions are available through history; do not repeatedly insert full duplicate plans into chat.
- Preserve user edits. If agent updates conflict with a newer edit, show the proposed change instead of overwriting it.
- Show saving, saved, and retry states; restore the latest saved draft after reopening the session.

### Question budget
- Research files and context first; do not ask the user for facts Diane can inspect.
- Draft immediately when enough is known. Ask only when an answer changes scope, required behavior, or an important tradeoff.
- Recommend **at most three substantive questions before the first reviewable draft**, normally one at a time. Do not hide several questions inside one prompt or restart the budget every turn.
- Each question explains the consequence briefly, offers a recommended option and one or two alternatives, and accepts free text.
- Offer `Use recommendation` and `Decide later`. A recommendation is not silently recorded as a user decision.
- After the budget, draft with labeled assumptions and any remaining blockers. Do not launch another automatic interview. Additional depth is user-initiated with `Review open decisions` or a request to challenge the plan.
- Deferring a genuinely blocking decision leaves the draft reviewable but explains why execution cannot start. Optional unanswered choices do not block a usable plan.
- Borrow dependency-aware questioning from grilling: settle the broad scope before asking about details that depend on it.

### Transition to execution
- States: Draft, Needs decision, Ready to start, Running, Completed; retain failure/interruption states from the runtime.
- `Start this plan` starts the displayed saved revision after unresolved blockers are handled. Disable during saving/start submission and prevent duplicate starts.
- Keep permission/approval policy unchanged. Accepting scope must not silently grant broader tool access.
- Once started, the same next-step list shows observed progress and the next action; do not mark steps complete from plan prose alone.
- Proposed material scope changes during execution appear as a revision with a concise difference summary. Editing a future draft does not silently replace the executing revision.
- Leaving Plan mode alone does not count as approval or start work. Keep the draft available.

## First-release scope
- Include mode entry, one durable plan document, readable scope/specs/steps, section editing/discussion, bounded questions, revision summary, and explicit start tied to the reviewed revision.
- Reuse existing conversation, document panes, task contracts, and progress transport wherever possible. Map missing UI fields explicitly; do not invent completion or support from plain text.
- Defer visual dependency editing, nested planning wizards, per-step agent configuration, team dashboards, and elaborate branching review tools.
- Delivered: integrated Plan review UI, durable server state, native/API planning, section and step revision, and an exact-revision execution handoff.

## Acceptance checks
- From the first view, a reader can identify the intended outcome, included/excluded scope, expected behavior, and first next step.
- A clear task produces a plan without compulsory questions; an ambiguous task reaches a draft within the three-question budget.
- A user can change one specification through inline editing or anchored discussion and see exactly what changed.
- Deferred assumptions remain visible and distinguishable from confirmed answers; a blocking decision cannot be mistaken for readiness.
- Starting uses the displayed saved revision exactly once, and the runtime permission policy remains intact.
- Reopen, session switching, narrow layout, keyboard navigation, and failed-save recovery preserve work and truthful status.
- Existing blueprint details and execution evidence remain accessible without dominating plan review.

## Notes
- October 13, 2026: research and design proposal only. No product code, runtime behavior, or installed app changed.

## Implementation and evidence, October 13
- Plan is a session conversation mode; the existing permission option is labeled Read only. `$skill` selection remains independent and selected planning skills carry into the explicit start.
- `PlanPanel` uses the shared side pane, existing resize control and palette. Sections/steps support editing and discussion references; a first-step preview keeps the immediate action visible. Narrow windows use the full pane with Back to chat.
- `.plan.json` beside each chat stores current document, 20 prior revisions, question count, a conflicting proposal, and the immutable started revision/run ID. Compare-and-swap saves preserve newer edits; unfinished local edits survive panel remounts in session storage.
- Native planners receive read-only permissions and a structured response contract. The built-in API planner uses the selected model without tools; it plans from supplied context and marks uninspected facts. It does not claim repository research.
- Start validates the displayed revision, blocks duplicates/open blockers, preserves execution permissions, and rejects attempts from stale windows while other session work is active. The panel shows observed run status/progress; it does not infer individual step completion from prose.
- Native process bridges reject start/stop in planning; list/logs remain available.
- Browser fixture verifies mode entry, scope and individual step edits, anchored discussion, 390px layout without overflow, and starting revision 3 with Auto permissions unchanged. Evidence: `output/playwright/plan-{desktop,mobile}.png`.
- A real native Codex planning run read a temporary README, saved revision 1, asked zero questions, and left only the original README in the fixture workspace. No implementation was dispatched by this smoke test.
- Production build and bundle budgets pass with the shared plan state in a separate chunk. API/native regression tests and component tests cover state, conflicts, question limits, start boundaries and process permissions.
- Installed-app replacement and Claude/provider live acceptance were not performed. All code and tests are local; the browser used synthetic API data.

- Final checks: 97 backend/session/native-lead tests and three process-manager tests pass; 167 focused frontend tests pass after the final changes. Earlier full frontend run passed 500 tests plus the process-cleanup permission-enabled rerun; the additional individual-step regression also passes. New Plan files pass ESLint; TypeScript, production build, bundle budgets, and whitespace checks pass.

- Slash-command follow-up: removed the composer mode button, corrected `/plan` discovery text, and show a noninteractive Plan mode status only while active. Synthetic browser verifies entry/exit/re-entry, desktop/390px review and exact-revision start; 157 focused frontend tests and production build/budgets pass.

## Superseding user direction: conversational planning
- The earlier side-pane/editor design above is superseded by the user's screenshot feedback.
- [x] Remove Plan from side tools and render the current plan within the assistant transcript, anchored to its planning run where available.
- [x] Remove section/title editing, Discuss actions, and panel navigation. Keep `/plan` entry and the active-only composer status.
- [x] Ask one inline MCQ at a time. `selection_mode=single` uses radios; `multiple` uses checkboxes. Recommended answers are labeled, never preselected. Submit explicitly; ordinary chat replies remain available.
- [x] Present the plan after questions, with the existing guarded Start this plan action and preserved backend revisions.
- [x] Validate transcript anchoring, choices, existing session/start guards, narrow layout and production build.
- Validation: 159 focused frontend tests, 10 backend plan tests, production build/budgets and synthetic desktop/mobile browser checks pass. The preview uses sample data, not a live model session. Installed app unchanged.

- [x] October 13: install the current local build at user request; signed bundle/archive equality, idle checks and launched backend/proxy health pass. Plan mode is now included in the installed app. Live model MCQ acceptance remains separate from the synthetic preview.

## Batched choices follow-up
- [x] Remove the empty planning greeting and empty assistant wrapper.
- [x] Support up to three simultaneous MCQs within the durable total question budget; accept older single-question records.
- [x] Use square checkboxes with single/multiple selection rules, always include Other and optional comments, and submit the batch together.
- [x] Six frontend and eleven backend tests, production build/budgets pass; prepare local bundle.
- [x] Install follow-up after idle guard clears; preserve rollback and verify signature/archive/proxy/backend health.

- Final refinement: bordered clickable answer rows; Other and comments share a horizontal row; Recommended stays inline. Custom Other answers use the same comment field. Model instructions request descriptive options. Desktop/390px preview, six frontend tests, eleven backend tests and build/budgets pass.

- [x] Distinguish single-choice rounded selectors from multiple-choice square checkboxes. Keep only Choose one / Choose multiple labels. Typing comments selects Other, preserving other selections only for multiple-choice questions.

- [x] Install concise choice labels and auto-select Other follow-up; seven focused tests, build/budgets, signature/archive equality and app health pass. Rollback retained at `/Applications/.Diane-previous-6c40a7e2-9cdb-4ebd-917c-a673be863601.app`.
