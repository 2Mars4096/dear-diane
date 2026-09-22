# Architecture

## Design artwork
- User-selected ivory v7 and its generation prompt live in tracked `editor/resources/icons/`, alongside PNG/ICNS/ICO exports for desktop packaging; `editor/public/favicon.png` is the browser export. Unselected generated variants were deleted. Local macOS updates retain their existing icon-preservation behavior.

## Latest-request editing
- `WorkbenchConversation` lazy-loads `RequestActions` for the latest user message. It owns the temporary edit/error state; `ChunkWorkspaceApp` validates the request, preserves attachments, persists replacement history, and uses the existing run admission path. Admission failure restores the prior transcript. Editing is disabled while a reply is pending.
- Work settings are lazy-loaded on opening to preserve the workspace shell bundle budget.

## Saved attachment links
- `WorkbenchConversation` lazy-loads `MessageAttachment` to open persisted `ChatAttachment.path` through Electron `shell.openPath`, retaining the original filename as the link label. Browser use relies on `workspaceFilePreviewUrl`; the file is not loaded until opened.

## Session identity and discovery
- ChatStore derives placeholder names from the first nonempty user request for snapshots, native append journals, and legacy reads. Read-time recovery does not mutate saved history; existing meaningful titles remain stable. Empty projects have no implicit session, and restoration is scoped to their active thread selection.
- Durable chat `id` is the user-facing session ID; `workflow_id` remains part of the transcript address. Run IDs and provider-native session IDs are separate execution identities. No alias registry is introduced.
- `GET /api/chats?q=` optionally filters summaries by ID/title/workflow. `GET /api/chats/{workflow_id}/{thread_id}` retrieves history. Native lead prompts include their own ID and the saved-session directory/API lookup contract, with on-demand history inspection.

## Queue transcript projection
- `queuedTranscript.ts` filters waiting user messages and queue acknowledgements from Work's visible transcript using queue command payload client IDs; legacy records fall back to text plus a known queue acknowledgement. Durable chat history remains intact. Delivered user messages become visible, and promoted runs target their own queued assistant ID.

## Activity disclosure
- Lazy `ActivitySummary` translates observed action summaries into concise progress labels without predicting results. Completed replies expose Work details rather than event counts; original records stay inside the disclosure.

## Product stack

Planned remote deployment (not implemented): [6-remote-control](plans/6-remote-control.md) keeps the backend, native CLI processes, credentials, and durable records on the execution host. An authenticated `ny` gateway serves the existing client and proxies through a host-owned SSH tunnel. Desktop and phone become clients of the same host; the bootstrap laptop is not required after setup.

```text
Work/Notes GUI + Super DAN TUI
            ↓
Agent V2 task/run/event control plane
            ↓
Super DAN plan and local tool runtime
            ↓
Universal Organism scheduler
            ↓
Universal Cell + typed WorkerBrief
            ↓
provider / tools / sandbox / memory / structured output
```

There is one active agent product. Task families such as coding, research, design, games, academic work, or market analysis are brief/blueprint shapes, not separate runtimes or GUI modes.

## Python layout

- `src/dan/worker/cell.py` — fixed Universal Cell system prompt and `build_cell`.
- `src/dan/worker/brief.py` — `RoleSpec`, `WorkerBrief`, prompt rendering, and execution-request conversion.
- `src/dan/worker/contracts/` — sampling, output, failure, recovery, snippet, and prompt-context contracts.
- `src/dan/worker/core/` — cell execution interfaces, capabilities, memory, acquisition, and structured output.
- `src/dan/worker/scheduler/` — task/dependency contracts, deterministic policy, specialized scheduler worker, and replay analysis.
- `src/dan/worker/organisms/universal_organism.py` — the only general organism runner.
- `src/dan/worker/organisms/super_organism.py` — Super DAN plan/report composition.
- `src/dan/worker/organisms/local_runtime.py` — local provider/tool loop for Super DAN.
- `src/dan/task_blueprints.py` — protected semantic task contract and revisioned execution-attempt models.
- `src/dan/providers/` — provider protocol, registry, retries, and OpenAI/Anthropic/Google adapters.
- `src/dan/tools/` — selectively loaded capabilities. Optional dependencies must stay lazy.
- `src/dan/sandbox/` — operational subprocess limits used by the retained shell tool; not an OS security boundary.
- `src/dan/skills/` — skill discovery, selection, and loading.
- `src/dan/server/` — durable stores, Agent V2 contracts/backends, and minimal FastAPI composition root.
- `src/dan/cli/` — server lifecycle, Super DAN, hooks/blueprints, and TUI entry points.

## Server composition

`src/dan/server/app.py` mounts four routers:

- workspace/notes/health (`routers/misc.py`);
- session CRUD (`routers/sessions.py`);
- Agent V2 task/run/event control (`routers/chat_v2.py`);
- native runtime discovery, worker inspection/stop, and opt-in session imports (`routers/native_workers.py`).

New GUI chats use their workspace ID in the existing session `workflow_id` namespace. Legacy `_scratch` records remain readable; unassigned records appear in Other chats without a project folder. Project registries and older thread bindings still live in profile-local storage, so cross-profile project synchronization remains pending.

Durable session records use `ChatStore`. Task/run/queue/event records use `ChatV2Store`. Both are initialized under `DAN_GRAPHS_DIR`; interrupted Agent runs are recovered on startup.

- FastAPI lifespan performs restart recovery and owns a cancellable queue-resumption task. `resume_recovered_queues` promotes pending follow-ups from restart-stopped runs through the normal background executor, defers behind active same-chat work, and retains persisted execution settings. Run policies are saved before backend execution; older native account/model settings can be recovered from `native_leads` records. Delivery is acknowledged only through the existing completion path. Explicitly stopped/paused work is not selected for automatic restart.

The public product API includes:

- `/health`, `/api/health`;
- `/api/chats/**`;
- `/api/v2/tasks/**`, `/api/v2/agent-runs/**`;
- `/api/workspace-roots`, `/api/workspace-files/**`;
- `/api/workspace-notes/**`, including preview and learning progress;
- `/api/workspace-skills` and read-only `/api/workspace-wireguard`.

## GUI architecture

`editor/src/components/workspace/ChunkWorkspaceApp.tsx` is the React application root. Work and Notes share session, composer, attachment, file, preview, and Agent V2 contracts.

Electron is intentionally narrow:

- start/stop and proxy the local backend;
- choose/read/write temporary or workspace files;
- open trusted local paths/URLs;
- watch files.

Electron no longer owns terminals, Git/GitHub, LSP, debugging, extensions, marketplace adapters, content bootstrapping, or MCP. Desktop update lifecycle is now supported.

## Invariants

- `build_cell(...)` is the only worker-construction primitive.
- Task behavior enters through `WorkerBrief`; infrastructure does not encode domain roles.
- `execute_universal_organism(...)` is the general organism executor.
- Deterministic policy admits scheduler/semantic proposals before state mutation.
- Validation and evidence gate terminal success.
- Provider/tool imports remain lazy so unused optional dependencies do not block startup.
- Relative workspace paths are bounded to the selected root; destructive authority is never inferred from a task family.
- Git history, not a source `_archive` tree, stores retired implementations.

## Repository hygiene

- `graphs/chats/` and `graphs/chat_v2/` are the retained repo-local session/task state; `.dan-super/` is retained TUI/run state.
- Dependencies and build products such as `editor/node_modules`, frontend bundles, Flutter tool state, APKs, and language caches are regenerated locally and are not part of the source tree.
- Legacy examples, standalone generated websites/animations/reports, old Code/Research histories, root checkpoints, and prior engine memory are absent from the active workspace.
- Keep new product artifacts in an operator-selected workspace rather than scattering generated deliverables across the repository root.

## Testing boundaries

- Worker tests cover cell/brief contracts, scheduler policy/replay, context/memory, organism execution, local runtime, and Super DAN.
- CLI tests cover dispatch, live-provider resolution, lifecycle commands, blueprints/hooks, Super DAN, and TUI.
- Server tests cover path resolution, Agent V2 state/control, and the integrated Work/Notes/session API.
- Frontend tests cover the retained workspace components and API clients.
- Eval tests cover Super DAN capability, flagship browser-artifact, and human-assist gates.

## Workbench presentation

- `lib/workbenchPalette.ts` defines the eight paired light/dark palettes and six semantic root tokens. `appearanceTheme.ts` applies the chosen palette at initial boot and on settings/OS appearance changes; workbench CSS aliases these tokens for surfaces and controls. Settings v7 persists `workbenchColorScheme` independently of Light/Dark/System and Notes tone.

- `editor/src/components/workbench/` owns the neutral Work presentation, native dialog project carousel/session wheel, transcript/activity disclosures, and scoped responsive styles. `ChunkWorkspaceApp` retains Agent V2 execution, persistence, Notes, attachments, and file previews.
- `navigation.ts` reconciles eight per-project slots against the newest sessions by creation instant, retaining surviving positions; local key `dan.workbench.sessionSlots.v1` stores slot IDs. Edits do not reorder them.
- `eventPresentation.ts` normalizes recorded backend identity and native tool/reasoning disclosures; token deltas, accumulated snapshots, and full messages remain distinct.
- Work layout uses `dan.chunkWorkspace.layout.v3`, migrating Notes preferences from v2/v1 while opening the project/chat sidebar and closing Files by default. Surface themes are scoped to Notes; Work follows light/dark appearance with neutral tokens.
- The sidebar groups chats by project with collapsible folders, search, and an Archived view. `ProjectSettings.tsx` supplies project name/folder creation and editing without the legacy Work settings toolbar. Native worker lifecycle integration is specified in `docs/UI-plans/2-native-agent-workers.md` and is not implemented by the presentation layer.

- Project folder drop uses `folderDrop.ts` → optional `nativeFs.droppedDirectory(File)` → Electron preload `webUtils.getPathForFile` → `fs:droppedDirectory` directory validation. Both TS and shipped CJS preloads expose the bridge. Browser-hidden paths are never reconstructed from folder names.
- Wheel directions track held keys per dialog; arrows/WASD combine into a compass vector, while Q/E/Z/C choose diagonals directly. Key release retains the selection, and close/window blur clears held state.

- `dan-native-window` scopes native title-bar drag regions to Electron. Work/Notes header controls and switcher dialogs opt out; blank header space uses OS drag/double-click behavior.

## Native workers
- `workbench/modelSelection.ts` defines saved harness profiles and independent model-source choices; source switches retain model/reasoning/fast settings separately. `ModelFields.tsx` renders the shared capability-aware controls for Lead and Team. The workspace migrates legacy DAN model choices into lead profiles and uses one payload helper for main and reader/sidecar execution, recording the selected provider/model/options in run metadata.
- `native_workers/models.py` separates the harness from its model source, validates supported OpenRouter combinations, resolves credentials server-side, and scopes DAN reasoning request options to one provider instance. Catalog `sources` describe support and configuration without credentials.
- Codex receives a DAN-owned OpenRouter provider table through per-process `-c` overrides; its existing stdio app-server receives the matching `modelProvider` on start/resume. Claude receives a scoped gateway environment and an isolated config directory under `graphs/model_accounts/claude_openrouter/<account-hash>`, preserving native login state. Native lead continuation keys include non-native source identity; switching sources does not reuse the other source's native session.
- Server CLI startup and `create_app()` load `.env` before resolving runtime paths/catalog credentials; existing process environment retains precedence.
- `src/dan/native_workers/`: local account/capability catalog, parent-scoped subprocess service, session discovery and native fork preparation. Credentials stay server-side. CLI subprocesses receive isolated account environments; the server environment is unchanged.
- A ContextVar binds `native_worker` to the current Super DAN parent run. The tool is only added to mutation-capable stages when workers are enabled. Start returns immediately; status waits at most ten seconds; parent completion/cancellation stops remaining children.
- `graphs/native_workers/` stores worker JSON records and append-only JSONL events; `graphs/native_imports/` stores approved source references for newly created DAN chats. Sources are never moved or overwritten. Imported Codex/Claude continuations always fork.
- `editor/src/components/reader/`: `InteractivePdfViewer.tsx` + CSS module and `lib/` are ported from learning-assistant's material guide (keep diffs minimal; DAN changes are commented). `ReaderView.tsx` wraps it for project PDFs (served by `/api/workspace-files/preview`), stores comments/refs/position in localStorage per path, and hands Ask to `SidecarChat` via its `purpose` prop. Loaded lazily; pdf.js worker is bundled with `?url`. `lib/ocr*.ts` run tesseract.js in the browser (assets from `public/tesseract`, copied by `scripts/copy-ocr-assets.mjs`), `lib/paper-anchors.ts` + `lib/text-layer.ts` re-find highlights, `lib/annotated-pdf.ts` exports highlights with pdf-lib; `server/routers/reader.py` stores OCR pages under `graphs/reader_ocr/` keyed by path and file identity.
- `dan/processes.py` (ProcessManager) + `/api/processes*`: DAN-owned long-running processes (own session, log file, PID re-attach). `native_workers/proc_bridge.py` is the sandbox-safe file RPC every native lead gets; `tools/managed_process.py` is the DAN-lead tool; `workbench/ProcessesPanel.tsx` + `processes.ts` are the side tab.
- `native_workers/skills.py` + `GET/PUT /api/skills`: cross-CLI skill pool (symlinks under `graphs/skill_pool/<runtime>/`, passed to Claude via `--add-dir`); `workbench/SkillPool.tsx` is its settings section.
- `native_workers/token_usage.py` + `/api/token-usage/{sessions,session,overview,classify,settings}`: per-session token profiler over native transcripts (`<claude home>/projects/*/*.jsonl` + `subagents/`, Codex `sessions/YYYY/MM/DD/rollout-*.jsonl` + children by `parent_thread_id`). Normal form: session → rounds → model calls and steps; step cost = tokens added × later calls before the next compaction. Claude usage is deduped by API message id. Labels come from `ACTIONS`/`STAGES` rules; `classify()` refines stages with a decision model via OpenRouter `POST /api/alpha/decisions` (default `jev-latest`, confidence ≥ 0.4, otherwise the rule stands). State lives in `graphs/token_usage/` (`summaries.json` keyed by path+mtime+size+`CACHE_VERSION`, `labels/`, `settings.json`). DAN runs join through `native_session_id`. UI: `workbench/TokenUsage.tsx` (lazy Settings section + analyzer dialog), pure helpers in `tokenUsageFormat.ts`.
- `native_workers/usage.py` + `GET /api/usage`: quota per recently used account (Codex rollout rate limits; Claude OAuth usage via keychain token read server-side). `workbench/UsagePanel.tsx` is the hover panel in the sidebar footer.
- `workbench/SideTabs.tsx`: the single right side panel. `ChunkWorkspaceApp` keeps one `sideTab` value; the old per-panel booleans are derived views of it.
- `NativeWorkers.tsx` renders capability-aware settings. `TeamProgress.tsx` (strip + Team panel) and `teamPresentation.ts` (pure state/priority/wording rules) show team progress; `service.describe()` turns native events into the short `activity`/`actions` fields they read. `ImportNativeSessions.tsx` requires an explicit source selection and Import as fork; no automatic import. Native history matching uses canonical recorded cwd, not project names.
- Authentication/live fork acceptance, cross-restart native resume UI, and approval transport remain pending. Antigravity requires installed/authenticated agy; its imports remain disabled until a headless fork is verified.

- `ProjectMenu.tsx` uses native popovers and a confirmation dialog for project actions. `Workspace.removedFromDan` is a profile-local visibility marker; the workspace record and its thread bindings remain intact. Session grouping includes hidden records for ownership resolution but omits their groups/chats, preventing orphan recovery from resurrecting removed projects. No file/session deletion API is called.

- Recovered session groups register into the local project store only when an action needs an ID. `createWorkspace(..., activate=false)` preserves the current selection; complete-group binding avoids losing chats when search filters are active. Group ownership falls back to folder matching across profiles.

- `workbench/DanSettings.tsx` exposes profile-wide Notes appearance and native-worker preferences. Project menus own folder-scoped session imports; batch import retains failed selections and removes successful ones before retry.

- `workbench/LeadAgentMenu.tsx` discovers available lead runtimes. Separate persisted lead/team profiles provide account/model/effort/fast settings. `native_workers/lead.py` runs Codex/Claude/Antigravity leads, persists account/folder/thread-scoped native continuation IDs, forks imported sources, and streams normalized events. `native_workers/bridge.py` exposes run-scoped start/status/resume/stop through a temporary workspace file queue for CLI shell tools; enabled team members may include DAN. Children are leaves and inherit parent policy for DAN execution. Stop closes the lead, team, and bridge. Native follow-ups queue for the next turn.

- Composer exposes only Lead and Team agent controls. Lead embeds shared runtime fields (`NativeWorkerSettings` inline); Team uses a runtime selector and independently retained profiles. DAN model choices remain in the Lead dropdown; unsupported DAN reasoning/fast overrides are disabled.

- `workbench/useDismissDetails.ts` shares pointer/focus outside dismissal between Lead and Team menus, with listener cleanup.

- `workbench/SidecarChat.tsx` executes a separate ChatV2 thread with the selected lead and bounded parent context. `dan.sidecar.v1:<workflow>:<parent>` stores the side-thread/pending-run reference; reopening resumes polling. Creation validates and saves same-workflow parent lineage. Markdown memoizes its HTML prop object to preserve native text selection across unrelated renders.

- Composer references are scoped by workflow/thread, retain selected message IDs, and serialize as quoted context in the request. A removable preview keeps quotes outside draft text. The textarea measures content and responds to width changes, capping at six lines.

- `native_workers/codex_children.py` observes direct Codex children omitted by `exec --json` (verified CLI 0.155.1). It uses the selected account’s read-only state index for exact rollout paths, reads typed `SubAgentActivity` parent events and child activity/results incrementally, filters by current run time/child turn, and writes only DAN-owned worker records. Observed children have `origin=codex_subagent` and `can_stop=false`; their lifecycle remains controlled by Codex. Lead shutdown marks unresolved observations interrupted.

## Desktop updates
- `electron/desktopUpdates.ts` owns main-frame-only update IPC, electron-updater release state, explicit download/install, active/queued-work checks, and backend ownership checks. Both preload variants expose the same narrow status/action bridge.
- `electron/localUpdate.ts` verifies local macOS identity/architecture/signature and stages a same-filesystem bundle; `updateInstaller.cjs` runs independently, waits for app/backend exit, renames with rollback on replacement/open failure, and relaunches. Local installs preserve the existing icon and re-sign ad hoc. Published signatures are handled by electron-updater, not rewritten.
- `workbench/DesktopUpdates.tsx` is lazy-loaded in DAN settings. Release channels come from packaged app-update.yml; unconfigured builds say so. Downloads never auto-install on quit.
- User data stays outside the app. The current Python backend is separately installed and restarts from its existing environment; this updater does not run Git/pip/npm upgrades. `editor/scripts/install-local-update.cjs` bootstraps older clients with a native confirmation after work is idle.

- `electron/githubAuth.ts` manages cancellable GitHub CLI browser authorization, device-code-only presentation, cached connection status, and private-update credentials. `desktopUpdates.ts` supplies an in-memory token to the fixed private GitHub release provider; renderer IPC never receives credentials. Auto-updater logging is disabled and errors redact the active token.

### Codex lead steering transport
- `native_workers/codex_live.py` maintains a stdio app-server connection for runtime-backed Codex leads. Thread start/resume retains account environment and permission policy; turn/steer targets the existing active turn. Ordinary child workers keep their existing execution path.
- Lead readiness projects `live_steering` onto the run/task. The runtime claims append entries, acknowledges successful steering, and releases unacknowledged entries on failure. New runs clear stale capability metadata.
- `POST /api/v2/agent-runs/{run_id}/queue/{item_id}/steer` moves an existing waiting entry to append delivery after active-run/capability checks. Persisted list order is unchanged; explicit steering can bypass continue-after-current entries.
- Lazy `FollowupQueue.tsx` renders queue actions and errors; composer capability follows the active run, independent of the next selected lead. Older exec-based native runs cannot gain a live connection retrospectively.
