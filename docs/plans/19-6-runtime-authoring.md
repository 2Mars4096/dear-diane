# 19-6: Runtime Authoring

**Parent:** [19-meta-orchestrator](19-meta-orchestrator.md)
**Status:** in-progress
**Goal:** Enable the meta-orchestrator to dynamically create, test, register, and persist custom tools and skills at runtime — without requiring an external IDE or manual file editing.

## Problem

When the meta-orchestrator plans a workflow that needs a capability not covered by built-in tools or existing skills, it currently has no recourse. It must either:

1. Approximate with generic tools (e.g., `run_python` with inline code) — fragile, no reuse
2. Ask the user to manually create a tool file and restart — breaks autonomy

The meta-orchestrator should be able to author new tools and skills as first-class, persistent artifacts that survive restarts and are discoverable by future planning sessions.

## Approach

Add a `RuntimeAuthor` service that:
1. **Generates** tool code or skill markdown via LLM (grounded by self-knowledge RAG from 19-5)
2. **Tests** generated tools in the sandbox before registration
3. **Registers** tools in `ToolRegistry` for immediate use
4. **Persists** artifacts to disk so they survive restarts and are auto-discovered

Skills follow a simpler path: generate `.md` content, validate format, write to skills directory, make available to `SKILL_LIBRARY`.

## Primary Files

| File | Role |
|------|------|
| `src/dan/meta/authoring.py` (new) | `RuntimeAuthor` — generate, test, register, persist tools and skills |
| `src/dan/meta/planner.py` | Invoke `RuntimeAuthor` when plan requires non-existent tool/skill |
| `src/dan/meta/controller.py` | Orchestrate authoring as part of the meta-session loop |
| `src/dan/executors/tool.py` | `ToolRegistry.register()` — already supports dynamic registration |
| `src/dan/sandbox/runner.py` | `SandboxRunner` — test generated code safely |
| `tests/test_meta/test_authoring.py` (new) | Unit + integration tests |

## Tasks

- [x] 1. **Tool authoring pipeline**
  - [x] 1-1. Define `ToolSpec` model: `tool_id`, `name`, `description`, `parameters` (JSON Schema), `code` (Python source), `dependencies` (pip packages), `test_cases` (input/expected pairs)
  - [x] 1-2. `RuntimeAuthor.generate_tool(intent: str, context: DiscoveryResult) -> ToolSpec` — LLM generates a tool spec from a natural language description, grounded in existing tool patterns and self-knowledge
  - [x] 1-3. `RuntimeAuthor.test_tool(spec: ToolSpec) -> TestResult` — run each `test_case` through `SandboxRunner`, collect pass/fail/error
  - [x] 1-4. `RuntimeAuthor.register_tool(spec: ToolSpec)` — register in `ToolRegistry` as a callable, available immediately for current and future runs
  - [x] 1-5. `RuntimeAuthor.persist_tool(spec: ToolSpec, target_dir: Path)` — write Python file to `custom_tools/` (or configurable directory), following existing tool file conventions: module-level `TOOL_METADATA` dict (keys: `tool_id`, `description`, `parameters`, `examples`, `category`, `returns`) + plain async function, matching `dan.tools.*` pattern
  - [x] 1-6. Auto-retry on test failure: if tests fail, feed error back to LLM for a second attempt (max 3 retries)

- [x] 2. **Skill authoring pipeline**
  - [x] 2-1. Define `SkillSpec` model: `skill_id`, `name`, `description`, `content` (markdown body), `target_nodes` (which node types this applies to), `hook_points` (pre_prompt | post_output | validation)
  - [x] 2-2. `RuntimeAuthor.generate_skill(intent: str, context: DiscoveryResult) -> SkillSpec` — LLM generates skill markdown following the hyperedge `.md` format (15-2)
  - [x] 2-3. `RuntimeAuthor.validate_skill(spec: SkillSpec) -> ValidationResult` — check frontmatter structure, hook points, target compatibility
  - [x] 2-4. `RuntimeAuthor.persist_skill(spec: SkillSpec, target_dir: Path)` — write `.md` file to skills directory
  - [x] 2-5. `RuntimeAuthor.activate_skill(spec: SkillSpec)` — add to `SKILL_LIBRARY` for immediate discoverability

- [ ] 3. **Planner integration**
  - [ ] 3-1. During planning, if the planner identifies a needed tool that doesn't exist in `DiscoveryResult.tools`, emit a `TOOL_NEEDED` signal with description
  - [x] 3-2. `MetaController` intercepts `TOOL_NEEDED`, invokes `RuntimeAuthor.generate_tool()`, tests, registers, then re-invokes planner with updated discovery
  - [x] 3-3. Same flow for `SKILL_NEEDED` — generate, validate, persist, activate, re-plan
  - [ ] 3-4. If human override is enabled, present generated tool/skill for approval before registration (via `HumanNode` mechanism)

- [x] 4. **Auto-discovery of persisted artifacts**
  - [x] 4-1. On server startup, scan `custom_tools/` directory using `get_all_tools()`-style discovery (import module, read `TOOL_METADATA`, extract async function) and register each in `ToolRegistry`. Add `ToolRegistry.register_custom_tools(custom_dir: Path)` method. *(Implemented as `RuntimeAuthor.discover_custom_tools()` + `register_custom_tools()` static methods)*
  - [x] 4-2. On server startup, scan `custom_skills/` directory for `.md` files and insert each into `SKILL_LIBRARY` dict (the module-level dict in `server/skill_library.py`). Skills become discoverable via `DiscoveryService.discover_skills()`. *(Implemented as `RuntimeAuthor.discover_custom_skills()`)*
  - [x] 4-3. `DiscoveryService.discover_all()` includes custom tools/skills alongside built-in ones (already works once registered — no change needed if 4-1 and 4-2 run before discovery)
  - [ ] 4-4. File watcher (optional): detect new tools/skills added externally and auto-register

- [x] 5. **Safety and isolation**
  - [x] 5-1. All generated tool code runs through `SandboxRunner` — no in-process `exec`
  - [x] 5-2. Generated tools are sandboxed at registration time: the callable registered with `ToolRegistry.register(tool_id, fn)` is itself a wrapper that invokes `SandboxRunner` with the generated code — `ToolRegistry` stores it like any other tool function
  - [x] 5-3. Resource limits (memory, CPU, timeout) applied per generated tool execution
  - [ ] 5-4. Optional human approval gate before any generated tool is registered (configurable in `MetaControllerConfig`)

- [ ] 6. **Tests**
  - [ ] 6-1. Unit: `generate_tool()` produces valid `ToolSpec` with test cases
  - [ ] 6-2. Unit: `test_tool()` catches failing code and returns structured errors
  - [ ] 6-3. Unit: `persist_tool()` writes valid Python file that can be imported
  - [ ] 6-4. Unit: `generate_skill()` produces valid hyperedge markdown
  - [ ] 6-5. Integration: end-to-end tool authoring — generate → test → register → invoke in a workflow
  - [ ] 6-6. Integration: planner requests non-existent tool → `RuntimeAuthor` creates it → planner succeeds on retry
  - [ ] 6-7. Safety: generated code cannot escape sandbox (file system, network, resource limits)

- [ ] 7. **Documentation**
  - [x] 7-1. Update `architecture.md` — document `authoring.py`, `custom_tools/`, `custom_skills/` directories
  - [ ] 7-2. Update `llm-api-guide.md` — document tool/skill authoring capabilities for LLM callers

## Decisions

- (to be filled during execution)

## Notes

- This builds on 19-5 (Self-Knowledge RAG): the `RuntimeAuthor` uses self-knowledge to generate tools that follow DAN's conventions.
- The `SandboxRunner` (Phase 6) already provides process isolation with timeout, memory limits, and environment filtering. Generated tools reuse this infrastructure.
- `ToolRegistry.register(tool_id, fn)` already supports dynamic registration at runtime — it stores an async callable. The persistence layer is new: writing to disk as a `TOOL_METADATA` + async function module (matching `dan.tools.*` convention) and auto-discovery on restart via `register_custom_tools()`.
- `SKILL_LIBRARY` is a module-level dict in `server/skill_library.py`. Runtime insertion (`SKILL_LIBRARY[key] = {...}`) makes skills discoverable via `DiscoveryService.discover_skills()`. For actual runtime behavior injection, the corresponding `Hyperedge` object should be created and registered with `HyperedgeResolver`.
- Human approval is optional but recommended for production. The meta-controller's `HumanOverride` mechanism (19-4) provides the approval gate.
