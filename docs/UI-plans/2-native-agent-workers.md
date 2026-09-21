# 2: Native agent workers in the workbench

**Status:** in-progress
**Goal:** Let a DAN manager delegate to Codex, Claude Code, and Antigravity child sessions, with one shared interface for observing and steering them.

## Presentation contract
- The main transcript belongs to the manager. Its provider/model can be selected independently of child runtimes.
- Each child has a durable `worker_id`, `parent_run_id`, backend, native session ID, title, workspace root, status, and capability flags.
- Activity shows recorded backend identity, emitted thinking summaries, commands, tool results, artifacts, and terminal state. Expanded raw events retain provenance.
- Selecting a worker opens its own transcript in the right drawer; a parent breadcrumb returns to the manager. Worker selection never changes the project's active top-level session.
- Normalize Codex JSONL items and Claude SDK/stream events into the same display contract. Preserve native IDs for resume/reconnect and raw data for inspection.
- The session wheel contains user sessions only. Child workers stay attached to their parent; they do not consume wheel slots.
- Every worker action names its target. Steering goes to that child; stopping a child does not implicitly stop its siblings or manager.
- An interruption/approval row displays what needs attention and the target worker. Expose approve/deny/stop/resume only when that worker adapter reports the capability.
- Keep interrupted, disconnected, failed, and completed distinct. Reconnect by cursor and deduplicate event IDs before appending text.

## Tasks
- [x] Raise JSONL transcript import limit from 128 MB to 256 MB; retain explicit rejection above the limit and source preservation.
- [x] Live Codex lead steering via persistent app-server, active-run capability, acknowledgement-gated queue delivery, and per-entry Steer now controls.
- [x] Automatically resume durable follow-up queues after backend restart with saved account/policy, legacy native-profile recovery, normal delivery acknowledgement, and no new queue buttons.
- [ ] Add a shared start/status/send/stop/resume child-worker service; integrate it as tools available to DAN/OpenRouter managers.
- [ ] Implement persistent Codex and Claude Code adapters and capability negotiation.
- [ ] Persist parent/child relationships and normalized worker event cursors.
- [ ] Add worker list, selected-child transcript, scoped controls, and approval rows to the activity drawer.
- [ ] Exercise mixed-provider runs, restart recovery, stop propagation, and failed-child retries against real adapters.

## Current boundary
- The workbench already renders backend-tagged DAN/Codex/Claude events through a shared disclosure component, including emitted reasoning and command output.
- Codex remains an Agent V2 backend and is also available through the new parent-scoped native-worker service.
- Claude and Antigravity headless adapters are implemented; authenticated mixed-agent acceptance remains pending.

## September 17 implementation baseline
- [x] OpenRouter manager choices (DeepSeek V4.1 Flash, Kimi K2.6), explicit gateway routing, reuse an existing OpenRouter key only when its configured gateway matches.
- [x] Native worker settings: local codexx account discovery, Claude config profiles, model, effort, fast capability checks, and missing-runtime state.
- [x] Parent-scoped `native_worker` start/status/stop/resume tool; four concurrent subprocess workers, native session IDs, durable JSON/JSONL records, streamed output and scoped stop controls.
- [x] Opt-in project-folder session discovery and import dialog. Discovery and Cancel do not import. Imports create separate DAN history; Codex uses thread/fork before native continuation and Claude uses --fork-session. Source transcripts are never rewritten.
- [x] Missing Antigravity CLI is disabled when absent; agy 1.2.5 is now installed locally and awaits sign-in; its documented local cache is discoverable, but import remains disabled because a headless fork contract has not been verified.
- [ ] Live authenticated acceptance for each CLI and native forks; current coverage uses subprocess/adapter fixtures and actual installed capability inspection.
- [ ] Restart-resume/reconnect UI and approval transport; current parent exit stops live children, persisted unfinished workers display interrupted after restart. Resume tool operates within the active parent.
- [x] Team progress strip and task-lane side panel: current action per worker, attention-first priority, settled results, scoped Stop, expandable recent actions/output.
- [ ] Live browser thumbnails in team lanes once a worker emits browser screenshots.
- [ ] Dedicated child drawer with full transcript paging; current UI has expandable output and scoped stop, with full raw events saved on disk.

### Verified runtime contracts
- Codex local CLI 0.154.0 help and generated app-server schema (`thread/fork`, `ThreadForkParams`).
- Claude local CLI 2.1.66: low/medium/high effort, stream-json, --resume/--fork-session. Headless fast requires a newer CLI; disabled locally.
- Antigravity: https://antigravity.google/docs/cli/headless/ and https://antigravity.google/docs/cli/commands/resume . Installed official agy 1.2.5 on September 17; `agy models` confirms first sign-in is still required.
- Fast settings: https://developers.openai.com/codex/speed and https://code.claude.com/docs/en/fast-mode . Availability remains model/account-dependent.

### Validation
- 82 backend tests, 172 frontend tests, and production build/bundle budgets pass.
- Browser checks: OpenRouter selection, codexx accounts, three runtime capability states, explicit source choice, disabled unsupported import, and Cancel without writes. Screenshots in `output/playwright/native-workers-settings.png` and `native-session-import.png`.

- [x] Install official Google agy 1.2.5, verify checksum/version/headless flags, and confirm DAN discovers it. User sign-in remains required.

- [x] Raise native transcript import size limit from 32 MB to 128 MB.

## Agent roles
- [x] Present roles as Lead and Team, separate from provider/model choices.
- [x] Wire Codex/Claude/Antigravity leads and DAN team members with separate account/model settings, scoped delegation bridge, durable native continuation, imported forks, streaming, queued follow-ups, and stop cleanup.
- [ ] Complete authenticated mixed-agent acceptance: Codex lead fixed-reply smoke passed; Claude timed out after 45 seconds; Antigravity awaits login. Tests cover all three CLI protocols and DAN delegation using fixtures.
- [x] Add Cursor (`agent` CLI) as a lead/team runtime with Plan/Auto/Full mapping; docs: https://cursor.com/docs/cli/headless and https://cursor.com/docs/cli/reference/parameters.
- [x] Import Cursor editor chats as history copies (read-only store access).
- [ ] Install Cursor CLI, sign in, and run a live Cursor lead/team smoke; confirm stream-json session/result fields; add CLI chat (`~/.cursor/chats`) import once a CLI chat exists to inspect.

## September 19 live review
- [x] Run two built-in Codex review subagents and inspect the current DAN run through the local API. It returned no native workers; built-in collaboration is not connected to the Team display.
- [ ] Repeat the visible demo after enabling Codex in Team before starting a turn; no delegation bridge was attached to the reviewed turn.
- [x] Mirror direct native Codex subagents into Team automatically using selected-account, parent-linked rollout events; no enabled Team delegate required.
- [ ] Fix Cursor legacy discovery fallback reintroducing excluded subagent/archived headers; surface the 20,000-bubble transcript limit instead of silently truncating.

### Native Codex display integration
- [x] Read indexed session rollouts incrementally, tolerate partial/oversized lines, and preserve account/parent/run isolation and shared session-directory symlinks.
- [x] Persist observed child identities, command activity, terminal state, and final replies; omit unsupported per-child Stop controls and reject that API action.
- [x] Verify with isolated adapter/UI tests and replay the two actual September 19 review agents without modifying native records.
- [ ] Install the rebuilt client and restart the backend when the active conversation can end; verify a fresh live spawn in the installed app.
- Validation: 31 backend tests and 6 focused UI tests pass; production typecheck/build/budgets pass. The arm64 app is rebuilt, ad-hoc signed and signature-verified with the installed icon preserved; not installed over the active app.
- [x] Skill pool: share Codex/Cursor skills with Claude Code through a DAN-owned symlink directory.
- [ ] Extend the skill pool to Codex and Cursor receivers once each CLI has a non-invasive way to load an extra skills directory.
