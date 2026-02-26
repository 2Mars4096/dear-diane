---
name: Vibe Research — Multi-Department Adaptive
description: Orchestrator oversees project, manages departments (max 6), strategy manager per department, script-as-param for custom strategies. Meta department (blend top K by Sharpe) runs every N iterations.
format_version: 1
tags: [vibe-research, multi-department, orchestrator, strategy-manager, run-strategy-script]
---

## Agents

- [entry_multi_dept](entry_multi_dept.md)
- [iteration_multi_dept](iteration_multi_dept.md)
- [extract_results](extract_adaptive_results.md)
- [write_csv](write_csv.md)
- [plot_one](plot_one.md)

## Flow

entry_multi_dept | loop(iteration_multi_dept, until: "not try_more", max: 5)
entry_multi_dept_loop_iteration_multi_dept.done → extract_results.input
extract_results.results → write_csv.results
extract_results | each(plot_one, parallel: 2)
