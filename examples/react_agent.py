"""ReAct agent loop: think → act → observe → repeat.

# Architecture
#
#   [init]           Code — initializes agent state
#       |
#       v
#   [agent_loop]     WhileLoop(condition="not done", max_iterations=5)
#     |  body:
#     |    [loop_in]   Code — unpack loop state
#     |       v
#     |    [think]     LLM — decides next action (JSON: thought, action,
#     |       |                action_input, done)
#     |       v
#     |    [dispatch]  Code — routes to tool based on action field
#     |       v
#     |    [search]    ToolOperator(web_search) — searches the web
#     |       v
#     |    [fetch]     ToolOperator(web_fetch) — fetches a URL
#     |       v
#     |    [merge]     Code — merges observation into state for next iteration
#
#   Topology: while-loop with LLM reasoning and tool dispatch.
#   Demonstrates: tool-augmented reasoning, multi-turn agent loop,
#                 conditional tool selection, graceful termination.

Usage:
    python examples/react_agent.py
    python examples/react_agent.py --task "Find the current population of Tokyo"
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.engine import Engine, EngineConfig
from dan.engine.executor import ExecutorRegistry
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.context import CompactionRule, CompactionStrategy, FailurePolicy

THINK_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string"},
        "action_input": {"type": "string"},
        "done": {"type": "boolean"},
        "final_answer": {"type": "string"},
    },
    "required": ["thought", "action", "action_input", "done"],
}

INIT_CODE = """\
result = {
    "task": task,
    "observations": "",
    "done": False,
    "step_count": 0,
    "final_answer": "",
}
"""

LOOP_IN_CODE = """\
result = {
    "task": task,
    "observations": observations,
    "done": done,
    "step_count": step_count,
    "final_answer": final_answer,
}
"""

DISPATCH_CODE = """\
search_query = ""
fetch_url = ""
if action == "search":
    search_query = str(action_input)
elif action == "fetch":
    fetch_url = str(action_input)
result = {
    "search_query": search_query,
    "fetch_url": fetch_url,
    "action": action,
    "action_input": action_input,
    "thought": thought,
    "done": done,
    "final_answer": final_answer if isinstance(final_answer, str) else "",
}
"""

MERGE_CODE = """\
search_result = search_results if isinstance(search_results, str) else str(search_results or "")
fetch_result = fetch_content if isinstance(fetch_content, str) else str(fetch_content or "")

if action == "search" and search_result:
    observation = search_result[:1000]
elif action == "fetch" and fetch_result:
    observation = fetch_result[:1000]
elif action == "none" or done:
    observation = ""
else:
    observation = f"No result for action '{action}'"

prev = observations if isinstance(observations, str) else ""
new_obs = prev
if observation:
    new_obs = prev + f"\\n\\n[Step {step_count + 1}] Action: {action}({action_input})\\nObservation: {observation}"

result = {
    "task": task,
    "observations": new_obs,
    "done": bool(done),
    "step_count": int(step_count) + 1,
    "final_answer": final_answer if isinstance(final_answer, str) else "",
}
"""


def build_react_agent():
    """Build a ReAct agent loop workflow."""
    wf = workflow(
        "react_agent",
        description="ReAct agent with tool-augmented reasoning loop",
        tags=["template", "agent", "react", "tools"],
    )

    init = wf.code(
        "init",
        code=INIT_CODE,
        input_ports=[{"name": "task"}],
        output_ports=[
            {"name": "task"},
            {"name": "observations"},
            {"name": "done"},
            {"name": "step_count"},
            {"name": "final_answer"},
        ],
    )

    with wf.while_loop(
        "agent_loop",
        condition="not done",
        max_iterations=5,
        compaction=CompactionRule(strategy=CompactionStrategy.SLIDING_WINDOW, window_size=3),
        failure_policy=FailurePolicy(max_iterations=5, stagnation_threshold=3),
        input_ports=[
            {"name": "task"},
            {"name": "observations"},
            {"name": "done"},
            {"name": "step_count"},
            {"name": "final_answer"},
        ],
        output_ports=[
            {"name": "task"},
            {"name": "observations"},
            {"name": "done"},
            {"name": "step_count"},
            {"name": "final_answer"},
        ],
    ) as body:
        loop_in = body.code(
            "loop_in",
            code=LOOP_IN_CODE,
            input_ports=[
                {"name": "task"},
                {"name": "observations"},
                {"name": "done"},
                {"name": "step_count"},
                {"name": "final_answer"},
            ],
            output_ports=[
                {"name": "task"},
                {"name": "observations"},
                {"name": "done"},
                {"name": "step_count"},
                {"name": "final_answer"},
            ],
        )

        think = body.llm(
            "think",
            prompt=(
                "You are a ReAct agent. Decide the next action to complete the task.\n\n"
                "Task: {task}\n\n"
                "Previous observations:\n{observations}\n\n"
                "Available actions:\n"
                "- search: Search the web for information (input: search query)\n"
                "- fetch: Fetch content from a URL (input: URL)\n"
                "- none: No action needed\n\n"
                "If you have enough information to answer, set done=true and "
                "provide final_answer. Otherwise pick an action."
            ),
            output_schema=THINK_SCHEMA,
            input_ports=[{"name": "task"}, {"name": "observations"}],
        )
        body.edge(loop_in["task"], think["task"])
        body.edge(loop_in["observations"], think["observations"])

        dispatch = body.code(
            "dispatch",
            code=DISPATCH_CODE,
            input_ports=[
                {"name": "action"},
                {"name": "action_input"},
                {"name": "thought"},
                {"name": "done"},
                {"name": "final_answer"},
            ],
            output_ports=[
                {"name": "search_query"},
                {"name": "fetch_url"},
                {"name": "action"},
                {"name": "action_input"},
                {"name": "thought"},
                {"name": "done"},
                {"name": "final_answer"},
            ],
        )
        body.edge(think["action"], dispatch["action"])
        body.edge(think["action_input"], dispatch["action_input"])
        body.edge(think["thought"], dispatch["thought"])
        body.edge(think["done"], dispatch["done"])
        body.edge(think["final_answer"], dispatch["final_answer"])

        search = body.tool(
            "search",
            tool_id="web_search",
            input_ports=[{"name": "query"}],
            output_ports=[{"name": "results"}],
        )
        body.edge(dispatch["search_query"], search["query"])

        fetch = body.tool(
            "fetch",
            tool_id="web_fetch",
            input_ports=[{"name": "url"}],
            output_ports=[{"name": "content"}],
        )
        body.edge(dispatch["fetch_url"], fetch["url"])

        merge = body.code(
            "merge",
            code=MERGE_CODE,
            input_ports=[
                {"name": "task"},
                {"name": "observations"},
                {"name": "action"},
                {"name": "action_input"},
                {"name": "thought"},
                {"name": "done"},
                {"name": "step_count"},
                {"name": "final_answer"},
                {"name": "search_results"},
                {"name": "fetch_content"},
            ],
            output_ports=[
                {"name": "task"},
                {"name": "observations"},
                {"name": "done"},
                {"name": "step_count"},
                {"name": "final_answer"},
            ],
        )
        body.edge(loop_in["task"], merge["task"])
        body.edge(loop_in["observations"], merge["observations"])
        body.edge(loop_in["step_count"], merge["step_count"])
        body.edge(dispatch["action"], merge["action"])
        body.edge(dispatch["action_input"], merge["action_input"])
        body.edge(dispatch["thought"], merge["thought"])
        body.edge(dispatch["done"], merge["done"])
        body.edge(dispatch["final_answer"], merge["final_answer"])
        body.edge(search["results"], merge["search_results"])
        body.edge(fetch["content"], merge["fetch_content"])

    loop_ref = NodeRef("agent_loop", "while_loop", wf)
    for port in ["task", "observations", "done", "step_count", "final_answer"]:
        wf.edge(init[port], loop_ref[port])

    return wf.build()


async def main(task: str = "Find the current population of Tokyo") -> None:
    graph = build_react_agent()

    graphs_dir = Path("graphs")
    graphs_dir.mkdir(exist_ok=True)
    graph_path = graphs_dir / "react_agent.json"
    graph_path.write_text(json.dumps(graph.model_dump(mode="json"), indent=2))
    print(f"Graph saved to {graph_path}")

    api_key = os.environ.get("DAN_LLM_API_KEY", "")
    if api_key:
        tool_registry = ToolRegistry()
        tool_registry.register_builtin_tools()

        exec_registry = ExecutorRegistry()
        exec_registry.register("tool_operator", ToolExecutor(tool_registry))

        config = EngineConfig(llm_api_key=api_key)
        engine = Engine(config, executor_registry=exec_registry)
        result = await engine.run(graph, inputs={"task": task})
        print(f"Success: {result.success}")
        print(f"Outputs: {json.dumps(result.outputs, indent=2, default=str)}")
    else:
        print("Set DAN_LLM_API_KEY to run the workflow")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ReAct agent template")
    parser.add_argument("--task", default="Find the current population of Tokyo")
    args = parser.parse_args()
    asyncio.run(main(args.task))
