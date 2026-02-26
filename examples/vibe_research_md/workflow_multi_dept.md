---
name: Vibe Research — Multi-Department Adaptive
description: Orchestrator oversees project, manages departments (max 6), strategy manager per department, script-as-param for custom strategies. Meta department (blend top K by Sharpe) runs every N iterations.
format_version: 1
tags: [vibe-research, multi-department, orchestrator, strategy-manager, run-strategy-script]
---

## Agents

- [entry_multi_dept](entry_multi_dept.md)
- [orchestrator_and_departments](iteration_multi_dept.md)
- [write_csv](write_csv.md)
- [plot_one](plot_one.md)

## Flow

entry_multi_dept | loop(orchestrator_and_departments, until: "not try_more", max: 5)
entry_multi_dept_loop_orchestrator_and_departments.done → write_csv.input
write_csv | each(plot_one, parallel: 2)
