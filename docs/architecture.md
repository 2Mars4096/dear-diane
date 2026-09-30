# Architecture

## Provider-reported Personal billing
- `personal/billing.py` persists per-attempt intent, UTC call day, upper-bound hold, generation ID and provider-reported USD in existing private job JSON. No schema migration is needed. New undispatched jobs cost zero after cancellation; old jobs lacking receipts remain unverified.
- `OpenAIProvider` preserves generation ID and `usage.cost` in completion metadata. `BriefCellAdapter` journals intent before each provider call and settles the receipt before output validation, including repair calls. Confirmed costs replenish the in-turn allowance; missing/invalid costs never become zero.
- Admission reserves at most the remaining daily/task headroom. Running holds protect concurrent admission; terminal jobs count actual costs plus only unresolved attempted-call holds. The compatibility `used_reservations_usd` field now represents their combined total. Status separately returns `actual_usd`, `held_usd`, `unverified_usd` and `accounted_usd`.
- Voice stores `X-Generation-Id` before reading response bytes and inline transcription usage when available. A lifespan-owned worker checks unresolved IDs every 30 seconds using the OpenRouter generation endpoint, validates identity, and rotates lookup order. It performs no paid retries. Receipts without IDs cannot be reconstructed automatically. Search/account fees remain outside this ledger.

- Personal voice now emits cumulative audio previews about once a second, with an earlier snapshot at a brief phrase break through the existing budgeted transcription endpoint, at most one preview in flight. Interim text is visible but never admitted as an action. VAD allows four seconds of quiet, and resumed unsubmitted PCM audio is joined within the existing 30-second bound. Generation/abort checks discard stale recognition results. Preview transcripts share the existing private audio journal and reservations; final recognition alone supplies conversation input. Connection polling owns a separate recoverable status flag.

## Personal public research
- `personal/research.py` reuses Workspace search/fetch, with at most two rounds of two queries, three selected pages and two linked menu pages per round. Official-domain matches get priority without starving the other query. Sources, timestamps, snippets/read failures and progress persist in the existing conversation body; the API exposes only references and progress.
- `tools/_public_web.py` backs `web_fetch(public_only=True)`: public HTTP(S), DNS/IP pinning with original TLS identity, validation at every redirect, no environment proxy, 2 MiB responses, bounded text/PDF parsing. It does not use logged-in browser sessions or local network access.
- Conversation research uses up to four model calls, with at most two per brief including repair. `budgets.CallAllowance` charges input UTF-8 bytes plus framing and the full output ceiling before every attempt against the unchanged durable USD reservation. Confirmed costs settle holds; uncertain calls retain their own bounds; no restart replay. `BriefCellAdapter.charge_completion` is optional, leaving existing extraction behavior unchanged. Conversation leases/voice waits allow 720 seconds for the bounded pipeline.
- Research evidence cannot select a local mutation. Chat/voice share the same validated result. Stop cancels the pipeline. Search-provider fees are separate from model reservations.

## Browser app and personal organizer pilot

- `personal/voice.py` adds bounded OpenRouter STT/TTS and fixed four-voice mappings. Schema v7 adds private `voice_operations` intent/reservation/transcript records and a v6 backup; raw audio stays transient. Audio reservations share AI budgets, with cached catalog checks rather than upstream price enforcement.
- `lib/personalVoice.ts`, `VoiceControl.tsx` and `public/app/voice-capture.js` own continuous microphone capture, local VAD, PCM resampling, speech playback, interruption and teardown. Voice uses existing conversation jobs; optional profile and interrupted-turn IDs preserve conversational context. Native metadata declares microphone usage and the voice proxy allows 70 seconds.

- `PersonalConversation.tsx` is the `#personal` entry. One composer, local unsent draft, attachments/OCR, durable turn polling and Stop; the existing organizer stays available under Activity. The manifest launches this conversation.
- `personal/conversation.py` maps a versioned tool-free brief to one validated local service operation through Agent V2 and BriefCellAdapter. Schema v6 adds `conversation_jobs` with private v5 migration backups. Conversation and extraction reservations share the operator limits. Each turn stores source evidence, model, reservations, state and a deterministic receipt. Unknown provider/apply outcomes are never automatically repeated. Existing source/record/lifecycle/reminder APIs remain authoritative.

- Personal confirmed records open as summaries with an explicit editor. Reminder rescheduling uses the existing reminder instant/timezone and the transactional schedule operation, which replaces outstanding reminders atomically. Empty reminder inboxes are hidden; settings follow the main work area.

- `personal/budgets.py` reserves conservative USD model usage in each extraction job JSON within the admission transaction. Schema v5 adds owner-scoped `personal_settings` with optimistic revisions and private migration backups. User limits cannot exceed host environment ceilings; the pause switch is checked before each model call, including repair. Receipts and unverified attempted-call holds persist through failures/restarts. Execution rechecks current caps and reserves another UTC day when needed. `BriefCellAdapter` bounds both input bytes and output tokens and forwards scoped provider price constraints. The first enforced-price route is explicit OpenRouter model IDs; other routes are blocked for personal extraction.
- `connectors/actions.py` and `connectors/mock.py` provide a mock-only calendar action ledger and provider simulator. Private separate SQLite proposals persist intent before effects. Hashes bind exact payload/account/source revision/expiry; verified read-back receipts survive uncertain responses. Reconciliation performs no repeat write and may read an uncertain result after approval expiry. This is not a credential/authentication boundary and has no HTTP or runtime-tool exposure; real adapters are rejected until an isolated broker exists. `python -m dan.connectors` runs a temporary synthetic capture-to-reconciled-update demonstration and can emit a JSON report.

- `dan.personal.__main__` launches one loopback server using this checkout’s built client and separate `.personal-local/` state (or explicit `--data-dir`). It does not replace installed app/backend state.

- `public/manifest.webmanifest`, `public/sw.js`, `public/app/` and `lib/webApp.ts` provide installation metadata and a generic offline fallback. Only that fallback is cached; authenticated HTML/APIs/files stay network-only. Registration requires a production secure browser context and excludes Electron. Fixed installation assets bypass remote login after Host/Origin validation; other routes retain authentication.
- `WebAppInstall` in Settings provides installation controls/instructions; the startup listener retains the browser install event before lazy Settings mounts. The app shell owns phone safe-area padding.
- `#personal` lazy-loads `components/personal/PersonalApp.tsx`. An opened Work workspace stays mounted while hidden, preserving document drafts and active chat state. The typed `personalApi` uses shared `lib/http.ts`; unsent capture text/operation ID uses origin-local storage, and blocked storage is reported.
- `dan.personal` owns schema-v7 models, SQLite operations/records/business events and ICS serialization. `routers/personal.py` exposes opt-in `/api/personal/{capabilities,captures,commitments}`, detail/review and `calendar.ics`. Enable with `DAN_PERSONAL_ENABLED=1`; disabling preserves files. Single operator per deployment; client-supplied owner IDs are rejected.
- `DAN_GRAPHS_DIR/personal/state.sqlite3` is mode 0600 inside a 0700 directory. Transactions serialize writers; canonical hashes bind operation IDs to exact results, while changed requests/stale revisions conflict. Business events commit with records. No dependency or chat-history migration was added.
- Confirmation needs explicit date/time/IANA zone or all-day choice; DST folds/gaps require correction. ICS has stable UID/revision, escaped/folded UTF-8 lines and UTC timed events; no guests, invitation method, invented duration or calendar-insertion claim.
- `chat_v2_dispatch.py` journals named admissions with deterministic task/run IDs and cross-process locking; personal extraction uses the existing Universal Cell executor through `cell_backend.py`. `personal/runner.py` owns a bounded extraction queue and a separate five-second reminder loop. Saved model results reconcile without paid replay; unknown interrupted work requires a new user request.
- `personal/reminders.py` commits wakeup delivery and unique inbox receipts atomically. SQLite schema v3 backs up v1/v2 before migration. Edits invalidate outstanding reminder timing. Mac desktop launches enable the local organizer unless explicitly disabled; `DAN_PERSONAL_MODEL` remains a separate extraction opt-in.
- `personal/intake.py` parses bounded text PDFs with the existing optional PyMuPDF dependency, EML with the standard email parser (body only), and UTF-8 TXT. Raw uploads use existing same-origin transport, one file per request; capture accepts up to five immutable source IDs. SQLite v4 stores originals as private BLOBs, extracted text and source metadata; capture commits references and records atomically. Original downloads require owner and commitment membership, use attachment disposition and nosniff. No multipart dependency was introduced. Original deletion clears the source BLOB with SQLite secure_delete enabled; referenced capture text and earlier backups are explicitly retained. Unreferenced source text is removed too. Source revisions and operation IDs protect transcription/deletion replay.
- `lib/personalOcr.ts` uses the existing Tesseract worker with bundled English, Simplified and Traditional Chinese data. PNG/JPEG images are limited to 10 MiB/20 megapixels, downscaled to a 4096-pixel edge and timed out after 90 seconds. Recognition stays in the renderer; originals and unverified text are then saved to the Mac host. Scanned PDFs still need transcription.
- `personal/lifecycle.py`, `TaskLifecycle.tsx` and `SourceLibrary.tsx` implement revision-bound task decisions and original management. Completion is explicitly user-reported; it is not a provider receipt. Task pause remembers which scheduled reminder it paused, avoiding accidental resumption of a manually paused reminder. Owner-scoped event cursors drive reconnect; commitment, lifecycle-note and reminder drafts are protected. Personal reuses document-level workbench palette tokens and system typography, with a compact two-column desktop layout and focus-managed phone detail navigation.
- Network notification outbox, personal facts and connector broker remain unimplemented. Stage 0 contracts/research live in `docs/personal/`; future broker tokens need a separate OS/process boundary.

## Product identity
- Public product: Dear Diane; assistant: Diane. Desktop packaging and browser/phone titles use the product name; agent messages and runtime labels use Diane.
- Electron pins `userData` to the existing `appData/dan` before setting its display name. Bundle ID, Python package, protocol IDs, environment variables, remote services, and saved keys retain their original identities. New bundles are `Dear Diane.app`; local update discovery uses that path.
- Session placeholder and status recognition supports old DAN records alongside new Diane output. `dear-diane` is an additional CLI entry point. See [naming plan](plans/4-10-dear-diane.md).

## Workspace lifecycle and file targets
- `documents/useDocumentTabs.ts` owns tabs, drafts, dirty/unload guards, document close callbacks, and browser URL cleanup. PDF files derive from the document registry, removing the second reader-file map. Library/paper modules remain lazy; switching tools does not release a tab.
- `workbench/projects.ts` shares registry creation, root reuse, edits, and selection; the workspace retains session sequencing and view changes. `lib/workspacePaths.ts` preserves existing normalization and legacy root aliases.
- `lib/fileTargets.ts` constructs explicit local/remote/browser-copy targets and preview URLs for documents and attachments. Display names do not change URL filenames; remote targets never invoke the local shell. Electron/backend path validation remains in place.

## Desktop window appearance
- `electron/windowAppearance.ts` gates initial reveal on both first paint and themed document load; tray and second-instance requests share that gate. HTML paints an opaque palette background; preload synchronizes palette changes to the native window through validated, main-frame-only IPC. Local services stop at `will-quit`, after windows close, preserving cancelled unloads.

## Shared metadata and durable writes
- `dan.notes_frontmatter` provides the existing lightweight Hugo parser to Notes routes and the paper catalogue without importing a router. It is not a general YAML parser.
- `dan._atomic_file` serves tools, runtime mutations, document editing, profiles, paper state, and the remote project registry. Existing workspace permissions are preserved; private state explicitly uses mode 0600 from temporary-file creation through replacement. Unique sibling temporaries, fsync, and failure cleanup are shared. The standalone SSH installer keeps its dependency-free writer.

## JSON transport and library subscriptions
- `lib/http.ts` owns JSON deadlines, caller abort propagation, header merging, and structured `ApiError` responses. API, papers, and SSH keep typed endpoint wrappers; ordinary requests default to 10 seconds, SSH inspection allows 45 seconds.
- The paper store owns one focus listener and 30-second poller for all subscribers, released when the final consumer unsubscribes. Failed/deadline-expired catalogue requests release the shared pending promise.

## Reading-session ownership
- Open document records own a synchronous close callback. Paper close flushes pending progress and releases clean sync state; dirty/blocked recovery survives. Reopening waits for an in-flight save and hydrates current server state, while an already active reader retains its position. Concurrent opens share one request.

## Remote project folders
- ProjectSettings detects the authenticated server's `dan-remote-machine` marker and replaces the desktop drop/picker with `RemoteProjectFolder`. The inline chooser uses the existing same-origin workspace-roots API, ignores stale responses, and returns a server-resolved path into the usual pinnedPaths/project registry flow. No SSH credentials or Mac paths enter this browser flow.

## PDF font compatibility
- InteractivePdfViewer always loads PDFs with `disableFontFace: true` and `useSystemFonts: false`, drawing glyph outlines locally without browser font conversion/substitution. No manual repair control or per-document preference remains; old stored preferences are ignored. PDF bytes are unchanged. Reader text/OCR callbacks ignore results from replaced documents.

## Persistent side tools
- Work owns one side-panel frame/header. `workbench/PersistentPanel.tsx` mounts a tool on first visit, then hides it without unmounting; chat polling and drafts continue, and scroll/expanded logs survive. Files and Preview use the same frame on desktop and phone. Project/PDF/thread keys isolate context-specific state.
- `MainTabs` handles the dedicated active-tab close shortcut and calls the existing guarded close callback. Native window shortcuts are not intercepted.

## Design artwork
- User-selected ivory v7 and its generation prompt live in tracked `editor/resources/icons/`, alongside PNG/ICNS/ICO exports for desktop packaging; `editor/public/favicon.png` is the browser export. Unselected generated variants were deleted. Local macOS updates retain their existing icon-preservation behavior.

## Latest-request editing
- `WorkbenchConversation` lazy-loads `RequestActions` for the latest user message. It owns the temporary edit/error state; `ChunkWorkspaceApp` validates the request, preserves attachments, persists replacement history, and uses the existing run admission path. Admission failure restores the prior transcript. Editing is disabled while a reply is pending.
- Work settings are lazy-loaded on opening to preserve the workspace shell bundle budget.

## Local update discovery
- Electron compilation emits an ignored `local-build.json` path marker inside the app. `preparedBuild.ts` checks that path, a remembered selection, or `DAN_LOCAL_UPDATE_PATH`; archive hashes identify same-version changes, cached by filesystem metadata. Detected builds still pass the existing signature/identity/architecture validation before staging.
- Update settings offer Install local update; checks prefer a prepared local build before GitHub. Provider errors are mapped to short messages without response headers or stacks.

## Document workspace
- Lazy `components/documents/` modules connect global file drops and Open file to main tabs. Desktop preload resolves File objects to original paths; browsers retain local File/blob copies and download edits explicitly. Existing project files open through the same tab path.
- Text draft/revision snapshots live in a workspace-owned ref across panel unmounts; dirty state guards tab close and page unload. PDFs reuse ReaderView; browser-only PDFs skip filesystem OCR cache.
- `routers/documents.py` provides bounded strict UTF-8 reads and revision-checked atomic writes under the existing workspace-root resolver. Saves preserve file permissions; stale revisions fail without replacing content. External programs do not participate in the in-process save lock.

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

## SSH setup
- SSH hosts render as compact rows; status is saved/installed until an explicit inspection succeeds. A native details dialog owns access-key reveal and opt-in installation settings, clearing secrets when closed; editor/phone forms remain separate.
- `RemoteConnections` uses a native modal dialog for name/hostname/port/authentication, mirroring the installed Codex workflow. Workspace and package options are progressive; phone relay configuration is a separate action.
- Profiles add optional `ssh_port`/`identity_file` and `relay_enabled`; missing flags retain legacy behavior. SSH-only profiles have no browser URL and cannot be deployed until a private relay is configured. Normalized comparisons retain installed state on legacy renames.
- Local-only `/api/remote/ssh-hosts` reads literal aliases and bounded Include files, returning names only. SSH/SCP share argument construction; target identity/port are never applied to the relay.

## Product stack

Remote deployment: [6-remote-control](plans/6-remote-control.md) keeps the backend, native CLI processes, credentials, and durable records on the execution host. `dan.remote.profiles` saves private local connection records and installs versioned releases using SSH; `install` provisions systemd user services and checks boot lingering. `relay` transparently forwards TCP over an existing private network, so HTTP streams and WebSockets share the same path without a laptop-owned tunnel. Each host uses its own relay port/browser origin. Relay SSH alias/address are profile fields, not a hard-coded `ny` dependency.

`RemoteAccess` protects every HTTP/static/file/WebSocket route on configured remote servers: per-machine access key, signed expiring HttpOnly session cookie, exact Host/Origin checks, login throttling, and no wildcard remote CORS. Plain HTTP is restricted to existing private/VPN interfaces; this is not public HTTPS deployment. SSH setup endpoints reject remote browsers and nonlocal origins. Profiles and keys are stored in mode-0600 ignored files, never browser localStorage. SSH uses pinned existing host keys, no agent forwarding, and independent connections to avoid stalled shared control sockets.

`remote_registry` persists project metadata with field-level patches. `remoteProjects.ts` hydrates before mounting the remote workbench and periodically syncs; transient tabs and selection remain local. Remote origin isolation applies uniformly to existing `/api` requests, previews, uploads, and event streams, and remote pages have no Electron filesystem bridge. Local legacy bindings are unchanged. Authenticated remote HTML carries a machine marker used for hydration and a visible machine label.

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

## Browser research

- `tools/_browser_session.py` binds a lazy controller to each GUI run through a ContextVar, including DAN delegates. Fresh contexts isolate chats and close on completion/cancellation; standalone tools retain their legacy persistent profile. Desktop-aware visibility honours `DAN_BROWSER_HEADLESS`.
- Ordinary Super DAN GUI requests default to the browser capability unless an explicit capability policy is supplied. Research navigation/click/fill/select are transient UI operations; this does not grant authority for external account changes.
- `native_workers/browser_bridge.py` exposes the existing browser tools through a copied stdlib-only client for native leads. Lead instructions prefer the ready connection for fresh tasks and an already-configured native integration when the user explicitly requests existing logged-in Chrome tabs. It validates actions/arguments, expires queued requests, restricts Plan actions, emits activity, and closes its controller on cancellation. No arbitrary browser evaluation endpoint is exposed.
- Scoped downloads require a new workspace path; screenshots live under `output/browser/<run-hash>/`. `browser_tabs(index=...)` selects popup tabs. The optional `browser` extra installs Playwright in the backend environment.
- `tests/eval/run_browser_research.py` is the single opt-in real-browser acceptance runner for fixture, Scholar, and authenticated native-lead paths.

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

- `docs/plans/` and `docs/*-plans/` are local, Git-ignored tracking documents. Existing working copies remain available; fresh clones will not include them after the untracking change is committed.

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

- `documents/FileOpener.tsx` routes external directory drops through the validated folder bridge into `ChunkWorkspaceApp.openDroppedProject`; file drops retain document opening. A `data-main-drop-area` element bounds the drag overlay. Remote connections reject local directory registration, and project-settings drop zones retain ownership.
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

## Paper library and reading sessions

- `server/paper_library.py` indexes configured Hugo projects (`content/papers` + `static/papers`) and PDF folders without modifying originals. Initial discovery reuses Notes candidates, skipping unavailable paper collections. Metadata parsing supports folded BibTeX and nested braces; Hugo identity uses source root + citation key. File metadata caches invalidate by mtime/size and PDF availability is rechecked.
- `server/routers/papers.py` exposes `/api/papers`, `/settings`, `/{id}/open`, `/{id}/pin`, `/{id}/pdf`, and `/sessions/{id}` with `/progress` and `/link`. Sources and reading records use private atomic JSON files under `graphs/papers/`; PDF links must remain within their configured source.
- One reading session per paper owns a durable ChatStore thread under `_dan_reading`. Coding-chat discovery excludes this namespace; Papers and the recent-reading sidebar own navigation. Progress writes use a revision check; conversation links validate thread/run ownership. Host records are shared across browsers, with no implicit transfer between hosts.
- `editor/src/components/papers/` owns lazy search/library/settings UI, unordered token/one-edit metadata retrieval, metadata + knowledge-base/annotation note search, source refresh on focus/30-second polling, and reading hydration/sync. `reading.ts` hydrates the existing reader before mount and coalesces position/comment/reference events. Failed writes retain a device-local recovery record and expose retry/download/reload.
- The existing PDF reader, Notes annotations, and SidecarChat remain the reading surfaces. Sidecar accepts a server-owned initial conversation link and persists run links before execution. Source library notes are shown read-only in Details; PDF annotations stay separate from Hugo Markdown.
- Library query/filter/view preferences and scroll remain device-local. Main tabs use lazy `MainTabs.tsx` with pure types/close behavior in `mainTabState.ts`. Cmd/Ctrl+K is reserved for papers; the project switcher keeps Cmd+Shift+P.

## Conversation file links
- Shared `MarkdownRenderer` preserves targets in sanitized `data-file-link` anchors and reads project context from `data-workspace-root` or the sidecar prop. Inline code paths use the same action path. Markdown rendering is bundled with the shared content libraries.
- `electron/fileLinks.ts` owns main-frame-only `shell:fileLink` IPC, local path/line-suffix resolution, stat validation, OS opening and native context actions. The TypeScript preload exposes the narrow bridge; `scripts/prepare-electron.cjs` renames its compiled CommonJS output to the runtime `.cjs`, leaving no handwritten duplicate. File copying is explicit through Save as; text clipboard reads require UTF-8 and a 5 MB bound. Remote/browser-only sessions show a fallback instead of opening local paths.

## Literature ingestion

- `components/papers/LiteraturePanel.tsx` is a lazy, persistent right-side Literature tool beside Chat/Files. Library search reuses the host catalogue; staged previews use `FileTarget` and open in the main reader. Literature owns drops within its surface, so the global document opener does not consume them.
- `routers/literature.py` exposes `/api/literature/batches` CRUD/intake/prepare/stop/apply and staged-PDF delivery. Uploads stream into private staging, bounded to 100 MB per PDF and 100 PDFs/1 GB per batch. Destinations must be configured Hugo sources containing `content/papers` and `static/papers`.
- `server/literature.py` saves batch state under `graphs/papers/imports/<id>/`, retrieves Crossref candidates (DOI with title fallback), and pairs supplied BibTeX by content. A single lifespan-owned worker uses the existing Agent V2 selected lead in Plan/read-only mode to produce a schema-validated match and grounded reading draft. `_dan_imports` sessions are hidden from ordinary chats/projects. Citation bytes come from supplied/retrieved records, never generated by the reading model.
- Ready items require a selected candidate, substantive notes, source anchors, and valid read-page numbers. Stop leaves queued items staged. Backend restart resumes queued items and flags active/interrupted imports for explicit retry. Drafts, corrections, PDFs, execution selection, and Agent run IDs persist independently of the browser.
- `server/paper_ingest.py` packages the deterministic ingest-paper-kb planner/writer without personal skill-path dependencies. Full selected-subset preflight checks keys, DOI/content duplicates, hashes, destination confinement, and existing files. Exclusive links publish verified PDF copies and Hugo pages; ordinary errors roll back only newly created files. Sources are retained. Existing pages are never overwritten or automatically merged; crash-interrupted writes require rechecking through Retry.
- Crossref coverage is incomplete, especially for books and working papers. Manual BibTeX/title correction remains available. PDF extraction uses the existing optional `pdf` dependencies; scanned-page OCR/rendering depends on the selected lead's tools. Book overview is distinct from a full chapter-by-chapter read. No Zotero or two-way synchronization is required.
