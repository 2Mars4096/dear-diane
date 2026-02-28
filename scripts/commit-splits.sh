#!/usr/bin/env bash
# Logical commit splits for current working tree.
# Run from repo root. Each block is one commit — uncomment and run sequentially,
# or run the whole script (it will stop on first error).

set -e
cd "$(git rev-parse --show-toplevel)"

# -----------------------------------------------------------------------------
# Commit 1: Plan 7-6 backend (already staged)
# -----------------------------------------------------------------------------
git commit -m "feat: Plan 7-6 node state simplification (loop-scoped state, code port defaults, spread edges)

- GateNode/WhileLoopNode: state_schema, state_defaults, scope injection in _execute_node
- Code node port defaults from json_schema type
- DataEdge.spread=True destructures dicts into individual inputs
- flow_parser: loop() state/defaults kwargs, GraphMutator + validation for spread
- builder, compiler, decompiler, loader, executors, validation, chat_manager, graph_mutator"

# -----------------------------------------------------------------------------
# Commit 2: Plan 7-6 & 7-7 docs
# -----------------------------------------------------------------------------
git add docs/plans/7-6-node-state-simplification.md \
        docs/plans/7-7-editor-navigation-layout-hardening.md \
        docs/plans/7-core-hardening.md \
        docs/todo.md
git commit -m "docs: Add plans 7-6 and 7-7, update 7-core-hardening and todo"

# -----------------------------------------------------------------------------
# Commit 3: Plan 7-7 editor navigation & layout hardening
# -----------------------------------------------------------------------------
git add editor/src/components/AnimatedEdge.tsx \
        editor/src/components/BreadcrumbBar.tsx \
        editor/src/components/DanNode.tsx \
        editor/src/components/PortMappingOverlay.tsx \
        editor/src/lib/graphAdapter.ts \
        editor/src/lib/layout.ts \
        editor/src/lib/portOrdering.ts \
        editor/src/store/useGraphStore.ts \
        editor/src/types/graph.ts
git commit -m "feat: Plan 7-7 editor navigation & layout hardening

- resolveGraphAtStack + deepSetSubGraph for nested sub-graph traversal
- Fix drillIn/drillOut/jumpToLayer to traverse full layer stack
- Fix saveGraph with deepSetSubGraph for nested immutable updates
- Fix PortMappingOverlay nested parent resolution
- Deterministic port ordering (portOrdering.ts), port-aware dagre, crossing minimization
- Per-port smoothstep offset in AnimatedEdge, data-edge label dedup
- Depth-3 cap in drillIn and BreadcrumbBar"

# -----------------------------------------------------------------------------
# Commit 4: Vibe research — remove legacy workflow files
# -----------------------------------------------------------------------------
git add examples/vibe_research_md/WORKFLOW.md \
        examples/vibe_research_md/department_run.md \
        examples/vibe_research_md/entry_multi_dept.md \
        examples/vibe_research_md/expand_departments.md \
        examples/vibe_research_md/iteration_multi_dept.md \
        examples/vibe_research_md/merge_dept_results.md \
        examples/vibe_research_md/output_per_dept.md \
        examples/vibe_research_md/persist_dept_state.md \
        examples/vibe_research_md/run_python.md \
        examples/vibe_research_md/strategy_manager_dept.md \
        examples/vibe_research_md/unpack_item.md \
        examples/vibe_research_md/unpack_multi_dept_input.md
git commit -m "refactor: Vibe research — remove legacy workflow files (superseded by state_schema loop)"

# -----------------------------------------------------------------------------
# Commit 5: Vibe research — add new workflow nodes
# -----------------------------------------------------------------------------
git add examples/vibe_research_md/department.md \
        examples/vibe_research_md/dept_gate.md \
        examples/vibe_research_md/dept_halt.md \
        examples/vibe_research_md/entry.md \
        examples/vibe_research_md/iteration.md \
        examples/vibe_research_md/merge.md \
        examples/vibe_research_md/pass_assignment_after_prev.md \
        examples/vibe_research_md/strategy_manager.md
git commit -m "feat: Vibe research — add new workflow nodes (department, dept_gate, dept_halt, entry, iteration, merge, pass_assignment_after_prev, strategy_manager)"

# -----------------------------------------------------------------------------
# Commit 6: Vibe research — quant_lib updates
# -----------------------------------------------------------------------------
git add examples/vibe_research_md/quant_lib/backtest.py \
        examples/vibe_research_md/quant_lib/factor_momentum.py \
        examples/vibe_research_md/quant_lib/load_compustat.py \
        examples/vibe_research_md/quant_lib/load_crsp.py \
        examples/vibe_research_md/quant_lib/run_backtest.py
git commit -m "fix: Vibe research quant_lib — factor sign convention, CRSP price filter, Compustat aliases, builtin reversal support"

# -----------------------------------------------------------------------------
# Commit 7: Vibe research — workflow spec updates
# -----------------------------------------------------------------------------
git add examples/vibe_research_md/README.md \
        examples/vibe_research_md/backtest_runner.md \
        examples/vibe_research_md/bug_fixer.md \
        examples/vibe_research_md/governor.md \
        examples/vibe_research_md/orchestrator.md \
        examples/vibe_research_md/plot_one.md \
        examples/vibe_research_md/run_multi_dept.py \
        examples/vibe_research_md/run_strategy_or_backtest.md \
        examples/vibe_research_md/save_tracking.md \
        examples/vibe_research_md/strategy_coder.md \
        examples/vibe_research_md/strategy_to_item.md \
        examples/vibe_research_md/workflow_multi_dept.md \
        examples/vibe_research_md/workflow_multi_dept_builder.py \
        examples/vibe_research_md/write_csv.md
git commit -m "fix: Vibe research workflow — I/O alignment, merge contract, governor/merge/save_tracking, strategy naming, plotting"

# -----------------------------------------------------------------------------
# Commit 8: Vibe research — compiled graph
# -----------------------------------------------------------------------------
git add graphs/vibe_research_multi_dept.json
git commit -m "chore: Regenerate vibe_research_multi_dept.json from workflow spec"

# -----------------------------------------------------------------------------
# Commit 9: Docs (architecture, bugs, changelog, llm-api-guide)
# -----------------------------------------------------------------------------
git add docs/architecture.md docs/bugs.md docs/changelog.md docs/llm-api-guide.md
git commit -m "docs: Update architecture, bugs, changelog, llm-api-guide"

# -----------------------------------------------------------------------------
# Commit 10: Server app.py
# -----------------------------------------------------------------------------
git add src/dan/server/app.py
git commit -m "feat: Server app.py updates (validation gate, idempotency, metrics, feature flags)"

# -----------------------------------------------------------------------------
# Optional: Test graphs (g1, g2, g_metrics) — uncomment if you want to commit
# -----------------------------------------------------------------------------
# git add graphs/g1.json graphs/g2.json graphs/g_metrics.json
# git commit -m "chore: Add test graph fixtures"

echo "Done. Remaining untracked: graphs/g1.json, graphs/g2.json, graphs/g_metrics.json (optional)"
