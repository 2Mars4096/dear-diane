# 21: Phase 12 — Author & Distribute

**Status:** completed
**Goal:** Make DAN usable beyond the visual editor: CLI for headless execution, publish workflows as callable MCP/API services, messaging adapters (email/Telegram/WhatsApp) as interactive surfaces, shareable agent blocks, and a clean PyPI package for `pip install`.

## Motivation

DAN is a powerful workflow authoring and execution platform — but today it's only accessible through the visual editor (browser) or programmatic Python imports. Real adoption requires:

- **Headless execution** — CI pipelines, cron jobs, server deployments, background agents. No browser needed.
- **Consumable services** — a workflow should be publishable as an MCP server or HTTP endpoint that others can call without understanding DAN internals. "Build once, serve everywhere."
- **Multi-channel interaction** — HumanNode shouldn't be limited to the browser. Email, Telegram, and WhatsApp are where many users already live. An adapter should render HumanNode I/O through these channels interactively.
- **Shareability** — composite nodes, workflows, and agent files should be packageable, versioned, and importable by others.
- **Distribution** — `pip install` with a stable, documented public API.

This phase comes after the meta-orchestrator (Phase 11) is stable, so the CLI can leverage NL→workflow planning, and published APIs benefit from autonomous execution and self-repair.

## Existing Infrastructure

| Component | Location | Relevance |
|---|---|---|
| `pyproject.toml` | root | Already defines `dan` package, hatchling build, optional deps, `dan-serve` entry point |
| `dan.engine` | `engine/scheduler.py` | Full async execution — CLI and publish both call `Engine.run()` |
| `dan.builder` | `builder/` | Programmatic graph construction — used to reconstruct graphs from Python file sources |
| `dan.loader` | `loader/` | Markdown→graph compilation — CLI loads `.md` workflows |
| `dan.meta` | `meta/controller.py` | Meta-orchestrator — CLI's NL→workflow path (`__init__.py` is currently empty — needs public API exports) |
| `dan.server` | `server/app.py` | FastAPI backend — publish-as-API extends this; messaging adapters register as HumanNode renderers |
| `HumanRenderer` protocol | `engine/executor.py` | Formal protocol for HumanNode rendering — `render(HumanRenderRequest) -> HumanRenderResponse`. Server uses `LegacyCallbackRenderer`; CLI and messaging adapters implement this directly. |
| `workflow-as-node` | `lib/graphImporter.ts` | Composite import — shareable blocks extend this with packaging/versioning |
| `dan-serve` entry point | `server/__main__.py` | Existing CLI for editor server — new CLI commands coexist |

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [21-1](21-1-pypi-package.md) | PyPI Package | Public API surface, package structure, split packages, version 0.1.1, build pipeline | ~1 day | None (foundational) |
| [21-2](21-2-cli-mode.md) | CLI Mode | `dan-run`, Rich TUI, `--interactive`, meta-orchestrator NL path, background mode, `dan-status`/`dan-logs` | ~2–3 days | 21-1 (entry points) |
| [21-3](21-3-publish-api-mcp.md) | Publish as API/MCP | MCP server generation, HTTP REST fallback, stateful WebSocket, easy portal, schema derivation | ~2–3 days | 21-2 (headless execution pattern) |
| [21-4](21-4-messaging-adapters.md) | Messaging Adapters | Adapter protocol, email (IMAP/SMTP), Telegram (Bot API), WhatsApp (Business API), interactive HumanNode rendering | ~3–4 days | 21-1 (package structure) |
| [21-5](21-5-shareable-blocks.md) | Shareable Blocks | Package format, export/import, versioning, local registry, discovery | ~1–2 days | 21-1 (package structure) |

## Dependencies / Sequencing

```
21-1 (PyPI Package)     ← foundational, start here
  ├→ 21-2 (CLI Mode)    ← depends on entry points + public API from 21-1
  │    └→ 21-3 (Publish as API/MCP)  ← builds on headless execution from 21-2
  │         └→ (shared) derive_workflow_interface() in dan.utils.workflow_interface
  ├→ 21-4 (Messaging Adapters)       ← independent track, needs package structure from 21-1
  └→ 21-5 (Shareable Blocks)         ← independent track, needs package structure from 21-1
       └→ (shared) derive_workflow_interface() — extracted to dan.utils so 21-3 and 21-5 share it
```

**Parallelizable:** After 21-1 completes, 21-2/21-4/21-5 can run in parallel. 21-3 waits for 21-2. `derive_workflow_interface()` is shared between 21-3 and 21-5 — whichever starts first creates `dan.utils.workflow_interface`, the other reuses it.

## Key Decisions

- **MCP-first for publish.** When a workflow is publishable as a callable service, MCP is the primary target (tool interop with Cursor, Claude Desktop, etc.). HTTP REST is the fallback for non-MCP consumers.
- **Stateful publishing.** Published services support WebSocket/SSE streaming and pause/resume for HumanNode workflows. Fire-and-forget (stateless HTTP) is also supported as a simpler mode.
- **Messaging = HumanNode renderers.** Email/Telegram/WhatsApp adapters implement the same HumanNode rendering protocol as the CLI's interactive mode and the browser's `HumanInputDialog`. The workflow doesn't know or care which surface is rendering.
- **Portal UX for publish.** `dan-publish` should produce something others can use with near-zero setup — not a Docker deployment guide. Think: "run this one command to expose your workflow as an MCP tool."
- **Package name is placeholder.** `dan` is used throughout but may change before PyPI publication. Renaming requires pyproject.toml + directory rename + import updates (documented in 21-1).
- **Version 0.1.1.** Pre-release signal. The API surface is real but not yet frozen.
- **Both monolithic and split installs.** `pip install dan[all]` gets everything. `pip install dan-core` gets engine+builder+loader only. Split packages are optional extras, not mandatory.
- **Rich TUI for CLI.** Progress display uses `rich` library — live tables, spinners, streaming panels. Not a full TUI app (no `textual`), but rich enough for comfortable terminal use.
- **Email + Telegram + WhatsApp.** These three messaging adapters are in scope. Slack/Discord deferred to backlog. Adapters work interactively — the messaging user is the "human" in the workflow, receiving prompts and sending responses.

## Success Criteria

- `pip install dan` works. `dan-run examples/simple_chain.py --input idea="test"` executes a workflow in terminal with Rich progress display.
- `dan-run "write a literature review on supply chain resilience"` triggers the meta-orchestrator, plans a workflow, and executes it end-to-end.
- `dan-run workflow.json --interactive` pauses at HumanNode and prompts via stdin; `--headless` auto-skips or uses defaults.
- `dan-publish workflow.json --type mcp` produces a working MCP server that Cursor/Claude Desktop can connect to and invoke.
- A Telegram bot backed by a DAN workflow can receive a message, execute the workflow with the message as input, and reply with the result — including multi-turn interaction for HumanNode prompts.
- A composite agent block can be exported, versioned, and imported into another workspace as a reusable node.
- All existing tests (currently 771 passed) continue to pass. New features have their own test suites.

## Notes

- The CLI and publish features establish DAN as a headless-first platform that happens to have a visual editor, rather than a visual editor that happens to have an engine. This is the distribution inflection point.
- Messaging adapters validate the "HumanNode is a protocol, not a UI component" design principle from architecture.md.
- PyPI packaging forces a cleanup pass on imports, public API surface, and dependency management — beneficial regardless of distribution.
