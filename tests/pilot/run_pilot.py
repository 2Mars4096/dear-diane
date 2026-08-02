#!/usr/bin/env python3
"""Manual pilot: send workflow-building prompts to live DAN server, log results.

Usage:
    python tests/pilot/run_pilot.py                  # run all prompts
    python tests/pilot/run_pilot.py --index 0        # run single prompt by index
    python tests/pilot/run_pilot.py --report FILE    # print report from existing JSONL
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]

BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000"
TIMEOUT_SECONDS = 120

PILOT_PROMPTS = [
    {
        "id": "t1-01",
        "tier": "T1",
        "prompt": "Build a simple 3-step chain: research a topic, analyze findings, write a summary",
        "expected": {"min_nodes": 3, "pattern": "chain"},
    },
    {
        "id": "t1-02",
        "tier": "T1",
        "prompt": "Create a review loop workflow where a writer drafts content and a reviewer gives feedback until approved",
        "expected": {"min_nodes": 3, "pattern": "review_loop", "has_loop": True},
    },
    {
        "id": "t2-01",
        "tier": "T2",
        "prompt": "Build a workflow that searches the web for recent news on a topic, reads the top 3 results, and produces a briefing",
        "expected": {"min_nodes": 3, "pattern": "chain_with_tools"},
    },
    {
        "id": "t2-02",
        "tier": "T2",
        "prompt": "Create a RAG pipeline: ingest PDF documents from a folder, index them, then answer questions using the indexed knowledge",
        "expected": {"min_nodes": 2, "pattern": "rag_qa"},
    },
    {
        "id": "t3-01",
        "tier": "T3",
        "prompt": (
            "Build a workflow that takes a research question, searches for papers, "
            "reads each paper in parallel, then synthesizes findings into a literature "
            "review with a review loop"
        ),
        "expected": {"min_nodes": 5, "has_loop": True, "has_fan_out": True},
    },
    {
        "id": "t3-02",
        "tier": "T3",
        "prompt": (
            "Create a data analysis workflow: read a CSV file, run Python code to "
            "compute statistics, generate a chart, and write a report summarizing the findings"
        ),
        "expected": {"min_nodes": 4, "has_code": True},
    },
    {
        "id": "t4-01",
        "tier": "T4",
        "prompt": (
            "Build a multi-department research system: create 3 parallel research teams "
            "— one for market analysis, one for technical assessment, one for competitive "
            "intelligence. Each team should have its own research-review loop. An orchestrator "
            "collects all findings and produces a unified strategic report."
        ),
        "expected": {"min_nodes": 10, "has_fan_out": True, "has_loop": True},
    },
    {
        "id": "t4-02",
        "tier": "T4",
        "prompt": (
            "Create an end-to-end academic paper writing workflow: start with a literature "
            "search using web search, organize papers by theme, generate an outline, write "
            "each section in parallel (intro, methods, results, discussion), assemble into "
            "a draft, run through a review panel with up to 3 revision cycles, compile to LaTeX PDF"
        ),
        "expected": {"min_nodes": 8, "has_fan_out": True, "has_loop": True},
    },
    {
        "id": "t5-01",
        "tier": "T5",
        "prompt": "Make something cool",
        "expected": {"should_build_workflow": False, "should_clarify": True},
    },
    {
        "id": "t5-02",
        "tier": "T5",
        "prompt": "What's the weather in New York?",
        "expected": {"should_build_workflow": False},
    },
]


async def list_graphs(client: httpx.AsyncClient) -> set[str]:
    resp = await client.get(f"{BASE_URL}/api/graphs")
    if resp.status_code != 200:
        return set()
    data = resp.json()
    return {g["graph_id"] for g in data.get("graphs", [])}


async def get_graph(client: httpx.AsyncClient, graph_id: str) -> dict | None:
    resp = await client.get(f"{BASE_URL}/api/graphs/{graph_id}")
    if resp.status_code != 200:
        return None
    return resp.json()


def count_nodes(graph_data: dict) -> int:
    nodes = graph_data.get("data", graph_data).get("nodes", [])
    return len(nodes)


def extract_node_types(graph_data: dict) -> list[str]:
    nodes = graph_data.get("data", graph_data).get("nodes", [])
    return [n.get("type", "unknown") for n in nodes]


def check_topology(graph_data: dict) -> dict:
    inner = graph_data.get("data", graph_data)
    nodes = inner.get("nodes", [])
    edges = inner.get("edges", [])
    node_types = [n.get("type", "") for n in nodes]

    has_loop = any(t in ("while_loop", "while_gate", "gate") for t in node_types)
    has_fan_out = any(t in ("for_each", "parallel_subagents") for t in node_types)
    has_code = any(t == "code" for t in node_types)
    has_tools = any(t == "tool" for t in node_types)
    sub_graphs = inner.get("sub_graphs", {})

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "node_types": node_types,
        "has_loop": has_loop,
        "has_fan_out": has_fan_out,
        "has_code": has_code,
        "has_tools": has_tools,
        "sub_graph_count": len(sub_graphs),
    }


def is_progress_ack(event: dict) -> bool:
    return (
        event.get("type") == "chat_complete"
        and event.get("detected_mode") == "progress_ack"
    )


async def run_prompt(
    client: httpx.AsyncClient,
    prompt_def: dict,
    workflow_id: str,
) -> dict:
    """Send a single prompt and collect results."""
    prompt = prompt_def["prompt"]
    result: dict = {
        "id": prompt_def["id"],
        "tier": prompt_def["tier"],
        "prompt": prompt,
        "expected": prompt_def.get("expected", {}),
        "workflow_id": workflow_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    graphs_before = await list_graphs(client)

    t_start = time.monotonic()

    try:
        resp = await client.post(
            f"{BASE_URL}/api/chat/message",
            json={
                "workflow_id": workflow_id,
                "message": prompt,
                "history": [],
                "mode": "agent",
                "surface": "cli",
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            result["status"] = "api_error"
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:500]}"
            result["total_ms"] = int((time.monotonic() - t_start) * 1000)
            return result

        resp_data = resp.json()
        channel_id = resp_data.get("stream_channel_id")
        if not channel_id:
            result["status"] = "no_channel"
            result["error"] = "No stream_channel_id returned"
            result["total_ms"] = int((time.monotonic() - t_start) * 1000)
            return result
    except Exception as e:
        result["status"] = "api_error"
        result["error"] = str(e)
        result["total_ms"] = int((time.monotonic() - t_start) * 1000)
        return result

    t_first_token = None
    full_response = ""
    events_collected: list[dict] = []
    mutation_applied = False
    error_text = ""

    if websockets is None:
        result["status"] = "missing_websockets"
        result["error"] = "websockets package not installed"
        result["total_ms"] = int((time.monotonic() - t_start) * 1000)
        return result

    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            async with websockets.connect(
                f"{WS_URL}/api/chat/{channel_id}/events"
            ) as ws:
                async for msg in ws:
                    event = json.loads(msg)
                    if event is None:
                        break

                    ev_type = event.get("type", "")
                    events_collected.append(
                        {"type": ev_type, "t_ms": int((time.monotonic() - t_start) * 1000)}
                    )

                    if ev_type == "chat_token":
                        if t_first_token is None:
                            t_first_token = time.monotonic()
                        full_response += event.get("delta", "")

                    elif ev_type == "chat_complete":
                        if is_progress_ack(event):
                            continue
                        content = event.get("content", "")
                        if content and not full_response:
                            full_response = content
                        elif content:
                            full_response = content
                        break

                    elif ev_type == "chat_error":
                        error_text = event.get("error", "Unknown")
                        break

                    elif ev_type == "graph_updated":
                        mutation_applied = True

    except asyncio.TimeoutError:
        result["status"] = "timeout"
        result["error"] = f"Stream timed out after {TIMEOUT_SECONDS}s"
        result["total_ms"] = int((time.monotonic() - t_start) * 1000)
        result["response_preview"] = full_response[:500]
        return result
    except Exception as e:
        result["status"] = "stream_error"
        result["error"] = str(e)
        result["total_ms"] = int((time.monotonic() - t_start) * 1000)
        result["response_preview"] = full_response[:500]
        return result

    t_end = time.monotonic()
    result["total_ms"] = int((t_end - t_start) * 1000)
    result["first_token_ms"] = int((t_first_token - t_start) * 1000) if t_first_token else None
    result["response_length"] = len(full_response)
    result["response_preview"] = full_response[:800]
    result["event_count"] = len(events_collected)
    result["mutation_applied"] = mutation_applied

    if error_text:
        result["status"] = "chat_error"
        result["error"] = error_text
        return result

    graphs_after = await list_graphs(client)
    new_graphs = graphs_after - graphs_before

    graph_data = await get_graph(client, workflow_id)

    if graph_data and count_nodes(graph_data) > 0:
        topo = check_topology(graph_data)
        result["graph_created"] = True
        result["graph_id"] = workflow_id
        result["topology"] = topo

        expected = prompt_def.get("expected", {})
        if expected.get("should_build_workflow") is False:
            result["status"] = "wrong_route"
            result["error"] = "Should NOT have built a workflow but did"
        elif expected.get("min_nodes") and topo["node_count"] < expected["min_nodes"]:
            result["status"] = "too_few_nodes"
            result["error"] = f"Expected >= {expected['min_nodes']} nodes, got {topo['node_count']}"
        else:
            result["status"] = "success"
    else:
        result["graph_created"] = False
        expected = prompt_def.get("expected", {})
        if expected.get("should_build_workflow") is False:
            result["status"] = "success"
        else:
            result["status"] = "no_graph"
            result["error"] = "No graph created"

    if new_graphs:
        result["new_graph_ids"] = sorted(new_graphs)

    return result


def print_report(results: list[dict]) -> None:
    """Print a summary table from results."""
    print("\n" + "=" * 80)
    print("PILOT RESULTS")
    print("=" * 80)

    by_tier: dict[str, list[dict]] = {}
    for r in results:
        by_tier.setdefault(r["tier"], []).append(r)

    total = len(results)
    total_pass = sum(1 for r in results if r["status"] == "success")

    for tier in sorted(by_tier):
        tier_results = by_tier[tier]
        tier_pass = sum(1 for r in tier_results if r["status"] == "success")
        print(f"\n--- {tier} ({tier_pass}/{len(tier_results)}) ---")
        for r in tier_results:
            status_icon = "PASS" if r["status"] == "success" else "FAIL"
            time_str = f"{r.get('total_ms', 0)/1000:.1f}s"
            nodes_str = ""
            if r.get("topology"):
                nodes_str = f" nodes={r['topology']['node_count']}"
            error_str = ""
            if r.get("error"):
                error_str = f" | {r['error'][:60]}"
            print(f"  [{status_icon}] {r['id']}: {time_str}{nodes_str}{error_str}")

    print(f"\n{'=' * 80}")
    print(f"OVERALL: {total_pass}/{total} ({total_pass/total*100:.0f}%)")

    times = [r.get("total_ms", 0) for r in results if r.get("total_ms")]
    if times:
        print(f"Avg time: {sum(times)/len(times)/1000:.1f}s | "
              f"Min: {min(times)/1000:.1f}s | Max: {max(times)/1000:.1f}s")

    failure_modes: dict[str, int] = {}
    for r in results:
        if r["status"] != "success":
            failure_modes[r["status"]] = failure_modes.get(r["status"], 0) + 1
    if failure_modes:
        print(f"\nFailure modes: {failure_modes}")

    print("=" * 80 + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description="DAN workflow generation pilot")
    parser.add_argument("--index", type=int, help="Run only prompt at this index")
    parser.add_argument("--report", type=str, help="Print report from existing JSONL file")
    parser.add_argument("--output", type=str, help="Output JSONL path")
    args = parser.parse_args()

    if args.report:
        results = []
        with open(args.report) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))
        print_report(results)
        return

    prompts = PILOT_PROMPTS
    if args.index is not None:
        prompts = [PILOT_PROMPTS[args.index]]

    output_path = Path(args.output) if args.output else (
        Path("tests/pilot/results") / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(f"{BASE_URL}/api/graphs")
            if resp.status_code != 200:
                print("Server not reachable. Run `dan-up` first.", file=sys.stderr)
                sys.exit(1)
        except Exception:
            print("Server not reachable. Run `dan-up` first.", file=sys.stderr)
            sys.exit(1)

        results: list[dict] = []

        for i, prompt_def in enumerate(prompts):
            wf_id = f"_pilot_{uuid.uuid4().hex[:8]}"
            print(f"[{i+1}/{len(prompts)}] {prompt_def['id']} ({prompt_def['tier']}): "
                  f"{prompt_def['prompt'][:60]}...")

            result = await run_prompt(client, prompt_def, wf_id)
            results.append(result)

            status = result["status"]
            t_ms = result.get("total_ms", 0)
            icon = "OK" if status == "success" else "XX"
            print(f"  -> [{icon}] {status} ({t_ms/1000:.1f}s)")
            if result.get("error"):
                print(f"     Error: {result['error'][:100]}")
            if result.get("topology"):
                topo = result["topology"]
                print(f"     Nodes: {topo['node_count']} | Edges: {topo['edge_count']} | "
                      f"Types: {topo['node_types']}")

            with open(output_path, "a") as f:
                f.write(json.dumps(result, default=str) + "\n")

    print_report(results)
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
