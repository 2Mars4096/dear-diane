# 19: Phase 11 — Meta-Orchestrator

**Status:** in-progress
**Goal:** Build an autonomous meta-orchestrator that receives a high-level user goal, plans and constructs (or retrieves and adapts) a workflow graph, executes it, diagnoses failures at every level of severity, and applies graduated repairs — from prompt tweaks to full workflow redesign — while allowing human intervention at any step.

## Motivation

DAN currently requires a human to design workflows. Even with the self-evolving orchestrator (9D), the system can only learn to execute a *given* graph better — it cannot design, discover, adapt, or restructure graphs autonomously. The user must know the topology, pick the nodes, wire the edges, and manually iterate when things go wrong.

The meta-orchestrator closes this gap. Given a goal like *"write a stylized paper in INFORMS format on geopolitics and global supply chains, targeting Management Science"*, it should:

1. **Search** — "Have I done something similar before?" → retrieve past paper-writing workflows and their experience
2. **Plan** — "What tools, skills, and node types do I need?" → compose a workflow graph (reuse existing, adapt, or generate from scratch)
3. **Execute** — run the workflow autonomously, allowing human override at any step
4. **Diagnose** — when something fails, classify the severity (prompt issue? wrong tool? structural bug? fundamentally wrong approach?)
5. **Repair** — apply the right-sized fix: patch the prompt (level 1), swap a tool (level 2), mutate the graph (level 3), or redesign entirely (level 4)
6. **Learn** — persist the experience so next time a similar request arrives (e.g., "write a paper on another topic in AER format"), the system starts from a better place

This is a **two-level architecture**:

```
┌─────────────────────────────────────────────────────────────┐
│  Level 2: Meta-Orchestrator (this phase)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Discover │→ │  Plan    │→ │ Execute  │→ │ Diagnose + │  │
│  │ & Recall │  │ & Build  │  │ & Monitor│  │ Repair     │──┼──→ loop back
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘  │
└─────────────────────────────────────────────────────────────┘
        ↕ reads/writes              ↕ runs                ↕ reads
┌─────────────────────────────────────────────────────────────┐
│  Level 1: DAN Execution Engine (existing)                   │
│  Engine, Scheduler, Executors, Memory, RAG, Hyperedges,     │
│  Self-Evolving (9D), Cost Tracking, Run Store               │
└─────────────────────────────────────────────────────────────┘
```

Level 1 is built. This phase builds Level 2.

## Existing Infrastructure (baseline)

| Component | Location | What exists | Relevance |
|---|---|---|---|
| Builder DSL | `builder/builder.py` | `workflow()`, `llm()`, `tool()`, `code()`, `>>` chaining, `build()` → `Graph` | **Planner output**: the meta-orchestrator generates builder code to construct workflows |
| GraphMutator | `server/graph_mutator.py` | 13 mutation types (AddNode, RemoveNode, EditNode, AddEdge, ReplaceSubgraph, ExpandPattern, etc.), `PATTERN_LIBRARY` (chain, review_loop, fan_out, rag_qa, data_analysis) | **Structural repair**: level 3 repairs produce `MutationPlan` objects |
| GraphStore | `server/graph_store.py` | CRUD for workflow JSON files, `list_graphs()`, `load_as_model()` | **Discovery**: the planner browses saved workflows |
| ToolRegistry | `executors/tool.py` | `register()`, `registered_ids()`, `register_builtin_tools()` | **Tool discovery**: planner queries available tools |
| SKILL_LIBRARY | `server/skill_library.py` | `management_science_writing`, `informs_latex_style`, etc. | **Skill discovery**: planner attaches relevant skills |
| PATTERN_LIBRARY | `server/graph_mutator.py` | `chain`, `review_loop`, `fan_out`, `rag_qa`, `data_ingest`, `data_analysis` | **Planning templates**: planner can expand patterns as building blocks |
| ErrorMemoryIndex (9D) | `engine/error_memory.py` | RAG over past errors, per-workflow | **Diagnosis input**: retrieve error patterns |
| PrincipleStore (9D) | `engine/error_memory.py` | Causal principles with condition/action/reason | **Learning input**: retrieve lessons learned |
| RuleLifecycleManager (9D) | `engine/rule_generator.py` | Generated rules with effectiveness tracking | **Repair context**: knows what fixes have been tried |
| RunStore | `server/run_store.py` | Run summaries, event logs, per-workflow | **Experience source**: run history for workflow experience |
| MemoryStore (9A) | `engine/memory_store.py` | Cross-run KV persistence (GLOBAL/WORKFLOW/SESSION) | **Experience storage**: persist workflow experience summaries |
| HyperedgeResolver | `engine/hyperedge_runtime.py` | Runtime behavior modification via hyperedge hooks | **Level 1 repair path**: prompt-level fixes via 9D |
| Export endpoints | `server/app.py` | `/api/graphs/{id}/export/python`, `/export/markdown` | **Adaptation**: export existing workflow as builder code for modification |

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [19-1](19-1-workflow-experience-memory.md) | Workflow Experience Memory | Implements deferred 17-5: index past workflows with experience summaries, cross-workflow RAG search, experience consolidation | `engine/experience.py`, `server/run_manager.py`, `server/graph_store.py` |
| [19-2](19-2-workflow-planner.md) | Workflow Planner | LLM agent that receives a goal, queries experience memory, discovers tools/skills, and produces a workflow graph (reuse/adapt/generate) | `meta/planner.py` (new), `meta/discovery.py` (new), `server/app.py` |
| [19-3](19-3-structural-repair.md) | Structural Repair Engine | Graduated repair: level 2 (parameter/tool swap), level 3 (graph mutation), level 4 (full redesign). Extends 9D's reflection with structural analysis | `meta/repair.py` (new), `server/graph_mutator.py`, `engine/error_memory.py` |
| [19-4](19-4-autonomous-execution-controller.md) | Autonomous Execution Controller | Outer meta-loop: plan → execute → observe → repair/replan → re-execute. Human override protocol at any step | `meta/controller.py` (new), `server/app.py`, `server/run_manager.py` |

## Dependencies / Sequencing

1. **19-1 (Experience Memory) can start immediately.**
   - Depends on: RunStore (13-1, done), MemoryStore (9A, done), GraphStore (Phase 2, done).
   - 9D's PrincipleStore and ErrorMemoryIndex are used for richer experience but not hard blockers.
   - Consolidates deferred scope from 9D bridge plan `17-5`; `19-1` is the execution owner.

2. **19-2 (Planner) depends on 19-1.**
   - The planner queries the experience memory to find similar past workflows.
   - Also needs: ToolRegistry (done), SKILL_LIBRARY (done), Builder DSL (done), PATTERN_LIBRARY (done).

3. **19-3 (Structural Repair) depends on 9D and 19-2.**
   - Uses 9D's reflection output (CausalPrinciple with repair classification) as input signal.
   - Can invoke 19-2's planner for level 4 (full redesign).
   - Uses GraphMutator for level 3 mutations.

4. **19-4 (Controller) depends on 19-1, 19-2, 19-3.**
   - Orchestrates all components into the outer loop.
   - Implements the human override protocol.

5. **Recommended sequence: 19-1 → 19-2 → 19-3 → 19-4** (serial, each building on the last).

## Graduated Repair Levels

The meta-orchestrator classifies failures and applies proportional fixes:

| Level | Repair Type | Actor | Mechanism | Example |
|---|---|---|---|---|
| 0 | Retry | Engine (7-1) | Retry policy with backoff | Transient API timeout |
| 1 | Prompt fix | 9D (17-3) | Self-generating hyperedge rules | "Don't use merge_asof on pre-1990 dates" |
| 2 | Parameter/tool swap | 19-3 | Runtime graph mutation (`EditNode` / whitelisted param patch) | "Use gpt-4o instead of gpt-4o-mini for this node" |
| 3 | Graph mutation | 19-3 | `MutationPlan` via `GraphMutator` | "Add a data validation node before the API call" |
| 4 | Full redesign | 19-3 + 19-2 | Scrap workflow, invoke planner with error context | "This sequential approach doesn't work; try parallel departments" |

Levels 0–1 are handled by existing infrastructure (retry policies + 9D). Levels 2–4 are new in this phase.

## Success Criteria

- Given a high-level goal and no pre-existing workflow, the meta-orchestrator produces and executes a valid workflow without human intervention.
- Given a goal similar to a past workflow, the meta-orchestrator retrieves, adapts, and executes a modified version — demonstrably faster and with fewer errors than starting from scratch.
- When a run fails, the meta-orchestrator correctly classifies the repair level and applies the right-sized fix (prompt patch, tool swap, graph mutation, or redesign).
- Human can pause, inspect, modify, and resume at any step of the plan → execute → repair loop.
- Workflow experience persists across sessions — the system gets better with use.
- All meta-orchestrator decisions are traceable via the event/audit system.

## Decisions

- **Reuse-first planning policy is canonical.** Planner action order is strict: `REUSE` existing workflow if fit is sufficient; else `ADAPT`; only then `GENERATE` from scratch.
- **Planner output is declarative first, code second.** Primary output is structured `PlanIR` (`REUSE` / `ADAPT` / `GENERATE`) validated before execution. Builder DSL code generation is secondary and only used when explicitly required.
- **Experience memory is global-scoped.** Unlike 9D's per-workflow error memory, experience summaries are indexed globally so the planner can discover relevant workflows across the entire workspace.
- **Structural repair uses `GraphMutator`, not direct JSON manipulation.** All graph modifications go through the existing mutation pipeline, which handles validation, revision tracking, and diagnostics.
- **Level 4 redesign is a new planning invocation with error context.** The planner receives "here's what we tried, here's why it failed" and generates a fundamentally different approach. This is not a patch — it's a fresh plan informed by failure.
- **Human override is pause-based, not approval-based.** The system runs autonomously by default. Humans can pause at any step (before planning, before execution, before repair, before redesign) and either modify the plan or let it proceed. This avoids blocking on approvals while preserving full control.

## Notes

- This phase bridges the gap between "tool" and "autonomous agent." DAN today is a powerful workflow execution engine that requires human design. The meta-orchestrator makes it a system that can receive high-level goals and figure out the rest.
- The paper-writing use case is the primary validation scenario: "write a paper on X in Y format" should work end-to-end, with the system remembering and improving across papers.
- The planner is itself an LLM agent — it uses DAN's own node types (LLM, tool, code) to do its work. This is intentionally recursive: DAN plans DAN workflows.
- Security consideration: auto-generated workflows execute tools and code. The planner must respect sandbox/tool authorization. Any generated code path must run in hardened isolation (out-of-process constraints), not trusted in-process `exec`.
- The `PATTERN_LIBRARY` in `GraphMutator` (chain, review_loop, fan_out, rag_qa, data_analysis, data_ingest) provides the planner with composable building blocks. Expanding this library with more patterns directly improves the planner's capabilities.
