# Deep Agent Network (DAN) — Project Overview

**Generated:** 2026-05-22 by Super DAN live execution cell  
**Sources:** README.md, docs/architecture.md, docs/development-plan.md, docs/changelog.md (top entries), docs/todo.md

---

## 1. What DAN Is

Deep Agent Network (DAN) is a **typed graph orchestration system** for the hard 5% of long-running agentic tasks. It lets users design persistent agent networks as directed graphs with typed edges, control-flow primitives, and heterogeneous models — then run them via Python, a visual editor, or (soon) markdown files.

The goal is not to make everyday chat heavier; it is to make rare, high-value tasks (multi-stage research, long-running builds, high-trust workflows) tractable, inspectable, and repeatable.

## 2. Target Audience

- Teams running tasks that need planning, decomposition, tool use, checkpoints, and recovery over hours or days.
- High-trust research and operational workflows where provenance, reviewability, and explicit control matter.
- Problems that benefit from hierarchical swarms of specialized workers rather than a single monolithic agent.

## 3. Tech Stack

- **Core engine:** Python 3.11+, Pydantic v2, OpenAI SDK
- **Provider adapter:** Multimodal image attachment helpers that normalize inputs across OpenAI, Anthropic, and Gemini.
- **LLM gateway:** Centralized dispatch (`ModelGateway`) with queued admission, in-flight caps, optional pacing, and queue telemetry.
- **Frontend:** React-based visual editor, Super DAN / Chat V2 surfaces, chunk workspace unified surface.

## 4. Architecture Highlights

- **Graph-based execution:** Nodes are functional units (LLM calls, tool calls, sub-workflows). Edges define data routing, control flow, and type contracts.
- **Heterogeneous models:** Different nodes can use different models/tiers based on task needs.
- **Control-flow primitives:** Sequential chains, parallel fan-outs, conditional branches, loops, and (planned) hyperedges.
- **State & memory:** Cross-run state, session conversation memory, context scoping boundaries, and long-chain memory systems.
- **Tooling & observability:** Gateway telemetry, run observability history, recovery debug workbench, and token analytics.

## 5. Development Themes (from plans/)

The project has a large, phased plan set covering:

1. **Orchestration engine** — core graph execution and builder API.
2. **UI/UX** — app shell, chat mode, research mode, code mode, marketplace, content mode with live Hugo preview.
3. **Chat & Control Plane** — chat panel backend, mention co-navigation, NL graph mutation, history execution, scoped runs, tool-aware conversation, auto-approve/undo.
4. **Memory & State** — session memory, context boundaries, long-chain memory, workflow experience memory.
5. **Execution Primitives** — agent teams, voting ensembles, async loops, loop context managers.
6. **Self-Evolving Orchestrator** — error-memory RAG, reflection nodes, self-generating rules, observability wiring, workflow experience summaries.
7. **Token Optimization** — prompt compression, caching, context-window management, token analytics, task-level model tiering.
8. **Meta-Orchestrator** — workflow planner, structural repair, autonomous execution controller, self-knowledge RAG, runtime authoring, system-architect mode.
9. **Distribution** — PyPI package, CLI mode, MCP/adapter thin clients, shareable blocks, gateway API.
10. **Benchmarks** — WorfBench, GAIA, AppWorld, custom longtail, agent-memory suites.

## 6. Recent Activity (Changelog Snapshot)

- **2026-05-22** — Super TUI prompt-flow enforcement tightened; Chat V2 Super DAN surface context forwarded structurally into backend args.
- **2026-05-21** — Super TUI framed Rich panels made atomic; regression tests added for read-only compile-check route drift and policy conflicts.
- Earlier work includes chunk-workspace unified surface, multi-mode chat, rich context mentions, and workflow authoring stability.

## 7. Current Backlog Snippet

- Chunk-workspace unified surface (`#workspace` route) is in progress with Work/Notes panes, Codexx-style session rail, and VS Code-style file tree.
- Cursor-parity chat features and workflow capability follow-ups are active.
- Run observability, recovery debug workbench, and memory cross-run state are upcoming.

## 8. Known Gaps / Risks

- Many planned features are in design or early implementation (see `docs/plans/` and `docs/UI-plans/`).
- The codebase surface is large; some docs may drift from implementation.
- No single canonical "getting started" quickstart exists in the root README beyond the high-level pitch.

---

*This document is a bounded snapshot. For the latest ground truth, consult the source files directly.*
