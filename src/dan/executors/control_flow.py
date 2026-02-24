"""Control-flow executors — IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop, Composite."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from dan.engine.conditions import ConditionError, evaluate_condition
from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    HumanInTheLoopNode,
    IfElseNode,
    ReduceNode,
    RouterNode,
    WhileLoopNode,
)
from dan.models.context import CompactionStrategy, MergeStrategy
from dan.models.nodes import NodeBase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IfElse
# ---------------------------------------------------------------------------


class IfElseExecutor:
    """Evaluates a condition and returns which branch to activate."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, IfElseNode)
        try:
            result = evaluate_condition(node.condition, inputs)
        except ConditionError as exc:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=str(exc),
            )

        branch = "true" if result else "false"
        return NodeResult(
            outputs={"branch": branch, **inputs},
            status=NodeStatus.COMPLETED,
            metadata={"condition_result": result, "branch": branch},
        )


# ---------------------------------------------------------------------------
# WhileLoop
# ---------------------------------------------------------------------------


class WhileLoopExecutor:
    """Iterates a body sub-graph until the condition is false or limits hit.

    Execution flow per iteration:
      1. Evaluate condition — if false, exit
      2. Execute body sub-graph with current inputs
      3. Apply compaction to local state
      4. Check failure policy (max_iterations, timeout, stagnation)
      5. Loop
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, WhileLoopNode)

        scope = context.local_state.get_scope(node.id)
        scope.setdefault("iteration", 0)
        scope.setdefault("history", [])

        working_data = dict(inputs)
        start_time = time.monotonic()
        max_iter = node.max_iterations
        if node.failure_policy.max_iterations is not None:
            max_iter = min(max_iter, node.failure_policy.max_iterations)

        prev_output: dict[str, Any] | None = None

        for iteration in range(max_iter):
            scope["iteration"] = iteration

            condition_vars = {**working_data, "iteration": iteration}
            try:
                should_continue = evaluate_condition(node.condition, condition_vars)
            except ConditionError as exc:
                return NodeResult(
                    outputs=working_data,
                    status=NodeStatus.FAILED,
                    error=f"Condition evaluation failed at iteration {iteration}: {exc}",
                )

            if not should_continue:
                break

            body_output = await context.run_subgraph(
                node.body_graph, working_data, parent_node_id=node.id
            )

            scope["history"].append(body_output)
            working_data = {**working_data, **body_output}

            self._apply_compaction(node, scope)

            if self._check_stagnation(node, prev_output, body_output, scope):
                logger.info(
                    "WhileLoop '%s' stopped: stagnation at iteration %d",
                    node.id, iteration,
                )
                break

            if node.failure_policy.timeout_seconds is not None:
                elapsed = time.monotonic() - start_time
                if elapsed > node.failure_policy.timeout_seconds:
                    logger.info(
                        "WhileLoop '%s' stopped: timeout after %.1fs",
                        node.id, elapsed,
                    )
                    break

            prev_output = body_output

        context.local_state.delete_scope(node.id)

        return NodeResult(
            outputs=working_data,
            status=NodeStatus.COMPLETED,
            metadata={
                "iterations": scope.get("iteration", 0) + 1,
                "elapsed": time.monotonic() - start_time,
            },
        )

    @staticmethod
    def _apply_compaction(node: WhileLoopNode, scope: dict[str, Any]) -> None:
        rule = node.compaction_rule
        if rule is None or rule.strategy == CompactionStrategy.NONE:
            return

        history = scope.get("history", [])
        if not history:
            return

        if rule.strategy == CompactionStrategy.KEEP_LAST:
            scope["history"] = history[-1:]
        elif rule.strategy == CompactionStrategy.SLIDING_WINDOW:
            window = rule.window_size or 3
            scope["history"] = history[-window:]
        elif rule.strategy == CompactionStrategy.DIFF_BASED:
            if len(history) > 2:
                scope["history"] = [history[0], history[-1]]

    @staticmethod
    def _check_stagnation(
        node: WhileLoopNode,
        prev_output: dict[str, Any] | None,
        current_output: dict[str, Any],
        scope: dict[str, Any],
    ) -> bool:
        threshold = node.failure_policy.stagnation_threshold
        if threshold is None or prev_output is None:
            return False

        if prev_output == current_output:
            scope.setdefault("stagnation_count", 0)
            scope["stagnation_count"] += 1
            return scope["stagnation_count"] >= threshold

        scope["stagnation_count"] = 0
        return False


# ---------------------------------------------------------------------------
# ForEach
# ---------------------------------------------------------------------------


class ForEachExecutor:
    """Fans out a sub-graph over each item in the input list."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, ForEachNode)

        items_key = "items"
        for key in ("items", "input", "data"):
            if key in inputs:
                items_key = key
                break

        items = inputs.get(items_key, [])
        if not isinstance(items, list):
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"ForEach expected a list at input key '{items_key}', got {type(items).__name__}",
            )

        semaphore = asyncio.Semaphore(node.parallelism)

        async def run_item(index: int, item: Any) -> dict[str, Any]:
            async with semaphore:
                item_input = {"item": item, "index": index}
                return await context.run_subgraph(node.body_graph, item_input, parent_node_id=node.id)

        tasks = [run_item(i, item) for i, item in enumerate(items)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        outputs: list[Any] = []
        errors: list[str] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                errors.append(f"Item {i}: {r}")
            else:
                outputs.append(r)

        if errors and not outputs:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"All ForEach branches failed: {'; '.join(errors)}",
            )

        merged = self._merge(outputs, node.merge_strategy)

        return NodeResult(
            outputs={"results": merged},
            status=NodeStatus.COMPLETED,
            metadata={
                "total_items": len(items),
                "succeeded": len(outputs),
                "failed": len(errors),
            },
        )

    @staticmethod
    def _merge(results: list[dict[str, Any]], strategy: MergeStrategy) -> Any:
        if strategy == MergeStrategy.APPEND:
            return results
        elif strategy == MergeStrategy.LAST_WRITE_WINS:
            merged: dict[str, Any] = {}
            for r in results:
                merged.update(r)
            return merged
        else:
            return results


# ---------------------------------------------------------------------------
# Reduce
# ---------------------------------------------------------------------------


class ReduceExecutor:
    """Aggregates outputs from upstream branches using a reducer expression."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, ReduceNode)

        try:
            result = evaluate_condition(node.reducer, {"inputs": inputs})
        except ConditionError as exc:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Reducer failed: {exc}",
            )

        if isinstance(result, dict):
            outputs = result
        else:
            outputs = {"result": result}

        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class RouterExecutor:
    """LLM-powered routing — the model decides which branch to activate."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, RouterNode)

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )

        route_desc = "\n".join(
            f"- {name}: {desc}"
            for name, desc in node.route_descriptions.items()
        )
        route_names = list(node.route_descriptions.keys())

        prompt = (
            f"Given the following input data:\n{json.dumps(inputs, indent=2, default=str)}\n\n"
            f"Choose exactly one of these routes:\n{route_desc}\n\n"
            f"Respond with ONLY the route name, one of: {route_names}"
        )

        try:
            resp = await client.chat.completions.create(
                model=node.model or context.config.llm_default_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            chosen = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Router LLM call failed: {exc}",
            )

        if chosen not in node.route_descriptions:
            for name in route_names:
                if name.lower() in chosen.lower():
                    chosen = name
                    break
            else:
                return NodeResult(
                    outputs={},
                    status=NodeStatus.FAILED,
                    error=f"Router returned unknown route '{chosen}'. Expected one of: {route_names}",
                )

        return NodeResult(
            outputs={"route": chosen, **inputs},
            status=NodeStatus.COMPLETED,
            metadata={"chosen_route": chosen},
        )


# ---------------------------------------------------------------------------
# HumanInTheLoop
# ---------------------------------------------------------------------------


class HumanInTheLoopExecutor:
    """Pauses execution and awaits human input via a callback."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, HumanInTheLoopNode)

        if context.human_input_callback is None:
            if node.default_action is not None:
                return NodeResult(
                    outputs={"response": node.default_action, **inputs},
                    status=NodeStatus.COMPLETED,
                    metadata={"source": "default_action"},
                )
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error="HumanInTheLoop requires a human_input_callback but none was provided",
            )

        prompt = node.prompt or f"Human input needed for node '{node.name}':"

        try:
            if node.timeout_seconds is not None:
                response = await asyncio.wait_for(
                    context.human_input_callback(prompt),
                    timeout=node.timeout_seconds,
                )
            else:
                response = await context.human_input_callback(prompt)
        except asyncio.TimeoutError:
            if node.default_action is not None:
                return NodeResult(
                    outputs={"response": node.default_action, **inputs},
                    status=NodeStatus.COMPLETED,
                    metadata={"source": "timeout_default"},
                )
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"HumanInTheLoop timed out after {node.timeout_seconds}s",
            )
        except Exception as exc:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Human input callback failed: {exc}",
            )

        if isinstance(response, dict):
            outputs = {**inputs, **response}
        else:
            outputs = {**inputs, "response": response}

        return NodeResult(
            outputs=outputs,
            status=NodeStatus.COMPLETED,
            metadata={"source": "human"},
        )


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


class CompositeExecutor:
    """Runs a named sub-graph once with port-mapped inputs/outputs.

    Unlike WhileLoop/ForEach, Composite does not iterate — it is a single
    "call" to the sub-graph, with input_mappings renaming outer port names
    to inner entry-point port names and output_mappings renaming inner
    exit-point port names back to outer port names.
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, CompositeNode)

        if node.input_mappings:
            mapped_inputs = {
                node.input_mappings[k]: v
                for k, v in inputs.items()
                if k in node.input_mappings
            }
            for k, v in inputs.items():
                if k not in node.input_mappings:
                    mapped_inputs[k] = v
        else:
            mapped_inputs = dict(inputs)

        body_output = await context.run_subgraph(node.body_graph, mapped_inputs, parent_node_id=node.id)

        if node.output_mappings:
            mapped_outputs: dict[str, Any] = {}
            for inner_port, outer_port in node.output_mappings.items():
                if inner_port in body_output:
                    mapped_outputs[outer_port] = body_output[inner_port]
            for k, v in body_output.items():
                if k not in node.output_mappings:
                    mapped_outputs[k] = v
        else:
            mapped_outputs = body_output

        return NodeResult(
            outputs=mapped_outputs,
            status=NodeStatus.COMPLETED,
        )
