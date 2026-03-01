"""Control-flow executors — IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop, Composite, Orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid as _uuid
import warnings
from typing import Any

from dan.engine.conditions import ConditionError, evaluate_condition, evaluate_expression
from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.engine.events import EventType
from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    GateNode,
    HumanInTheLoopNode,
    IfElseNode,
    OrchestratorNode,
    ParallelSubagentsNode,
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
        warnings.warn(
            "IfElseNode is deprecated; use GateNode(gate_mode='if_else') instead",
            DeprecationWarning,
            stacklevel=2,
        )
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
# Gate
# ---------------------------------------------------------------------------


class GateExecutor:
    """Evaluates condition and routes data to exactly one branch output port.

    For if_else mode: evaluates condition, writes inputs to the active branch port only.
    For while mode: injects iteration counter from local state, then routes to
    'continue' or 'done'. The scheduler handles back-edge re-execution externally.
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, GateNode)

        scope: dict[str, Any] = {}
        if node.gate_mode == "while":
            scope = context.local_state.get_scope(node.id)
            if getattr(node, 'state_schema', None) and not scope:
                defaults = getattr(node, 'state_defaults', None) or {}
                init_state = dict(defaults)
                for k in node.state_schema:
                    if k in inputs:
                        init_state[k] = inputs[k]
                context.local_state.set_scope(node.id, init_state)
                scope = context.local_state.get_scope(node.id)

        if getattr(node, 'state_schema', None) and scope:
            condition_vars = dict(scope)
            condition_vars.update(inputs)
        else:
            condition_vars = dict(inputs)
            for _port, val in inputs.items():
                if isinstance(val, dict):
                    condition_vars.update(val.get("result", val))
                    break
            cond = (node.condition or "").strip()
            if cond and cond not in condition_vars and "input" in inputs:
                val = inputs["input"]
                if val is not None and not isinstance(val, dict):
                    condition_vars[cond] = val
            if "try_more" not in condition_vars and "try_more" in (node.condition or ""):
                condition_vars["try_more"] = True

        if node.gate_mode == "while":
            if getattr(node, 'state_schema', None):
                iteration = scope.get("iteration", 0)
            else:
                iteration = scope.get("gate_iteration", 0)
            condition_vars["iteration"] = iteration

        try:
            result = evaluate_condition(node.condition, condition_vars)
        except ConditionError as exc:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=str(exc),
            )

        if node.gate_mode == "if_else":
            active_branch = "true" if result else "false"
            return NodeResult(
                outputs={active_branch: inputs},
                status=NodeStatus.COMPLETED,
                metadata={"condition_result": result, "active_branch": active_branch},
            )

        # while mode
        active_branch = "continue" if result else "done"
        # For "done": pass body value directly so write_csv etc. get governor output, not {"input": body}
        out_val = inputs.get("input", inputs) if active_branch == "done" else inputs

        gate_data: dict[str, Any] = {
            "gate_mode": "while",
            "active_branch": active_branch,
            "iteration": condition_vars.get("iteration", 0),
            "max_iterations": node.max_iterations,
            "condition": node.condition,
            "condition_vars": dict(condition_vars),
        }
        await context.emit_event(
            event_type="gate_evaluated",
            node_id=node.id,
            node_type="gate",
            data=gate_data,
        )

        return NodeResult(
            outputs={active_branch: out_val},
            status=NodeStatus.COMPLETED,
            metadata={
                "condition_result": result,
                "active_branch": active_branch,
                "iteration": condition_vars.get("iteration", 0),
            },
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
        warnings.warn(
            "WhileLoopNode is deprecated; use GateNode(gate_mode='while') for flat visible loops",
            DeprecationWarning,
            stacklevel=2,
        )

        scope = context.local_state.get_scope(node.id)
        scope.setdefault("iteration", 0)
        scope.setdefault("history", [])

        _has_schema = getattr(node, 'state_schema', None) is not None
        if _has_schema:
            _defaults = getattr(node, 'state_defaults', None) or {}
            for _k in node.state_schema:
                if _k not in scope:
                    scope[_k] = _defaults.get(_k, inputs.get(_k))

        if _has_schema:
            working_data = dict(scope)
            working_data.update(inputs)
        else:
            working_data = dict(inputs)
        start_time = time.monotonic()
        max_iter = node.max_iterations
        if node.failure_policy.max_iterations is not None:
            max_iter = min(max_iter, node.failure_policy.max_iterations)

        prev_output: dict[str, Any] | None = None

        for iteration in range(max_iter):
            scope["iteration"] = iteration

            await context.emit_event(
                event_type="iteration_started",
                node_id=node.id,
                node_type="while_loop",
                data={"iteration": iteration, "max_iterations": max_iter, "condition": node.condition},
            )

            if _has_schema:
                condition_vars = dict(scope)
                condition_vars["iteration"] = iteration
            else:
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
            if _has_schema:
                _schema_updates = {k: v for k, v in body_output.items() if k in node.state_schema}
                scope.update(_schema_updates)
                working_data = dict(scope)
                working_data.update(body_output)
            else:
                working_data = {**working_data, **body_output}

            await context.emit_event(
                event_type="iteration_completed",
                node_id=node.id,
                node_type="while_loop",
                data={"iteration": iteration, "max_iterations": max_iter},
            )

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
# ParallelSubagents
# ---------------------------------------------------------------------------


class ParallelSubagentsExecutor:
    """Runs multiple sub-graphs concurrently; merges results at fan-in."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, ParallelSubagentsNode)

        if not node.branch_graphs:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error="parallel_subagents node has no branches",
            )

        def _branch_inputs(branch_key: str) -> dict[str, Any]:
            inner: dict[str, Any] = {}
            for outer_port, inner_port in node.input_mappings.items():
                if outer_port in inputs:
                    inner[inner_port] = inputs[outer_port]
            overrides = node.branch_inputs.get(branch_key, {})
            inner.update(overrides)
            return inner

        semaphore = asyncio.Semaphore(node.parallelism)

        async def run_branch(branch_key: str) -> dict[str, Any]:
            async with semaphore:
                await context.emit_event(
                    event_type="parallel_branch_started",
                    node_id=node.id,
                    node_type="parallel_subagents",
                    data={"branch_key": branch_key},
                )
                result = await context.run_subgraph(
                    branch_key, _branch_inputs(branch_key), parent_node_id=node.id
                )
                await context.emit_event(
                    event_type="parallel_branch_completed",
                    node_id=node.id,
                    node_type="parallel_subagents",
                    data={"branch_key": branch_key},
                )
                return result

        tasks = [run_branch(bk) for bk in node.branch_graphs]

        timeout = node.failure_policy.timeout_seconds
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"parallel_subagents timed out after {timeout}s",
            )

        outputs_list: list[dict[str, Any]] = []
        errors: list[str] = []
        for i, r in enumerate(results):
            branch_key = node.branch_graphs[i]
            if isinstance(r, Exception):
                errors.append(f"{branch_key}: {r}")
            else:
                outputs_list.append(r)

        if errors and not outputs_list:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"All parallel subagent branches failed: {'; '.join(errors)}",
            )

        merged = self._merge(outputs_list, node.merge_strategy, node.reducer)

        await context.emit_event(
            event_type="parallel_fan_in_completed",
            node_id=node.id,
            node_type="parallel_subagents",
            data={"branch_count": len(node.branch_graphs), "succeeded": len(outputs_list)},
        )

        return NodeResult(
            outputs={"results": merged},
            status=NodeStatus.COMPLETED,
            metadata={
                "branch_count": len(node.branch_graphs),
                "succeeded": len(outputs_list),
                "failed": len(errors),
            },
        )

    @staticmethod
    def _merge(
        results: list[dict[str, Any]],
        strategy: MergeStrategy,
        reducer: str | None = None,
    ) -> Any:
        if strategy == MergeStrategy.APPEND:
            return results
        if strategy == MergeStrategy.LAST_WRITE_WINS:
            merged: dict[str, Any] = {}
            for r in results:
                merged.update(r)
            return merged
        if strategy == MergeStrategy.REDUCER:
            if not reducer:
                raise ConditionError(
                    "merge_strategy is REDUCER but no reducer expression provided"
                )
            return evaluate_expression(reducer, {"inputs": results})
        return results


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
                await context.emit_event(
                    event_type="iteration_started",
                    node_id=node.id,
                    node_type="for_each",
                    data={"index": index, "total": len(items)},
                )
                item_input = {"item": item, "index": index}
                result = await context.run_subgraph(node.body_graph, item_input, parent_node_id=node.id)
                await context.emit_event(
                    event_type="iteration_completed",
                    node_id=node.id,
                    node_type="for_each",
                    data={"index": index, "total": len(items)},
                )
                return result

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
            result = evaluate_expression(node.reducer, {"inputs": inputs})
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

        model = node.model or context.config.llm_default_model

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
            chosen = await self._call_router_llm(context, model, prompt)
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

    @staticmethod
    async def _call_router_llm(context: ExecutionContext, model: str, prompt: str) -> str:
        """Dispatch to provider registry, falling back to direct AsyncOpenAI."""
        messages = [{"role": "user", "content": prompt}]

        if context.provider_registry is not None:
            provider = context.provider_registry.resolve(model)
            result = await provider.complete(
                messages=messages, model=model, temperature=0.0,
            )
            return result.text.strip()

        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,
        )
        return (resp.choices[0].message.content or "").strip()


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

        dynamic_prompt = inputs.get("user_prompt") or inputs.get("prompt")
        if isinstance(dynamic_prompt, str) and dynamic_prompt.strip():
            prompt = dynamic_prompt
        else:
            prompt = node.prompt or f"Human input needed for node '{node.name}':"

        request_id = str(_uuid.uuid4())
        await context.emit_event(
            event_type="human_input_needed",
            node_id=node.id,
            node_type="human_in_the_loop",
            data={"prompt": prompt, "request_id": request_id},
        )

        request_meta = {"node_id": node.id, "prompt": prompt, "request_id": request_id}
        try:
            if node.timeout_seconds is not None:
                response = await asyncio.wait_for(
                    context.human_input_callback(request_meta),
                    timeout=node.timeout_seconds,
                )
            else:
                response = await context.human_input_callback(request_meta)
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
            broadcast_inputs: dict[str, Any] = {}
            targeted_inputs: dict[str, dict[str, Any]] = {}
            for k, v in inputs.items():
                if k in node.input_mappings:
                    target = node.input_mappings[k]
                    if "::" in target:
                        target_node_id, port_name = target.split("::", 1)
                        targeted_inputs.setdefault(target_node_id, {})[port_name] = v
                    else:
                        broadcast_inputs[target] = v
                else:
                    broadcast_inputs[k] = v
            mapped_inputs = broadcast_inputs
        else:
            mapped_inputs = dict(inputs)
            targeted_inputs = {}

        body_output = await context.run_subgraph(
            node.body_graph, mapped_inputs,
            parent_node_id=node.id,
            targeted_inputs=targeted_inputs if targeted_inputs else None,
        )

        if node.output_mappings:
            mapped_outputs: dict[str, Any] = {}
            mapped_inner_keys: set[str] = set()
            for inner_key, outer_port in node.output_mappings.items():
                if "::" in inner_key:
                    _, port_name = inner_key.split("::", 1)
                    if port_name in body_output:
                        mapped_outputs[outer_port] = body_output[port_name]
                        mapped_inner_keys.add(port_name)
                else:
                    if inner_key in body_output:
                        mapped_outputs[outer_port] = body_output[inner_key]
                        mapped_inner_keys.add(inner_key)
            for k, v in body_output.items():
                if k not in mapped_inner_keys:
                    mapped_outputs[k] = v
        else:
            mapped_outputs = body_output

        return NodeResult(
            outputs=mapped_outputs,
            status=NodeStatus.COMPLETED,
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class OrchestratorExecutor:
    """Runs subgraph teams concurrently with an async event-processing loop.

    Unlike ParallelSubagentsExecutor (fire-and-forget gather), the orchestrator
    spawns teams as background asyncio tasks, then runs its own event loop
    concurrently — receiving events from teams and optionally writing back
    to shared context for bidirectional communication.
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, OrchestratorNode)

        if not node.teams:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error="orchestrator node has no teams",
            )

        event_queue: asyncio.Queue = asyncio.Queue()

        original_callback = context._event_callback

        async def routing_callback(event: Any) -> None:
            if original_callback:
                await original_callback(event)
            await event_queue.put(event)

        context._event_callback = routing_callback

        def _team_inputs(team_name: str) -> dict[str, Any]:
            inner: dict[str, Any] = {}
            for outer_port, inner_port in node.input_mappings.items():
                if outer_port in inputs:
                    inner[inner_port] = inputs[outer_port]
            overrides = node.team_inputs.get(team_name, {})
            inner.update(overrides)
            return inner

        team_tasks: dict[str, asyncio.Task] = {}
        team_results: dict[str, Any] = {}
        team_status: dict[str, str] = {}

        for team_name, sub_key in node.teams.items():
            team_status[team_name] = "running"
            task = asyncio.create_task(
                context.run_subgraph(sub_key, _team_inputs(team_name), parent_node_id=node.id)
            )
            team_tasks[team_name] = task

            await context.emit_event(
                event_type="parallel_branch_started",
                node_id=node.id,
                node_type="orchestrator",
                data={"branch_key": sub_key, "team_name": team_name},
            )

        for team_name, task in team_tasks.items():
            def _on_done(t: asyncio.Task, tn: str = team_name) -> None:
                if t.cancelled():
                    team_status[tn] = "failed"
                    team_results[tn] = {"error": "cancelled"}
                elif t.exception():
                    team_status[tn] = "failed"
                    team_results[tn] = {"error": str(t.exception())}
                else:
                    team_status[tn] = "completed"
                    team_results[tn] = t.result()
            task.add_done_callback(_on_done)

        orchestrator_log: list[dict] = []
        iteration = 0

        timeout = node.timeout_seconds
        start_time = asyncio.get_event_loop().time()

        try:
            while iteration < node.max_iterations:
                iteration += 1

                if timeout and (asyncio.get_event_loop().time() - start_time) > timeout:
                    for t in team_tasks.values():
                        if not t.done():
                            t.cancel()
                    break

                if node.completion_condition == "all_done":
                    if all(s != "running" for s in team_status.values()):
                        break
                elif node.completion_condition == "any_done":
                    if any(s == "completed" for s in team_status.values()):
                        for t in team_tasks.values():
                            if not t.done():
                                t.cancel()
                        break

                events_batch: list[Any] = []
                try:
                    while True:
                        event = event_queue.get_nowait()
                        events_batch.append(event)
                except asyncio.QueueEmpty:
                    pass

                if events_batch:
                    for event in events_batch:
                        evt_type = event.event_type
                        evt_type_str = evt_type.value if hasattr(evt_type, "value") else str(evt_type)
                        orchestrator_log.append({
                            "iteration": iteration,
                            "event_type": evt_type_str,
                            "node_id": event.node_id,
                            "data": event.data,
                        })

                        if evt_type == EventType.NODE_COMPLETED:
                            layer = event.data.get("layer_path", [])
                            team_for = None
                            for tn, sk in node.teams.items():
                                if layer and sk == layer[0]:
                                    team_for = tn
                                    break
                            if team_for:
                                try:
                                    context.shared_context.write(
                                        f"__orchestrator__{node.id}__received__{team_for}",
                                        event.data,
                                    )
                                except KeyError:
                                    pass
                else:
                    await asyncio.sleep(0.05)
        finally:
            context._event_callback = original_callback

        pending = [t for t in team_tasks.values() if not t.done()]
        if pending:
            done, still_pending = await asyncio.wait(pending, timeout=2.0)
            for t in still_pending:
                t.cancel()

        for team_name in node.teams:
            if team_name not in team_results:
                task = team_tasks[team_name]
                if task.done() and not task.cancelled():
                    exc = task.exception()
                    if exc:
                        team_results[team_name] = {"error": str(exc)}
                    else:
                        team_results[team_name] = task.result()
                else:
                    team_results[team_name] = {"error": "cancelled or not completed"}

        await context.emit_event(
            event_type="parallel_fan_in_completed",
            node_id=node.id,
            node_type="orchestrator",
            data={
                "team_count": len(node.teams),
                "completed": sum(1 for s in team_status.values() if s == "completed"),
                "failed": sum(1 for s in team_status.values() if s == "failed"),
            },
        )

        has_failures = any(s == "failed" for s in team_status.values())
        all_failed = all(s == "failed" for s in team_status.values())

        return NodeResult(
            outputs={
                "results": team_results,
                "orchestrator_log": orchestrator_log,
                "team_status": dict(team_status),
            },
            status=NodeStatus.FAILED if all_failed else NodeStatus.COMPLETED,
            error="All teams failed" if all_failed else None,
            metadata={
                "team_count": len(node.teams),
                "iterations": iteration,
                "team_status": dict(team_status),
            },
        )
