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
Structured Cell compiler + Universal Cell
            ↓
provider / tools / sandbox / memory / structured output
```

There is one active agent product. Task families such as coding, research, design, games, academic work, or market analysis are brief/blueprint shapes, not separate runtimes or GUI modes.

## Structured Universal Cell configuration

`src/dan/worker/structured_cell.py` is the canonical high-level cell authoring layer below the organism boundary. The overall configuration separates behavior, structure, and execution machinery:

```text
UniversalCellConfig
├── cells
│   └── CellSpec
│       ├── context      flexible task/scope/role/evidence/state
│       └── contract
│           ├── allowed_tools
│           ├── dos / donts / preferences
│           ├── limits
│           └── acceptance
├── executors            cell id → ExecutorRef
├── topology
│   ├── sequence edges  previous → focal → next
│   └── fork/join edges parent → parallel children → collapse
└── runtime              sampling, model view, concurrency, retries, timeout
```

`CellSpec` is position-independent and has no identity, model, topology position, status, or execution history. `CellInvocation` binds a spec to `cell_id` plus `ExecutorRef`. Relationships live only in `CellTopology`; a cell never owns mutable neighbor pointers. The two dimensions have deliberately different transfer semantics:

```text
                           parent cell
                                │
                 nested ContextView + narrowed contract
                                ▼
previous cell ──full──▶ focal cell ──full──▶ next cell
                                │
                  parallel child reports
                                ▼
                 namespaced collapse / join barrier
```

- Horizontal inheritance transfers complete committed logical context plus the prior context delta; focal-local keys win collisions. Model compilation separately applies a bounded `ContextView`, so logical inheritance does not imply an unbounded prompt.
- Vertical delegation transfers only explicitly selected nested paths plus child-local context. Unselected parent data is absent from child state.
- Child authority is monotone: allowed tools remain a subset, dos/don'ts/preferences accumulate, existing acceptance checks cannot be weakened, and limits cannot increase. Per-fork concurrency belongs to topology/runtime, not the cell.
- Every declared child settles before join evaluation. `all_success`, `all_settled`, `quorum`, and `at_least_one` policies decide whether the focal cell may advance.
- `CellReport` contains only outcome, result, context delta, records, and error. Its open record ledger tracks input sources, exact modified line spans, output files, deliverables, execution events, and future fact kinds without changing `CellSpec`.
- Acceptance can require matching records by kind, role, media type, resource pattern, metadata, and count. Missing required records turn an otherwise completed execution into failure.
- Collapse stores compact child reports under `_cell_child_reports`; complete child contexts never merge into the focal namespace.
- `compile_cell(...)` lowers `CellInvocation` through `WorkerBrief` and `ExecutionRequest` into the unchanged `WorkerCoreExecutor`. Direct brief construction remains available for advanced callers.
- This layer introduces no organism plan, ready queue, or scheduler. It defines one cell and its immediate transfer boundaries.

## Python layout

- `src/dan/worker/cell.py` — fixed Universal Cell system prompt and `build_cell`.
- `src/dan/worker/structured_cell.py` — reduced cell/contract, executor binding, record ledger, external topology, context views, compiler, and fork/join boundaries.
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

`src/dan/server/app.py` mounts only three routers:

- workspace/notes/health (`routers/misc.py`);
- session CRUD (`routers/sessions.py`);
- Agent V2 task/run/event control (`routers/chat_v2.py`).

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

- `build_cell(...)` remains the only low-level worker-construction primitive.
- `build_cell_spec(...)` creates the canonical position-independent cell; `bind_cell(...)` creates an invocation. `build_structured_cell(...)` remains a convenience that performs both steps.
- Cells own only context and contract. Identity/model selection belongs to `CellInvocation`; sequence and parent/child structure belongs to `CellTopology`; sampling and execution machinery belongs to `CellRuntimeConfig`.
- Cell-level tool authority is a closed `allowed_tools` allowlist. Platform/runtime denials remain outside the cell.
- Task behavior enters through context plus dos, don'ts, and preferences; infrastructure does not encode domain roles.
- Observed sources, effects, and artifacts enter through typed `CellRecord` values, preferably collected from runtime/tool facts rather than model claims.
- Full logical context is never confused with the bounded model projection, and vertical context transfer requires an explicit view.
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
