# Architecture

## Product stack

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

Electron no longer owns terminals, Git/GitHub, LSP, debugging, extensions, marketplace adapters, content bootstrapping, MCP, or update UI.

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

- `editor/src/components/workbench/` owns the neutral Work presentation, native dialog project carousel/session wheel, transcript/activity disclosures, and scoped responsive styles. `ChunkWorkspaceApp` retains Agent V2 execution, persistence, Notes, attachments, and file previews.
- `navigation.ts` reconciles eight per-project slots against the newest sessions by creation instant, retaining surviving positions; local key `dan.workbench.sessionSlots.v1` stores slot IDs. Edits do not reorder them.
- `eventPresentation.ts` normalizes recorded backend identity and native tool/reasoning disclosures; token deltas, accumulated snapshots, and full messages remain distinct.
- Work layout uses `dan.chunkWorkspace.layout.v3`, migrating Notes preferences from v2/v1 while opening the project/chat sidebar and closing Files by default. Surface themes are scoped to Notes; Work follows light/dark appearance with neutral tokens.
- The sidebar groups chats by project with collapsible folders, search, and an Archived view. `ProjectSettings.tsx` supplies project name/folder creation and editing without the legacy Work settings toolbar. Native worker lifecycle integration is specified in `docs/UI-plans/2-native-agent-workers.md` and is not implemented by the presentation layer.

- Project folder drop uses `folderDrop.ts` → optional `nativeFs.droppedDirectory(File)` → Electron preload `webUtils.getPathForFile` → `fs:droppedDirectory` directory validation. Both TS and shipped CJS preloads expose the bridge. Browser-hidden paths are never reconstructed from folder names.
- Wheel directions track held keys per dialog; arrows/WASD combine into a compass vector, while Q/E/Z/C choose diagonals directly. Key release retains the selection, and close/window blur clears held state.

- `dan-native-window` scopes native title-bar drag regions to Electron. Work/Notes header controls and switcher dialogs opt out; blank header space uses OS drag/double-click behavior.

## Native workers
- `src/dan/native_workers/`: local account/capability catalog, parent-scoped subprocess service, session discovery and native fork preparation. Credentials stay server-side. CLI subprocesses receive isolated account environments; the server environment is unchanged.
- A ContextVar binds `native_worker` to the current Super DAN parent run. The tool is only added to mutation-capable stages when workers are enabled. Start returns immediately; status waits at most ten seconds; parent completion/cancellation stops remaining children.
- `graphs/native_workers/` stores worker JSON records and append-only JSONL events; `graphs/native_imports/` stores approved source references for newly created DAN chats. Sources are never moved or overwritten. Imported Codex/Claude continuations always fork.
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
