# Module Audit

Date: 2026-03-17

Scope: subsystem-by-subsystem audit of backend, engine, tools, providers, CLI, adapters, meta/runtime surfaces, and editor UX. This report focuses on:

- whether key features appear to function
- where the real regressions cluster
- what looks under-tested or hard to operate
- what would make the product easier to use

## Audit Method

I used the existing test suite as the primary functionality signal, then read the highest-risk code paths directly.

Representative checks:

- `pytest -q`
- `HOME=/tmp/... DAN_FURNACE_API_ENABLED=0 DAN_TELEMETRY_DB=/tmp/... pytest -q tests/test_server/test_api.py tests/test_server/test_mutation_regression.py tests/test_server/test_telemetry.py`
- `HOME=/tmp/... DAN_PROFILE_PATH=/tmp/... pytest -q tests/test_engine tests/test_concierge`
- `HOME=/tmp/... pytest -q tests/test_providers tests/test_provider_timeouts.py tests/test_openai_provider.py tests/test_google_provider.py`
- `HOME=/tmp/... pytest -q tests/test_tools`
- grouped passing suites for builder/loader/blocks/validation/publish/models/migration
- grouped passing suites for meta/adapters/cli/client/notifications/MCP/eval runner
- `HOME=/tmp/... pytest -q tests/test_furnace_api.py`
- `cd editor && npm test`

## Status Matrix

### Functioning Well

- `builder`
- `loader`
- `blocks`
- `validation`
- `publish`
- `models`
- `migration`
- `meta`
- `adapters`
- `cli`
- `client`
- `notifications`
- most of `server` once home-directory assumptions are neutralized
- most of `engine` and `concierge`
- most of `tools`
- `editor` library/test surface

### Regressions / Gaps

- `server` startup in restricted environments
- `server/routers/furnace`
- `engine` preference/domain learning
- `providers/google_provider.py`
- `tools/clipboard.py`
- product/docs safety model around file tools

## Subsystem Review

### 1. Server

Key features that appear to function:

- Graph CRUD and mutation API pass when run under a writable temporary home.
- telemetry API logic itself passes once the DB path is forced to a writable location
- mutation regression tests pass under sanitized environment settings
- chat/server integration looks broadly well-covered

What looks solid:

- The server has very strong test coverage depth.
- There is good separation between routers, startup wiring, and capability registration.
- The grouped server runs suggest the main API logic is not broadly broken.

What needs work:

- Startup is too eager about writing under `~/.dan`.
- `src/dan/server/startup.py`, `src/dan/blocks/registry.py`, and Furnace store initialization make the server operationally fragile in sandboxes, CI, and hosted deployments.
- `src/dan/server/chat_manager.py` is extremely large at about 4,000 lines. It may be feature-rich, but it is also a maintenance bottleneck and a test-locality problem.
- `src/dan/server/concierge/runtime.py`, `src/dan/server/run_manager.py`, and `src/dan/server/routers/furnace.py` are also very large and appear to be accumulating cross-cutting responsibilities.

Optimization / maintainability suggestions:

- Keep splitting the giant orchestration modules by lifecycle step rather than by helper type.
- Prefer explicit configuration objects over more module-level environment lookups.
- Add a startup “safe mode” path that skips all user-home writes unless the feature is actively used.

UX suggestions:

- If startup degrades because a home-directory feature cannot initialize, surface a visible warning in the app instead of failing the whole server.
- Make operational capabilities discoverable from one place: “storage paths”, “telemetry”, “Furnace”, and “block registry” should have a clear status page in the app.

### 2. Furnace

Key features that appear to function:

- session creation, listing, retrieval, tagging, variant inheritance, and the basic API lifecycle all pass in `tests/test_furnace_api.py`
- the research-mode API surface is present and reasonably featureful

Main issues:

- path safety issue for artifact writes using unsanitized `source_id`
- cancellation does not actually stop the running worker
- `start` / `resume` can queue duplicate workers due to delayed lock acquisition
- SSE progress is effectively single-subscriber only
- source identity generation can collide across different files/URLs

Test gap:

- The dedicated Furnace API tests pass, but they do not cover the failure-prone runtime cases above.
- Current tests are stronger on CRUD than on concurrent lifecycle behavior.

Optimization suggestions:

- Treat session execution as a state machine with explicit transitions and worker ownership.
- Persist worker intent separately from session status so the UI can distinguish queued, active, paused, cancelling, cancelled, failed, and completed.
- Add concurrency tests around repeated `start`, `resume`, `cancel`, `delete`, and dual SSE subscribers.

UX suggestions:

- Add clearer user-facing session states, especially `cancelling` and `queued`.
- Show where artifacts are stored and whether a run is still actively writing.
- Warn users when sources collapse onto the same derived ID before ingestion starts.

### 3. Engine

Key features that appear to function:

- the engine plus concierge grouped suite is overwhelmingly green
- core scheduling/integration behavior looks healthy
- examples and recipe distillation related tests pass

Observed issues:

- domain preference extraction misses common real-world domains like supply chain / inventory optimization
- profile import loses fidelity for common abbreviations such as `ML` / `NLP`

Other observations:

- `src/dan/engine/scheduler.py` is another very large core module
- deprecation warnings for legacy loop/control nodes are still active in the suite, which suggests migration is not fully complete across tests/examples

Optimization suggestions:

- Expand domain taxonomy and seed keyword maps.
- Preserve both canonical domain IDs and original user labels where helpful.
- Consider a small alias layer for common abbreviations before full normalization.

UX suggestions:

- If domain learning is uncertain, surface the inferred domain and let the user confirm it instead of silently storing nothing.
- In profile/settings views, show both the normalized domain label and the original phrase that triggered it.

### 4. Providers

Key features that appear to function:

- OpenAI provider tests pass, including the recent Kimi/Moonshot temperature normalization.
- Provider timeouts and compatibility logic look centralized in the right layer for OpenAI-compatible backends.

Main issue:

- the Google provider currently has three persistent test failures:
  - missing `_to_gemini_messages` compatibility/test surface
  - timeout test now fails before timeout handling because `_protos` is assumed too early

Assessment:

- This looks like refactor drift, not total provider collapse.
- Live functionality may still work, but regression protection is weaker than it should be.

Optimization suggestions:

- Keep a thin pure-data conversion helper that is independently testable without the SDK.
- Separate SDK object construction from message transformation so timeout tests can stay lightweight.

UX suggestions:

- If Google support is experimental or less exercised than OpenAI/Anthropic, expose that clearly in docs/settings.
- Provider error messages should tell users whether the failure came from auth, schema conversion, timeout, or SDK availability.

### 5. Tools

Key features that appear to function:

- almost the entire tools suite passes
- browser, desktop, file, compression, diff, JSON/regex, web, spreadsheet, and shell tools look broadly healthy

Observed issue:

- clipboard behavior is environment-sensitive and currently too optimistic on macOS/headless sessions

Important product mismatch:

- `README.md` still says built-in tools are “all sandboxed to workspace root”
- actual implementation in `src/dan/tools/_workspace.py` allows explicit absolute paths outside the workspace
- tests intentionally validate that behavior

Assessment:

- This is not just a docs nit: it changes the user’s safety model.

Optimization suggestions:

- Decide which contract is intended:
  - true workspace sandbox
  - or “relative paths are sandboxed, explicit absolute paths are trusted”
- Then align README, plan docs, tool descriptions, and tests to that same rule.

UX suggestions:

- If absolute paths are allowed, the UI should visibly warn when a tool call is about to operate outside pinned/trusted roots.
- Clipboard errors should explain “clipboard utility exists but is unavailable in this session” rather than returning an empty failure string.

### 6. Builder / Loader / Validation / Blocks / Publish / Models / Migration

Status:

- grouped suite for these subsystems passed cleanly

Assessment:

- This is one of the healthiest parts of the codebase.
- The typed graph core still looks like the project’s strongest foundation.

Optimization suggestions:

- The main remaining concern here is not broad correctness but deprecation cleanup.
- The project still emits legacy node warnings; reducing those would make the platform feel more coherent.

UX suggestions:

- Continue improving error messages around graph validation and migration so users see actionable repair guidance instead of structural diagnostics alone.

### 7. Meta / Adapters / CLI / Client / Notifications / MCP

Status:

- grouped suite passed cleanly under a writable temp home

Assessment:

- These surfaces look more reliable than the server startup path suggested at first glance.
- The adapter and CLI layers in particular seem well-exercised by tests.

Optimization suggestions:

- Keep consolidating env/config resolution so CLI, server, and local chat do not drift in behavior.

UX suggestions:

- Add one “environment diagnostics” command or panel summarizing active model, workspace root, telemetry path, profile path, Furnace state, and MCP config.
- This would reduce a lot of current ambiguity for users when the app behaves differently across local/server contexts.

### 8. Editor

Key features that appear to function:

- all 81 editor tests pass
- chat persistence/branching/mutation utilities look healthy

Main concerns:

- the editor test surface is almost entirely in `editor/src/lib/__tests__`
- there are effectively no component tests for the biggest UI components
- the largest components are very large:
  - `editor/src/components/ChatPanel.tsx` about 4,700 lines
  - `editor/src/components/modes/ResearchMode.tsx` about 3,300 lines
  - `editor/src/components/modes/CodeMode.tsx` about 944 lines

Specific UX gap:

- Code mode surfaces Workflow and Furnace activity-bar entries, but their panels are still placeholders:
  - `WorkflowSidebarPanel()` only shows static text and a generic button
  - `FurnaceSidebarPanel()` only shows static text and a generic button

Assessment:

- The editor’s data/model layer is healthier than its component composition layer.
- The app is feature-rich, but some of the UX currently feels like features are discoverable before they are truly operable.

Optimization suggestions:

- Break `ChatPanel` into explicit subtrees:
  - thread rail
  - composer
  - stream lifecycle manager
  - branch controls
  - attachment manager
  - mutation approval flow
- Add component tests for the top interaction surfaces, not just the utility layer.

UX suggestions:

- Avoid presenting placeholder sidebars as if they are first-class tools.
- Either hide unfinished panels behind a feature flag or replace them with actionable “coming next” cards that link to the real surface.
- Research mode and chat mode would benefit from stronger progressive disclosure:
  - beginner path
  - advanced path
  - debug path
- Right now the capability density is high enough to overwhelm new users.

## High-Value UX Improvements

These are the changes I think would most improve day-to-day usability:

1. Add a unified “System Status” surface.
   Show workspace roots, profile path, telemetry status, Furnace status, provider/model, MCP state, and writable locations.

2. Clarify trust zones for file operations.
   If writes outside the workspace are allowed, surface that clearly and ask for confirmation on first touch.

3. Make long-running task states explicit.
   Especially for Furnace and chat: `queued`, `active`, `paused`, `cancelling`, `failed`, `completed`, `backgrounded`.

4. Reduce feature discoverability mismatch.
   Don’t expose placeholder Workflow/Furnace panels in code mode as if they’re production-ready.

5. Add guided onboarding modes.
   The app would benefit from lightweight “basic / advanced” affordances so new users are not dropped into the full surface area immediately.

6. Surface inferred preferences and domains.
   When the system learns something durable, show it and let the user confirm or reject it.

## High-Value Optimization / Maintainability Improvements

1. Continue breaking up giant modules.
   Priority files:
   - `src/dan/server/chat_manager.py`
   - `src/dan/server/concierge/runtime.py`
   - `src/dan/server/run_manager.py`
   - `src/dan/server/routers/furnace.py`
   - `src/dan/engine/scheduler.py`
   - `editor/src/components/ChatPanel.tsx`
   - `editor/src/components/modes/ResearchMode.tsx`

2. Eliminate module-import-time config reads where possible.
   Dynamic config is easier to test and far less surprising operationally.

3. Add concurrency tests for task runners.
   Furnace especially needs tests for duplicate starts, cancel/delete races, and multi-subscriber progress.

4. Align documentation with actual safety behavior.
   The current workspace-sandbox language is too strong for the actual file-tool contract.

## Bottom Line

The project is in better shape than the first raw `pytest` output suggests. Once the home-directory write assumptions are neutralized, most subsystem families pass their tests cleanly. The codebase’s strongest areas are the typed graph core, builder/loader/validation stack, adapters/CLI/meta surfaces, and the editor’s supporting library layer.

The main areas that still need attention are:

- operational robustness at startup
- Furnace runtime correctness under concurrency and cancellation
- domain preference learning quality
- Google provider regression coverage
- user trust/clarity around file-tool safety
- editor component complexity and unfinished-yet-visible UI surfaces
