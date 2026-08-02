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
