"""Durability smoke checks for generated workflows (plan 33-4).

D1: Repeat-run stability
D2: Reload + validate stability
D3: Export / import stability
D4: Mutation-after-build stability
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from tests.eval import EvalRecord, ValidationResult
from tests.eval.client import DanClient

_RUN_POLL_INTERVAL = 2.0
_RUN_TIMEOUT = 120.0
_TERMINAL_STATES = frozenset({"completed", "failed", "error", "cancelled"})


async def check_repeat_run(
    client: DanClient, graph_id: str, runs: int = 3
) -> dict:
    """D1: Run the same workflow *runs* times and compare outcomes."""
    result: dict = {
        "runs_attempted": 0,
        "runs_completed": 0,
        "durations": [],
        "token_counts": [],
        "terminal_states": [],
        "stable": False,
    }

    for _ in range(runs):
        result["runs_attempted"] += 1
        try:
            resp = await client.start_run(graph_id)
            run_id = resp.get("run_id") or resp.get("id")
            if not run_id:
                result.setdefault("errors", []).append("no run_id returned")
                continue

            t0 = time.monotonic()
            state = ""
            run_info: dict = {}
            while (time.monotonic() - t0) < _RUN_TIMEOUT:
                run_info = await client.get_run(run_id)
                state = run_info.get("status") or run_info.get("state", "")
                if state in _TERMINAL_STATES:
                    break
                await asyncio.sleep(_RUN_POLL_INTERVAL)

            duration_ms = (time.monotonic() - t0) * 1000
            result["durations"].append(duration_ms)
            result["terminal_states"].append(state or "unknown")
            result["token_counts"].append(run_info.get("total_tokens", 0))
            if state == "completed":
                result["runs_completed"] += 1
        except Exception as exc:
            result.setdefault("errors", []).append(str(exc))

    result["stable"] = (
        result["runs_attempted"] > 0
        and result["runs_completed"] == result["runs_attempted"]
    )
    result["state_counts"] = dict(Counter(result["terminal_states"]))
    return result


async def check_reload_validate(client: DanClient, graph_id: str) -> dict:
    """D2: Fetch graph twice, then re-validate."""
    result: dict = {"reload_ok": False, "validate_ok": False, "details": ""}
    try:
        g1 = await client.get_graph(graph_id)
        if g1 is None:
            result["details"] = "graph not found on first fetch"
            return result

        g2 = await client.get_graph(graph_id)
        if g2 is None:
            result["details"] = "graph not found on second fetch"
            return result

        result["reload_ok"] = True
        validation = await client.validate_graph(graph_id)
        result["validate_ok"] = len(validation.get("errors", [])) == 0
        result["details"] = validation
    except Exception as exc:
        result["details"] = str(exc)
    return result


async def check_export_import(client: DanClient, graph_id: str) -> dict:
    """D3: Export to Python and Markdown, verify non-empty output."""
    result: dict = {
        "python_ok": False,
        "markdown_ok": False,
        "python_len": 0,
        "markdown_len": 0,
    }

    try:
        py_code = await client.export_python(graph_id)
        result["python_len"] = len(py_code)
        result["python_ok"] = len(py_code) > 0
    except Exception as exc:
        result["python_error"] = str(exc)

    try:
        md = await client.export_markdown(graph_id)
        result["markdown_len"] = len(md)
        result["markdown_ok"] = len(md) > 0
    except Exception as exc:
        result["markdown_error"] = str(exc)

    return result


async def check_mutation_stability(
    client: DanClient, graph_id: str, follow_up: str
) -> dict:
    """D4: Apply a follow-up mutation and re-validate the graph."""
    result: dict = {
        "mutation_applied": False,
        "still_valid": False,
        "node_count_before": 0,
        "node_count_after": 0,
    }

    try:
        g_before = await client.get_graph(graph_id)
        if g_before is None:
            result["error"] = "graph not found"
            return result

        nodes_before = g_before.get("data", {}).get("nodes", [])
        result["node_count_before"] = len(nodes_before)

        if not follow_up:
            result["error"] = "no follow_up prompt provided"
            return result

        await client.send_message(graph_id, follow_up, mode="build")
        await asyncio.sleep(3.0)

        g_after = await client.get_graph(graph_id)
        if g_after is None:
            result["error"] = "graph not found after mutation"
            return result

        nodes_after = g_after.get("data", {}).get("nodes", [])
        result["node_count_after"] = len(nodes_after)
        result["mutation_applied"] = len(nodes_after) != len(nodes_before)

        validation = await client.validate_graph(graph_id)
        result["still_valid"] = len(validation.get("errors", [])) == 0
    except Exception as exc:
        result["error"] = str(exc)
    return result


async def run_durability_suite(
    client: DanClient, graph_id: str, follow_up: str = ""
) -> dict:
    """Run all four durability checks on a graph and return combined results."""
    return {
        "repeat_run": await check_repeat_run(client, graph_id),
        "reload_validate": await check_reload_validate(client, graph_id),
        "export_import": await check_export_import(client, graph_id),
        "mutation_stability": await check_mutation_stability(
            client, graph_id, follow_up
        ),
    }
