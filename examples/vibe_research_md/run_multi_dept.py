#!/usr/bin/env python3
"""Run the multi-department adaptive workflow.

Usage:
    python examples/vibe_research_md/run_multi_dept.py
    python examples/vibe_research_md/run_multi_dept.py --start 2012 --end 2022
    python examples/vibe_research_md/run_multi_dept.py --max-factors 10
    python examples/vibe_research_md/run_multi_dept.py --build-only
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from dan.loader import compile_workflow
from dan.engine import Engine, EngineConfig
from dan.engine.executor import ExecutorRegistry
from dan.executors.tool import ToolExecutor, ToolRegistry


def _build_tool_registry() -> ToolRegistry:
    """Build registry with run_backtest, run_strategy_script, plot_backtest, save_grid_csv, department state."""
    import sys

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from dan.server.app import (
        _run_backtest,
        _run_strategy_script,
        _plot_backtest,
        _save_grid_csv,
        _get_department_state,
        _update_department_state,
    )

    reg = ToolRegistry()
    reg.register_builtin_tools()
    reg.register("run_backtest", _run_backtest)
    reg.register("run_strategy_script", _run_strategy_script)
    reg.register("plot_backtest", _plot_backtest)
    reg.register("save_grid_csv", _save_grid_csv)
    reg.register("get_department_state", _get_department_state)
    reg.register("update_department_state", _update_department_state)
    return reg


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-department adaptive workflow")
    parser.add_argument("--start", type=int, default=2010)
    parser.add_argument("--end", type=int, default=2023)
    parser.add_argument("--max-factors", type=int, default=20, help="Hard limit: stop when this many factors generated")
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()

    workflow_dir = Path(__file__).parent
    result = compile_workflow(workflow_dir / "workflow_multi_dept.md")

    if not result.graph:
        for d in result.diagnostics:
            print(f"{d.severity}: {d.message}")
        raise SystemExit(1)

    if args.build_only:
        graphs_dir = Path("graphs")
        graphs_dir.mkdir(exist_ok=True)
        path = graphs_dir / "vibe_research_multi_dept.json"
        path.write_text(result.graph.model_dump_json(indent=2), encoding="utf-8")
        print(f"Saved {path}")
        return

    config = EngineConfig(
        llm_api_key=os.environ.get("DAN_LLM_API_KEY", ""),
        llm_base_url=os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
        llm_default_model=os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
    )
    tool_registry = _build_tool_registry()
    exec_registry = ExecutorRegistry()
    exec_registry.register("tool_operator", ToolExecutor(tool_registry))
    engine = Engine(config, executor_registry=exec_registry)

    async def run() -> None:
        run_result = await engine.run(
            result.graph,
            inputs={"start_year": args.start, "end_year": args.end, "max_factors": args.max_factors},
        )
        print("Success:", run_result.success)
        if run_result.outputs:
            for k, v in run_result.outputs.items():
                print(f"  {k}:", v)
        if run_result.errors:
            print("Errors:", run_result.errors)

    asyncio.run(run())


if __name__ == "__main__":
    main()
