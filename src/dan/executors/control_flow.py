"""Control-flow executors — IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop, Composite, Orchestrator, Vote, AgentTeam."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid as _uuid
import warnings
from typing import Any

from pydantic import BaseModel

from dan.engine.conditions import ConditionError, evaluate_condition, evaluate_expression
from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.engine.events import EventType
from dan.providers import CompletionResult
from dan.models.control_flow import (
    AgentTeamNode,
    CompositeNode,
    ForEachNode,
    GateNode,
    HandoffRequest,
    HumanInTheLoopNode,
    HumanNode,
    IfElseNode,
    OrchestratorNode,
    ParallelSubagentsNode,
    ReduceNode,
    RouterNode,
    TeamConversation,
    TeamMessage,
    VoteConfig,
    VoteNode,
    WhileLoopNode,
)
from dan.models.context import CompactionStrategy, FeedbackSelector, MergeStrategy
from dan.models.nodes import NodeBase
from dan.engine.state_store import LoopIterationState, TeamTurnState
from dan.engine.token_optimization import LoopCompactor
from dan.utils.tokens import estimate_tokens

logger = logging.getLogger(__name__)


def _apply_feedback_selector(data: dict[str, Any], selector: FeedbackSelector) -> dict[str, Any]:
    """Filter/rename/transform a feedback dict according to *selector*."""
    filtered = dict(data)

    if selector.include is not None:
        filtered = {k: v for k, v in filtered.items() if k in selector.include}
    elif selector.exclude is not None:
        filtered = {k: v for k, v in filtered.items() if k not in selector.exclude}

    if selector.rename:
        filtered = {selector.rename.get(k, k): v for k, v in filtered.items()}

    if selector.transform is not None:
        filtered = evaluate_expression(selector.transform, {"inputs": filtered})
        if not isinstance(filtered, dict):
            filtered = {"result": filtered}

    return filtered


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
            _iter_start = time.monotonic()

            if _has_schema:
                condition_vars = dict(scope)
                condition_vars["iteration"] = iteration
            else:
                condition_vars = {**working_data, "iteration": iteration}
            await context.emit_event(
                event_type="iteration_started",
                node_id=node.id,
                node_type="while_loop",
                data={
                    "iteration": iteration,
                    "max_iterations": max_iter,
                    "condition": node.condition,
                    "context_tokens": estimate_tokens(json.dumps(condition_vars, default=str)),
                },
            )
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

            # -- 16-4: artifact extraction --------------------------------
            feedback_data = dict(body_output)
            if node.artifact_ports:
                artifact_scope = context.local_state.get_scope(f"{node.id}__artifacts")
                artifact_list = artifact_scope.setdefault("items", [])
                iter_artifacts = {k: v for k, v in feedback_data.items() if k in node.artifact_ports}
                if iter_artifacts:
                    artifact_list.append(iter_artifacts)
                feedback_data = {k: v for k, v in feedback_data.items() if k not in node.artifact_ports}

            # -- 16-4: feedback selector ----------------------------------
            if node.feedback_selector is not None:
                feedback_data = _apply_feedback_selector(feedback_data, node.feedback_selector)

            scope["history"].append(body_output)
            if _has_schema:
                _schema_updates = {k: v for k, v in body_output.items() if k in node.state_schema}
                scope.update(_schema_updates)
                working_data = dict(scope)
                working_data.update(feedback_data)
            else:
                working_data = {**working_data, **feedback_data}

            await context.emit_event(
                event_type="iteration_completed",
                node_id=node.id,
                node_type="while_loop",
                data={"iteration": iteration, "max_iterations": max_iter},
            )

            if getattr(context, "state_store", None) is not None:
                _iter_elapsed = time.monotonic() - _iter_start
                try:
                    iter_state = LoopIterationState(
                        iteration=iteration,
                        status="completed",
                        duration_ms=round(_iter_elapsed * 1000, 1),
                        output_keys=list(body_output.keys()) if isinstance(body_output, dict) else [],
                        output_preview=str(body_output)[:200],
                    )
                    await context.state_store.write(
                        context.state.run_id,
                        f"loop:{node.id}:iter:{iteration}",
                        iter_state,
                    )
                    await context.emit_event(
                        event_type="state_externalized",
                        node_id=node.id,
                        node_type="while_loop",
                        data={"scope": context.state.run_id, "keys_written": [f"loop:{node.id}:iter:{iteration}"]},
                    )
                except Exception:
                    logger.debug(
                        "Failed to externalize state for %s",
                        node.id, exc_info=True,
                    )

            await self._apply_compaction(node, scope, context, iteration)

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

        # -- 16-4: merge accumulated artifacts into final output ----------
        final_outputs = dict(working_data)
        artifact_scope_id = f"{node.id}__artifacts"
        if node.artifact_ports and context.local_state.has_scope(artifact_scope_id):
            artifact_scope = context.local_state.get_scope(artifact_scope_id)
            final_outputs["artifacts"] = artifact_scope.get("items", [])
            context.local_state.delete_scope(artifact_scope_id)

        context.local_state.delete_scope(node.id)

        return NodeResult(
            outputs=final_outputs,
            status=NodeStatus.COMPLETED,
            metadata={
                "iterations": scope.get("iteration", 0) + 1,
                "elapsed": time.monotonic() - start_time,
            },
        )

    @staticmethod
    async def _apply_compaction(
        node: WhileLoopNode,
        scope: dict[str, Any],
        context: ExecutionContext,
        iteration: int,
    ) -> None:
        rule = node.compaction_rule
        if rule is None or rule.strategy == CompactionStrategy.NONE:
            return

        history = scope.get("history", [])
        if not history:
            return

        compactor = LoopCompactor(
            rule,
            memory=context.short_term_memory,
            state_store=getattr(context, "state_store", None),
        )
        compacted, emit_data = await compactor.compact(
            history,
            loop_node_id=node.id,
            iteration=iteration,
        )
        scope["history"] = compacted

        await context.emit_event(
            event_type="loop_compaction_applied",
            node_id=node.id,
            node_type="while_loop",
            data={"iteration": iteration, **emit_data},
        )

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
        scope = context.local_state.get_scope(node.id)
        completed_branches = scope.setdefault("completed_branches", {})

        async def run_branch(branch_key: str) -> dict[str, Any]:
            if branch_key in completed_branches:
                return completed_branches[branch_key]
                
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
                completed_branches[branch_key] = result
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
# GoalLoop
# ---------------------------------------------------------------------------


class GoalLoopExecutor:
    """Iterates a body sub-graph until a goal metric is satisfied or limits hit.

    Each iteration:
      1. Run the body sub-graph with current inputs
      2. Extract the metric from body output
      3. Check if metric meets the target (using comparison operator)
      4. If met, return success; otherwise loop
    """

    _CMP_FNS: dict[str, Any] = {
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: abs(a - b) < 1e-9,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
    }

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        from dan.models.control_flow import GoalLoopNode

        assert isinstance(node, GoalLoopNode)

        scope = context.local_state.get_scope(node.id)
        scope.setdefault("iteration", 0)
        scope.setdefault("best_score", None)

        cmp_fn = self._CMP_FNS.get(node.comparison)
        if cmp_fn is None:
            return NodeResult(
                outputs=inputs,
                status=NodeStatus.FAILED,
                error=f"Unknown comparison operator: {node.comparison!r}",
            )

        working_data = dict(inputs)
        working_data["goal_text"] = node.goal_text
        best_output: dict[str, Any] = {}

        for iteration in range(node.max_iterations):
            scope["iteration"] = iteration

            await context.emit_event(
                event_type="iteration_started",
                node_id=node.id,
                node_type="goal_loop",
                data={
                    "iteration": iteration,
                    "max_iterations": node.max_iterations,
                    "goal_text": node.goal_text,
                    "metric": node.metric_name,
                    "target": node.target_value,
                },
            )

            body_output = await context.run_subgraph(
                node.body_graph, working_data, parent_node_id=node.id,
            )

            score = body_output.get(node.metric_name)
            if score is None:
                try:
                    score = float(body_output.get("result", 0))
                except (TypeError, ValueError):
                    score = 0.0
            else:
                score = float(score)

            if scope["best_score"] is None or cmp_fn(score, scope["best_score"]):
                scope["best_score"] = score
                best_output = dict(body_output)

            if node.success_criteria:
                try:
                    met = evaluate_condition(node.success_criteria, {**body_output, "score": score})
                except ConditionError:
                    met = False
            else:
                met = cmp_fn(score, node.target_value)

            await context.emit_event(
                event_type="iteration_completed",
                node_id=node.id,
                node_type="goal_loop",
                data={
                    "iteration": iteration,
                    "score": score,
                    "target": node.target_value,
                    "met": met,
                    "best_score": scope["best_score"],
                },
            )

            if met:
                return NodeResult(
                    outputs={**best_output, "goal_met": True, "iterations": iteration + 1, "best_score": score},
                    status=NodeStatus.COMPLETED,
                )

            working_data = {**inputs, **body_output, "goal_text": node.goal_text}

        return NodeResult(
            outputs={**best_output, "goal_met": False, "iterations": node.max_iterations, "best_score": scope["best_score"]},
            status=NodeStatus.COMPLETED,
        )


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
                _branch_start = time.monotonic()
                item_input = {"item": item, "index": index}
                result = await context.run_subgraph(node.body_graph, item_input, parent_node_id=node.id)
                await context.emit_event(
                    event_type="iteration_completed",
                    node_id=node.id,
                    node_type="for_each",
                    data={"index": index, "total": len(items)},
                )
                if getattr(context, "state_store", None) is not None:
                    _branch_elapsed = time.monotonic() - _branch_start
                    try:
                        branch_state = LoopIterationState(
                            iteration=index,
                            status="completed",
                            duration_ms=round(_branch_elapsed * 1000, 1),
                            output_keys=list(result.keys()) if isinstance(result, dict) else [],
                            output_preview=str(result)[:200],
                        )
                        await context.state_store.write(
                            context.state.run_id,
                            f"foreach:{node.id}:branch:{index}",
                            branch_state,
                        )
                        await context.emit_event(
                            event_type="state_externalized",
                            node_id=node.id,
                            node_type="for_each",
                            data={"scope": context.state.run_id, "keys_written": [f"foreach:{node.id}:branch:{index}"]},
                        )
                    except Exception:
                        logger.debug(
                            "Failed to externalize state for %s",
                            node.id, exc_info=True,
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

        compacted_outputs = await self._apply_compaction(
            node, outputs, context, len(items),
        )

        merged = self._merge(compacted_outputs, node.merge_strategy)

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
    async def _apply_compaction(
        node: ForEachNode,
        outputs: list[Any],
        context: ExecutionContext,
        total_items: int,
    ) -> list[Any]:
        rule = node.compaction_rule
        if rule is None or rule.strategy == CompactionStrategy.NONE:
            return outputs
        dict_outputs = [
            o if isinstance(o, dict) else {"__value__": o} for o in outputs
        ]
        if not dict_outputs:
            return outputs

        compactor = LoopCompactor(
            rule,
            memory=context.short_term_memory,
            state_store=getattr(context, "state_store", None),
        )
        compacted, emit_data = await compactor.compact(
            dict_outputs,
            loop_node_id=node.id,
            iteration=total_items - 1,
        )
        await context.emit_event(
            event_type="loop_compaction_applied",
            node_id=node.id,
            node_type="for_each",
            data={"total_items": total_items, **emit_data},
        )
        return compacted

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
        tier_params: dict[str, Any] = {}
        if context.model_selector is not None:
            effective_policy = context.model_selector.resolve_effective_policy(
                node, context.config,
            )
            if effective_policy is not None:
                select_result = await context.model_selector.select(
                    effective_policy, node, context,
                )
                if select_result.model:
                    model = select_result.model
                    tier_params = select_result.tier_params

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
            chosen = await self._call_router_llm(context, model, prompt, tier_params=tier_params)
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
    async def _call_router_llm(
        context: ExecutionContext,
        model: str,
        prompt: str,
        tier_params: dict[str, Any] | None = None,
    ) -> str:
        """Dispatch to provider registry, falling back to direct AsyncOpenAI."""
        messages = [{"role": "user", "content": prompt}]

        effective_temp = 0.0
        extra_kwargs: dict[str, Any] = {}
        if tier_params:
            effective_temp = tier_params.get("temperature", effective_temp)
            for k, v in tier_params.items():
                if k not in ("temperature", "max_tokens"):
                    extra_kwargs[k] = v

        if context.provider_registry is not None:
            provider = context.provider_registry.resolve(model)
            async with context.llm_slot():
                result = await provider.complete(
                    messages=messages, model=model, temperature=effective_temp,
                    **extra_kwargs,
                )
            return result.text.strip()

        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )
        async with context.llm_slot():
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=effective_temp,
            )
        return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# HumanNode (generalized — Plan 16-3)
# ---------------------------------------------------------------------------

_MAX_VALIDATION_RETRIES = 2


def _validate_against_schema(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate *data* against a JSON Schema.  Returns a list of error messages."""
    try:
        import jsonschema
        validator = jsonschema.Draft7Validator(schema)
        return [e.message for e in validator.iter_errors(data)]
    except ImportError:
        pass

    # Lightweight fallback when jsonschema is not installed
    errors: list[str] = []
    required = schema.get("required", [])
    props = schema.get("properties", {})
    for key in required:
        if key not in data:
            errors.append(f"Missing required key: '{key}'")
    for key, prop_schema in props.items():
        if key not in data:
            continue
        expected_type = prop_schema.get("type")
        if expected_type == "boolean" and not isinstance(data[key], bool):
            errors.append(f"Key '{key}' must be boolean")
        elif expected_type == "string" and not isinstance(data[key], str):
            errors.append(f"Key '{key}' must be string")
        elif expected_type == "number" and not isinstance(data[key], (int, float)):
            errors.append(f"Key '{key}' must be number")
    return errors


class HumanNodeExecutor:
    """Pauses execution and awaits human input via the rendering surface protocol.

    Falls back to the legacy ``human_input_callback`` when no renderer is
    configured.  Validates responses against ``output_schema`` with up to
    two retries before failing or falling back to ``default_action``.
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, HumanNode)

        from dan.engine.executor import HumanRenderRequest, HumanRenderResponse

        # Resolve dynamic prompt from inputs (backward compat)
        dynamic_prompt = inputs.get("user_prompt") or inputs.get("prompt")
        if isinstance(dynamic_prompt, str) and dynamic_prompt.strip():
            prompt = dynamic_prompt
        else:
            prompt = node.prompt or f"Human input needed for node '{node.name}':"

        render_mode = getattr(node, "render_mode", "text")
        instructions = getattr(node, "instructions", "")
        options = getattr(node, "options", None)
        input_schema = getattr(node, "input_schema", None)
        output_schema = getattr(node, "output_schema", None)
        render_target = getattr(node, "render_target", "dialog")

        request = HumanRenderRequest(
            node_id=node.id,
            node_name=node.name,
            render_mode=render_mode,
            prompt=prompt,
            instructions=instructions,
            input_data=dict(inputs),
            input_schema=input_schema,
            output_schema=output_schema,
            options=options,
            timeout_seconds=node.timeout_seconds,
            default_action=node.default_action,
            render_target=render_target,
        )

        await context.emit_event(
            event_type="human_input_needed",
            node_id=node.id,
            node_type="human",
            data={
                "prompt": prompt,
                "request_id": request.request_id,
                "render_mode": render_mode,
                "instructions": instructions,
                "options": options,
                "input_schema": input_schema,
                "output_schema": output_schema,
                "render_target": render_target,
            },
        )

        # ---- Renderer path (preferred) ----
        if context.human_renderer is not None:
            return await self._render_with_retries(node, request, inputs, context, output_schema)

        # ---- Legacy callback path ----
        if context.human_input_callback is not None:
            return await self._legacy_callback(node, request, inputs, context)

        # ---- No interaction surface ----
        if node.default_action is not None:
            return NodeResult(
                outputs={"response": node.default_action, **inputs},
                status=NodeStatus.COMPLETED,
                metadata={"source": "default_action"},
            )
        return NodeResult(
            outputs={},
            status=NodeStatus.FAILED,
            error="HumanNode requires a human_renderer or human_input_callback but none was provided",
        )

    async def _render_with_retries(
        self,
        node: HumanNode,
        request,
        inputs: dict[str, Any],
        context: ExecutionContext,
        output_schema: dict[str, Any] | None,
    ) -> NodeResult:
        from dan.engine.executor import HumanRenderRequest

        for attempt in range(_MAX_VALIDATION_RETRIES + 1):
            try:
                if node.timeout_seconds is not None:
                    response = await asyncio.wait_for(
                        context.human_renderer.render(request),
                        timeout=node.timeout_seconds,
                    )
                else:
                    response = await context.human_renderer.render(request)
            except asyncio.TimeoutError:
                return self._timeout_result(node, inputs)
            except Exception as exc:
                return NodeResult(
                    outputs={},
                    status=NodeStatus.FAILED,
                    error=f"Human renderer failed: {exc}",
                )

            if output_schema and response.source == "human":
                errors = _validate_against_schema(response.data, output_schema)
                if errors:
                    if attempt < _MAX_VALIDATION_RETRIES:
                        request = HumanRenderRequest(
                            request_id=request.request_id,
                            node_id=request.node_id,
                            node_name=request.node_name,
                            render_mode=request.render_mode,
                            prompt=request.prompt,
                            instructions=(
                                f"Validation failed: {'; '.join(errors)}. Please try again.\n\n"
                                + request.instructions
                            ),
                            input_data=request.input_data,
                            input_schema=request.input_schema,
                            output_schema=request.output_schema,
                            options=request.options,
                            timeout_seconds=request.timeout_seconds,
                            default_action=request.default_action,
                            render_target=request.render_target,
                        )
                        continue
                    if node.default_action is not None:
                        await self._emit_received(context, node, request.request_id, {"response": node.default_action}, "default")
                        return NodeResult(
                            outputs={"response": node.default_action, **inputs},
                            status=NodeStatus.COMPLETED,
                            metadata={"source": "default_action", "validation_errors": errors},
                        )
                    return NodeResult(
                        outputs={},
                        status=NodeStatus.FAILED,
                        error=f"Output schema validation failed after {_MAX_VALIDATION_RETRIES} retries: {'; '.join(errors)}",
                    )

            await self._emit_received(context, node, response.request_id, response.data, response.source)
            return NodeResult(
                outputs={**inputs, **response.data},
                status=NodeStatus.COMPLETED,
                metadata={"source": response.source, "render_mode": getattr(node, "render_mode", "text")},
            )

        # Should not reach here, but safety fallback
        return NodeResult(outputs={}, status=NodeStatus.FAILED, error="Unexpected retry exhaustion")

    async def _legacy_callback(
        self,
        node: HumanNode,
        request,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        request_meta = {"node_id": node.id, "prompt": request.prompt, "request_id": request.request_id}
        try:
            if node.timeout_seconds is not None:
                response = await asyncio.wait_for(
                    context.human_input_callback(request_meta),
                    timeout=node.timeout_seconds,
                )
            else:
                response = await context.human_input_callback(request_meta)
        except asyncio.TimeoutError:
            return self._timeout_result(node, inputs)
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

        await self._emit_received(context, node, request.request_id, outputs, "human")
        return NodeResult(
            outputs=outputs,
            status=NodeStatus.COMPLETED,
            metadata={"source": "human"},
        )

    @staticmethod
    def _timeout_result(node: HumanNode, inputs: dict[str, Any]) -> NodeResult:
        if node.default_action is not None:
            return NodeResult(
                outputs={"response": node.default_action, **inputs},
                status=NodeStatus.COMPLETED,
                metadata={"source": "timeout_default"},
            )
        return NodeResult(
            outputs={},
            status=NodeStatus.FAILED,
            error=f"HumanNode timed out after {node.timeout_seconds}s",
        )

    @staticmethod
    async def _emit_received(
        context: ExecutionContext,
        node: HumanNode,
        request_id: str,
        data: dict[str, Any],
        source: str,
    ) -> None:
        await context.emit_event(
            event_type="human_input_received",
            node_id=node.id,
            node_type="human",
            data={"request_id": request_id, "source": source},
        )


HumanInTheLoopExecutor = HumanNodeExecutor


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

_orch_log = logging.getLogger(__name__ + ".orchestrator")


class OrchestratorExecutor:
    """LLM-driven orchestrator that dynamically dispatches work to teams.

    The orchestrator LLM is called reactively: on startup it receives the
    initial inputs and available teams, dispatches work via tool calls
    (``dispatch_to_team``), waits for team-completion events, then gets
    called again with the results.  It halts via ``halt_orchestrator``.

    When neither ``orchestrator_prompt`` nor ``orchestrator_model`` is set,
    the executor falls back to static fan-out (spawn all teams, wait for
    completion) — preserving backward compatibility.
    """

    TOOL_SCHEMAS: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "dispatch_to_team",
                "description": (
                    "Start a team's sub-graph with the given inputs.  "
                    "A team that is already running cannot be dispatched "
                    "again until it completes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "team_name": {
                            "type": "string",
                            "description": "Name of the team to dispatch work to.",
                        },
                        "inputs": {
                            "type": "object",
                            "description": "Input data to pass to the team sub-graph.",
                        },
                    },
                    "required": ["team_name", "inputs"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "halt_orchestrator",
                "description": (
                    "Stop the orchestrator and return a final aggregated "
                    "result.  Call this when the overall task is complete."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why the orchestrator is stopping.",
                        },
                        "final_result": {
                            "type": "object",
                            "description": "The final aggregated output.",
                        },
                    },
                    "required": ["reason", "final_result"],
                },
            },
        },
    ]

    # ---------------------------------------------------------------
    # Public entry point
    # ---------------------------------------------------------------

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

        if not node.orchestrator_prompt or node.orchestrator_model is None:
            return await self._execute_static_fanout(node, inputs, context)
        return await self._execute_llm_driven(node, inputs, context)

    # ---------------------------------------------------------------
    # LLM-driven orchestration (Plan 16-5)
    # ---------------------------------------------------------------

    async def _execute_llm_driven(
        self,
        node: OrchestratorNode,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        event_queue: asyncio.Queue = asyncio.Queue()
        original_callback = context._event_callback

        async def _routing_cb(event: Any) -> None:
            if original_callback:
                await original_callback(event)
            await event_queue.put(event)

        context._event_callback = _routing_cb

        team_names_list = list(node.teams.keys())
        orchestrator_messages: list[dict[str, Any]] = []
        if node.orchestrator_prompt:
            orchestrator_messages.append(
                {"role": "system", "content": node.orchestrator_prompt}
            )
        orchestrator_messages.append({
            "role": "user",
            "content": (
                f"Available teams: {', '.join(team_names_list)}\n\n"
                f"Initial inputs:\n{json.dumps(inputs, indent=2, default=str)}\n\n"
                "Dispatch work to teams using the dispatch_to_team tool. "
                "When all work is complete, call halt_orchestrator with "
                "the final result."
            ),
        })

        team_tasks: dict[str, asyncio.Task] = {}
        team_results: dict[str, Any] = {}
        team_status: dict[str, str] = {}
        orchestrator_log: list[dict] = []

        model, orch_tier_params = await self._resolve_model(node, context)

        iteration = 0
        llm_calls = 0
        halted = False
        halt_reason = ""
        halt_result: dict[str, Any] = {}
        timeout = node.timeout_seconds
        start_time = asyncio.get_event_loop().time()

        try:
            while (
                not halted
                and iteration < node.max_iterations
                and llm_calls < node.max_llm_calls
            ):
                iteration += 1

                if timeout and (asyncio.get_event_loop().time() - start_time) > timeout:
                    orchestrator_log.append({"iteration": iteration, "action": "timeout"})
                    break

                try:
                    llm_result = await self._call_orchestrator_llm(
                        context, model, orchestrator_messages,
                        tier_params=orch_tier_params,
                    )
                except Exception as exc:
                    _orch_log.warning("Orchestrator LLM call failed: %s", exc)
                    orchestrator_log.append({
                        "iteration": iteration, "action": "llm_error", "error": str(exc),
                    })
                    break

                llm_calls += 1
                _orch_log.debug("LLM call #%d, iteration %d", llm_calls, iteration)

                orchestrator_messages.append(
                    {"role": "assistant", "content": llm_result.text or ""}
                )
                orchestrator_log.append({
                    "iteration": iteration,
                    "llm_call": llm_calls,
                    "text": llm_result.text or "",
                    "tool_calls": llm_result.tool_calls,
                })

                if not llm_result.tool_calls:
                    orchestrator_messages.append({
                        "role": "user",
                        "content": (
                            "You must use tool calls to interact. "
                            "Use dispatch_to_team to send work or "
                            "halt_orchestrator to finish."
                        ),
                    })
                    continue

                dispatched_this_round = 0
                error_feedback = False

                for tc in llm_result.tool_calls:
                    fn_name = tc.get("function", {}).get(
                        "name", tc.get("name", ""),
                    )
                    raw_args = tc.get("function", {}).get(
                        "arguments", tc.get("arguments", "{}"),
                    )
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except (json.JSONDecodeError, TypeError):
                        args = {}

                    if fn_name == "halt_orchestrator":
                        halted = True
                        halt_reason = args.get("reason", "")
                        halt_result = args.get("final_result", {})
                        if not isinstance(halt_result, dict):
                            halt_result = {"value": halt_result}
                        orchestrator_log.append({
                            "iteration": iteration,
                            "action": "halt",
                            "reason": halt_reason,
                        })
                        break

                    if fn_name == "dispatch_to_team":
                        t_name = args.get("team_name", "")
                        t_inputs = args.get("inputs", {})

                        if t_name not in node.teams:
                            orchestrator_messages.append({
                                "role": "user",
                                "content": (
                                    f"Error: unknown team '{t_name}'. "
                                    f"Available: {', '.join(team_names_list)}"
                                ),
                            })
                            error_feedback = True
                            continue

                        if t_name in team_tasks and not team_tasks[t_name].done():
                            orchestrator_messages.append({
                                "role": "user",
                                "content": (
                                    f"Error: team '{t_name}' is already running. "
                                    "Wait for it to complete before "
                                    "re-dispatching."
                                ),
                            })
                            error_feedback = True
                            continue

                        merged = self._build_team_inputs(node, inputs, t_name)
                        merged.update(t_inputs)

                        team_status[t_name] = "running"
                        task = asyncio.create_task(
                            self._dispatch_team(node, context, t_name, merged)
                        )
                        team_tasks[t_name] = task

                        def _on_done(t: asyncio.Task, tn: str = t_name) -> None:
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

                        await context.emit_event(
                            event_type="parallel_branch_started",
                            node_id=node.id,
                            node_type="orchestrator",
                            data={
                                "branch_key": self._team_dispatch_ref(node, t_name),
                                "team_name": t_name,
                                "dispatch_mode": self._team_dispatch_mode(node, t_name),
                            },
                        )
                        orchestrator_log.append({
                            "iteration": iteration,
                            "action": "dispatch",
                            "team": t_name,
                            "inputs": t_inputs,
                        })
                        dispatched_this_round += 1

                if halted:
                    break

                if error_feedback and dispatched_this_round == 0:
                    continue

                running = [
                    (tn, team_tasks[tn])
                    for tn in team_tasks
                    if not team_tasks[tn].done()
                ]

                if not running:
                    if team_results:
                        summary = self._format_all_results(
                            team_results, team_status,
                        )
                        orchestrator_messages.append(
                            {"role": "user", "content": summary},
                        )
                        continue
                    break

                remaining_timeout = None
                if timeout:
                    remaining_timeout = max(
                        0,
                        timeout - (asyncio.get_event_loop().time() - start_time),
                    )
                    if remaining_timeout == 0:
                        orchestrator_log.append({
                            "iteration": iteration,
                            "action": "timeout_while_waiting",
                        })
                        break

                done_set, _ = await asyncio.wait(
                    [t for _, t in running],
                    timeout=remaining_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done_set and timeout:
                    orchestrator_log.append({
                        "iteration": iteration,
                        "action": "timeout_while_waiting",
                    })
                    break

                newly_completed: list[str] = []
                for tn, t in running:
                    if t.done():
                        newly_completed.append(tn)
                        await context.emit_event(
                            event_type="parallel_branch_completed",
                            node_id=node.id,
                            node_type="orchestrator",
                            data={
                                "team_name": tn,
                                "status": team_status.get(tn, "unknown"),
                            },
                        )

                if newly_completed:
                    event_summary = self._format_completion_events(
                        newly_completed, team_results, team_status,
                    )
                    orchestrator_messages.append(
                        {"role": "user", "content": event_summary},
                    )

                self._drain_events(event_queue, orchestrator_log, iteration)

        finally:
            context._event_callback = original_callback

        for t in team_tasks.values():
            if not t.done():
                t.cancel()
        await self._collect_remaining(team_tasks, team_results, team_status)

        await context.emit_event(
            event_type="parallel_fan_in_completed",
            node_id=node.id,
            node_type="orchestrator",
            data={
                "team_count": len(node.teams),
                "completed": sum(
                    1 for s in team_status.values() if s == "completed"
                ),
                "failed": sum(
                    1 for s in team_status.values() if s == "failed"
                ),
            },
        )

        exceeded = (
            llm_calls >= node.max_llm_calls
            or iteration >= node.max_iterations
        )
        final_outputs: dict[str, Any] = {
            "results": halt_result if halted else team_results,
            "orchestrator_log": orchestrator_log,
            "team_status": dict(team_status),
        }
        if halted:
            final_outputs["halt_reason"] = halt_reason
            final_outputs["team_results"] = team_results

        all_failed = bool(team_status) and all(
            s == "failed" for s in team_status.values()
        )
        no_work_dispatched = not team_status

        status = NodeStatus.COMPLETED
        error_msg: str | None = None

        if halted:
            status = NodeStatus.COMPLETED
        elif all_failed:
            status = NodeStatus.FAILED
            error_msg = "All teams failed"
        elif exceeded and no_work_dispatched:
            status = NodeStatus.FAILED
            error_msg = (
                f"Safety bound reached without dispatching any work: "
                f"{llm_calls} LLM calls, {iteration} iterations"
            )
        elif exceeded:
            error_msg = (
                f"Safety bound reached: {llm_calls} LLM calls, "
                f"{iteration} iterations"
            )

        return NodeResult(
            outputs=final_outputs,
            status=status,
            error=error_msg,
            metadata={
                "team_count": len(node.teams),
                "iterations": iteration,
                "llm_calls": llm_calls,
                "halted": halted,
                "team_status": dict(team_status),
            },
        )

    # ---------------------------------------------------------------
    # Static fan-out fallback (backward compat, no LLM)
    # ---------------------------------------------------------------

    async def _execute_static_fanout(
        self,
        node: OrchestratorNode,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        event_queue: asyncio.Queue = asyncio.Queue()
        original_callback = context._event_callback

        async def _routing_cb(event: Any) -> None:
            if original_callback:
                await original_callback(event)
            await event_queue.put(event)

        context._event_callback = _routing_cb

        team_tasks: dict[str, asyncio.Task] = {}
        team_results: dict[str, Any] = {}
        team_status: dict[str, str] = {}

        for team_name in node.teams:
            team_status[team_name] = "running"
            task = asyncio.create_task(
                self._dispatch_team(
                    node,
                    context,
                    team_name,
                    self._build_team_inputs(node, inputs, team_name),
                )
            )
            team_tasks[team_name] = task
            await context.emit_event(
                event_type="parallel_branch_started",
                node_id=node.id,
                node_type="orchestrator",
                data={
                    "branch_key": self._team_dispatch_ref(node, team_name),
                    "team_name": team_name,
                    "dispatch_mode": self._team_dispatch_mode(node, team_name),
                },
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
                        events_batch.append(event_queue.get_nowait())
                except asyncio.QueueEmpty:
                    pass

                if events_batch:
                    for event in events_batch:
                        evt_type = event.event_type
                        evt_str = (
                            evt_type.value
                            if hasattr(evt_type, "value")
                            else str(evt_type)
                        )
                        orchestrator_log.append({
                            "iteration": iteration,
                            "event_type": evt_str,
                            "node_id": event.node_id,
                            "data": event.data,
                        })
                else:
                    await asyncio.sleep(0.05)
        finally:
            context._event_callback = original_callback

        await self._collect_remaining(team_tasks, team_results, team_status)

        await context.emit_event(
            event_type="parallel_fan_in_completed",
            node_id=node.id,
            node_type="orchestrator",
            data={
                "team_count": len(node.teams),
                "completed": sum(
                    1 for s in team_status.values() if s == "completed"
                ),
                "failed": sum(
                    1 for s in team_status.values() if s == "failed"
                ),
            },
        )

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

    # ---------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------

    @staticmethod
    async def _resolve_model(
        node: OrchestratorNode, context: ExecutionContext,
    ) -> tuple[str, dict[str, Any]]:
        model = node.orchestrator_model or context.config.llm_default_model
        tier_params: dict[str, Any] = {}
        if context.model_selector is not None:
            effective_policy = context.model_selector.resolve_effective_policy(
                node, context.config,
            )
            if effective_policy is not None:
                select_result = await context.model_selector.select(
                    effective_policy, node, context,
                )
                if select_result.model:
                    model = select_result.model
                    tier_params = select_result.tier_params
        return model, tier_params

    @staticmethod
    def _build_team_inputs(
        node: OrchestratorNode,
        outer_inputs: dict[str, Any],
        team_name: str,
    ) -> dict[str, Any]:
        inner: dict[str, Any] = {}
        for outer_port, inner_port in node.input_mappings.items():
            if outer_port in outer_inputs:
                inner[inner_port] = outer_inputs[outer_port]
        overrides = node.team_inputs.get(team_name, {})
        inner.update(overrides)
        return inner

    @staticmethod
    def _team_dispatch_mode(node: OrchestratorNode, team_name: str) -> str:
        spec = node.team_expansions.get(team_name)
        return spec.mode if spec is not None else "sub_graph"

    @staticmethod
    def _team_dispatch_ref(node: OrchestratorNode, team_name: str) -> str:
        spec = node.team_expansions.get(team_name)
        return spec.ref if spec is not None else node.teams[team_name]

    async def _dispatch_team(
        self,
        node: OrchestratorNode,
        context: ExecutionContext,
        team_name: str,
        team_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        spec = node.team_expansions.get(team_name)
        if spec is None:
            return await context.run_subgraph(
                node.teams[team_name],
                team_inputs,
                parent_node_id=node.id,
            )

        envelope = await context.run_child_workflow(
            spec,
            team_inputs,
            parent_node_id=node.id,
            source="engine",
            boundary_contract=spec.boundary_contract,
        )
        if isinstance(envelope, BaseModel):
            payload = envelope.model_dump()
            outputs = payload.get("outputs", {})
            return outputs if isinstance(outputs, dict) else {"result": outputs}
        outputs = getattr(envelope, "outputs", None)
        if isinstance(outputs, dict):
            return outputs
        if isinstance(envelope, dict):
            child_outputs = envelope.get("outputs")
            if isinstance(child_outputs, dict):
                return child_outputs
            return envelope
        return {"result": envelope}

    @staticmethod
    async def _call_orchestrator_llm(
        context: ExecutionContext,
        model: str,
        messages: list[dict[str, Any]],
        tier_params: dict[str, Any] | None = None,
    ) -> CompletionResult:
        if context.provider_registry is not None:
            provider = context.provider_registry.resolve(model)
            effective_temp = 0.0
            extra_kwargs: dict[str, Any] = {"tools": OrchestratorExecutor.TOOL_SCHEMAS}
            if tier_params:
                effective_temp = tier_params.get("temperature", effective_temp)
                for k, v in tier_params.items():
                    if k not in ("temperature", "max_tokens"):
                        extra_kwargs[k] = v
            async with context.llm_slot():
                return await provider.complete(
                    messages=messages,
                    model=model,
                    temperature=effective_temp,
                    **extra_kwargs,
                )

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )
        async with context.llm_slot():
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.0,
                tools=OrchestratorExecutor.TOOL_SCHEMAS,
            )
        msg = resp.choices[0].message
        tool_calls_raw = None
        if msg.tool_calls:
            tool_calls_raw = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        return CompletionResult(
            text=msg.content or "",
            model=model,
            tool_calls=tool_calls_raw,
        )

    @staticmethod
    def _format_completion_events(
        completed_teams: list[str],
        team_results: dict[str, Any],
        team_status: dict[str, str],
    ) -> str:
        parts: list[str] = []
        for tn in completed_teams:
            st = team_status.get(tn, "unknown")
            result = team_results.get(tn, {})
            result_str = json.dumps(result, indent=2, default=str)
            parts.append(f"Team '{tn}' {st}.\nResult:\n{result_str}")
        return "--- Team Events ---\n" + "\n\n".join(parts)

    @staticmethod
    def _format_all_results(
        team_results: dict[str, Any],
        team_status: dict[str, str],
    ) -> str:
        parts: list[str] = []
        for tn, result in team_results.items():
            st = team_status.get(tn, "unknown")
            result_str = json.dumps(result, indent=2, default=str)
            parts.append(f"Team '{tn}' ({st}):\n{result_str}")
        return (
            "All dispatched teams have completed. Results:\n\n"
            + "\n\n".join(parts)
            + "\n\nCall halt_orchestrator to finish, or dispatch more work."
        )

    @staticmethod
    def _drain_events(
        event_queue: asyncio.Queue,
        orchestrator_log: list[dict],
        iteration: int,
    ) -> None:
        while True:
            try:
                event = event_queue.get_nowait()
                evt_type = event.event_type
                evt_str = (
                    evt_type.value
                    if hasattr(evt_type, "value")
                    else str(evt_type)
                )
                orchestrator_log.append({
                    "iteration": iteration,
                    "event_type": evt_str,
                    "node_id": event.node_id,
                    "data": event.data,
                })
            except asyncio.QueueEmpty:
                break

    @staticmethod
    async def _collect_remaining(
        team_tasks: dict[str, asyncio.Task],
        team_results: dict[str, Any],
        team_status: dict[str, str],
    ) -> None:
        pending = [t for t in team_tasks.values() if not t.done()]
        if pending:
            _, still_pending = await asyncio.wait(pending, timeout=2.0)
            for t in still_pending:
                t.cancel()

        for team_name, task in team_tasks.items():
            if team_name not in team_results:
                if task.done() and not task.cancelled():
                    exc = task.exception()
                    if exc:
                        team_results[team_name] = {"error": str(exc)}
                    else:
                        team_results[team_name] = task.result()
                else:
                    team_results[team_name] = {"error": "cancelled or not completed"}


# ---------------------------------------------------------------------------
# Vote / Ensemble
# ---------------------------------------------------------------------------


class VoteExecutor:
    """Runs the same prompt through multiple LLM instances and picks the best.

    Supports same-model voting (single candidate, N votes) and cross-model
    ensemble (multiple candidates). Strategy selects the winner.
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, VoteNode)

        prompt = self._render_prompt(node.prompt_template, inputs)
        config = node.vote_config or VoteConfig()

        await context.emit_event(
            event_type="vote_started",
            node_id=node.id,
            node_type="vote",
            data={
                "num_votes": node.num_votes,
                "candidates": node.candidates,
                "strategy": node.vote_strategy,
            },
        )

        candidates = self._collect_candidates(node)
        semaphore = asyncio.Semaphore(node.parallelism)

        async def _call(index: int, model: str) -> dict[str, Any] | None:
            async with context.node_slot():
                async with semaphore:
                    try:
                        result = await self._call_llm(context, model, prompt, node)
                        await context.emit_event(
                            event_type="vote_cast",
                            node_id=node.id,
                            node_type="vote",
                            data={"index": index, "model": model},
                        )
                        cost = self._compute_cost(model, result.usage)
                        return {
                            "index": index,
                            "model": model,
                            "text": result.text,
                            "usage": result.usage,
                            "cost": cost,
                        }
                    except Exception as exc:
                        logger.warning("Vote %d (%s) failed: %s", index, model, exc)
                        return None

        tasks = [_call(i, m) for i, m in candidates]
        timeout = getattr(node, "timeout_seconds", None)
        try:
            if timeout is not None:
                raw = await asyncio.wait_for(
                    asyncio.gather(*tasks), timeout=timeout,
                )
            else:
                raw = await asyncio.gather(*tasks)
        except asyncio.TimeoutError:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Voting timed out after {timeout}s",
            )

        votes: list[dict[str, Any]] = [v for v in raw if v is not None]

        import math
        min_required = math.ceil(node.num_votes / 2)
        if len(votes) < min_required:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Only {len(votes)}/{node.num_votes} votes succeeded (need {min_required})",
            )

        winner, consensus = await self._apply_strategy(
            node.vote_strategy, votes, config, context, node,
        )

        total_cost = sum(v.get("cost", 0.0) or 0.0 for v in votes)

        all_votes = [
            {"model": v["model"], "text": v["text"], "score": v.get("score")}
            for v in votes
        ]

        await context.emit_event(
            event_type="vote_completed",
            node_id=node.id,
            node_type="vote",
            data={
                "winner": winner["model"],
                "consensus": consensus,
                "cost": total_cost,
            },
        )

        return NodeResult(
            outputs={
                "winner": winner["text"],
                "winner_model": winner["model"],
                "winner_index": winner["index"],
                "all_votes": all_votes,
                "consensus_reached": consensus,
                "vote_count": len(votes),
                "total_cost": total_cost,
                "strategy_used": node.vote_strategy,
            },
            status=NodeStatus.COMPLETED,
            metadata={
                "num_votes": node.num_votes,
                "succeeded": len(votes),
                "strategy": node.vote_strategy,
                "consensus": consensus,
                "total_cost": total_cost,
            },
        )

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _render_prompt(template: str, inputs: dict[str, Any]) -> str:
        """Replace ``{key}`` placeholders with input values."""
        result = template
        for key, value in inputs.items():
            placeholder = "{" + key + "}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))
        return result

    @staticmethod
    def _collect_candidates(node: VoteNode) -> list[tuple[int, str]]:
        """Build (index, model) pairs via round-robin over candidates."""
        models = node.candidates
        return [(i, models[i % len(models)]) for i in range(node.num_votes)]

    @staticmethod
    async def _call_llm(
        context: ExecutionContext,
        model: str,
        prompt: str,
        node: VoteNode,
    ):
        messages: list[dict[str, str]] = []
        if node.system_prompt:
            messages.append({"role": "system", "content": node.system_prompt})
        messages.append({"role": "user", "content": prompt})

        if context.provider_registry is not None:
            provider = context.provider_registry.resolve(model)
            async with context.llm_slot():
                return await provider.complete(
                    messages=messages, model=model, temperature=node.temperature,
                )

        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )
        async with context.llm_slot():
            resp = await client.chat.completions.create(
                model=model, messages=messages, temperature=node.temperature,
            )
        from dan.providers import CompletionResult
        text = resp.choices[0].message.content or ""
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
            "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
        }
        return CompletionResult(text=text, usage=usage, model=model)

    @staticmethod
    def _compute_cost(model: str, usage: dict[str, int] | None) -> float:
        if not usage:
            return 0.0
        from dan.providers.costs import estimate_cost
        cost = estimate_cost(
            model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )
        return cost if cost is not None else 0.0

    async def _apply_strategy(
        self,
        strategy: str,
        votes: list[dict[str, Any]],
        config: VoteConfig,
        context: ExecutionContext,
        node: VoteNode,
    ) -> tuple[dict[str, Any], bool]:
        """Apply the voting strategy. Returns (winner_vote_dict, consensus_reached)."""
        if strategy == "majority":
            return self._strategy_majority(votes)
        elif strategy == "weighted":
            return self._strategy_weighted(votes, config)
        elif strategy in ("best_of_n", "judge"):
            return await self._strategy_judge(votes, config, context, node)
        elif strategy == "unanimous":
            return self._strategy_unanimous(votes, config)
        else:
            return self._strategy_majority(votes)

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize whitespace for comparison."""
        return " ".join(text.split())

    def _strategy_majority(
        self, votes: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], bool]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for v in votes:
            key = self._normalize(v["text"])
            buckets.setdefault(key, []).append(v)

        best_key = max(buckets, key=lambda k: len(buckets[k]))
        best_group = buckets[best_key]
        consensus = len(best_group) == len(votes)
        winner = best_group[0]
        return winner, consensus

    def _strategy_weighted(
        self, votes: list[dict[str, Any]], config: VoteConfig
    ) -> tuple[dict[str, Any], bool]:
        expr = config.quality_metric or "len(output)"
        best: dict[str, Any] | None = None
        best_score = float("-inf")

        for v in votes:
            variables = {"output": v["text"], "model": v["model"], "cost": v.get("cost", 0)}
            try:
                score = float(evaluate_expression(expr, variables))
            except Exception:
                score = 0.0
            v["score"] = score
            if score > best_score:
                best_score = score
                best = v

        assert best is not None
        return best, False

    async def _strategy_judge(
        self,
        votes: list[dict[str, Any]],
        config: VoteConfig,
        context: ExecutionContext,
        node: VoteNode,
    ) -> tuple[dict[str, Any], bool]:
        judge_model = config.judge_model or (node.candidates[0] if node.candidates else context.config.llm_default_model)

        answers_text = "\n\n".join(
            f"--- Answer {v['index']} (model: {v['model']}) ---\n{v['text']}"
            for v in votes
        )
        judge_prompt = config.judge_prompt or (
            "Given these answers to the same question, which is best? "
            "Return ONLY the answer number (0-indexed integer)."
        )
        full_prompt = f"{judge_prompt}\n\n{answers_text}"

        messages = [{"role": "user", "content": full_prompt}]

        try:
            if context.provider_registry is not None:
                provider = context.provider_registry.resolve(judge_model)
                async with context.llm_slot():
                    result = await provider.complete(
                        messages=messages, model=judge_model, temperature=0.0,
                    )
                judge_text = result.text.strip()
            else:
                judge_text = "0"

            chosen_idx = int("".join(c for c in judge_text if c.isdigit()) or "0")
            for v in votes:
                if v["index"] == chosen_idx:
                    return v, False
            return votes[0], False
        except Exception:
            return votes[0], False

    def _strategy_unanimous(
        self, votes: list[dict[str, Any]], config: VoteConfig
    ) -> tuple[dict[str, Any], bool]:
        threshold = config.unanimity_threshold
        normalized = [self._normalize(v["text"]) for v in votes]

        from collections import Counter
        counts = Counter(normalized)
        most_common_text, most_common_count = counts.most_common(1)[0]
        agreement_ratio = most_common_count / len(votes)

        consensus = agreement_ratio >= threshold

        for v in votes:
            if self._normalize(v["text"]) == most_common_text:
                return v, consensus

        return votes[0], consensus


# ---------------------------------------------------------------------------
# AgentTeam — group-chat style multi-agent coordination (Plan 16-1)
# ---------------------------------------------------------------------------

_MENTION_RE = re.compile(r"@(\w+)")


class AgentTeamExecutor:
    """Runs a group-chat style conversation among multiple agents.

    Each agent is a sub-graph.  The executor manages turn order (via one of
    four strategies), parses ``@agent_name`` mentions for routing, handles
    explicit handoffs, and checks completion conditions each turn.
    """

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, AgentTeamNode)

        if len(node.agents) < 2:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error="agent_team requires at least 2 agents",
            )

        agent_names = list(node.agents.keys())
        conversation = TeamConversation()
        conv_key = f"__team__{node.id}__conversation"
        self._ctx_write(context, conv_key, conversation.model_dump())

        agent_contributions: dict[str, list[str]] = {n: [] for n in agent_names}
        consult_return_stack: list[str] = []
        current_agent = agent_names[0]
        agent_index = 0
        completed_via_condition = False
        error_turns = 0
        total_turns_executed = 0

        for turn in range(node.max_turns):
            conversation.turn_count = turn + 1
            conversation.active_agent = current_agent

            await context.emit_event(
                event_type="team_turn_started",
                node_id=node.id,
                node_type="agent_team",
                data={"agent_name": current_agent, "turn_number": turn},
            )

            agent_in = self._build_agent_inputs(
                node, current_agent, conversation, agent_names, inputs,
            )
            sub_graph_key = node.agents[current_agent]

            total_turns_executed += 1
            try:
                agent_output = await context.run_subgraph(
                    sub_graph_key, agent_in, parent_node_id=node.id,
                )
            except Exception as exc:
                error_turns += 1
                msg = TeamMessage(
                    sender=current_agent,
                    content=f"[Error: {exc}]",
                    turn_number=turn,
                )
                conversation.messages.append(msg)
                current_agent, agent_index = self._advance_round_robin(
                    agent_names, agent_index,
                )
                continue

            content = self._extract_content(agent_output)
            mentions = [
                m for m in self._parse_mentions(content, agent_names)
                if m != current_agent
            ]
            handoff = self._parse_handoff(agent_output, current_agent, agent_names)
            if handoff and node.handoff_policy == "moderator_only":
                handoff = None

            msg = TeamMessage(
                sender=current_agent,
                recipients=mentions if mentions else ["all"],
                content=content,
                message_type="handoff" if handoff else "message",
                turn_number=turn,
            )
            conversation.messages.append(msg)
            agent_contributions[current_agent].append(content)

            await context.emit_event(
                event_type="team_turn_completed",
                node_id=node.id,
                node_type="agent_team",
                data={
                    "agent_name": current_agent,
                    "turn_number": turn,
                    "message_summary": content[:200],
                },
            )

            if getattr(context, "state_store", None) is not None:
                try:
                    turn_state = TeamTurnState(
                        turn_number=turn,
                        agent_id=current_agent,
                        status="completed",
                        message_preview=content[:200],
                        handoff_to=handoff.target_agent if handoff else None,
                    )
                    await context.state_store.write(
                        context.state.run_id,
                        f"team:{node.id}:turn:{turn}",
                        turn_state,
                    )
                    await context.emit_event(
                        event_type="state_externalized",
                        node_id=node.id,
                        node_type="agent_team",
                        data={"scope": context.state.run_id, "keys_written": [f"team:{node.id}:turn:{turn}"]},
                    )
                except Exception:
                    logger.debug(
                        "Failed to externalize state for %s",
                        node.id, exc_info=True,
                    )

            if handoff:
                conversation.handoff_log.append(handoff)
                await context.emit_event(
                    event_type="team_handoff",
                    node_id=node.id,
                    node_type="agent_team",
                    data={
                        "source": handoff.source_agent,
                        "target": handoff.target_agent,
                        "reason": handoff.reason,
                    },
                )
                if handoff.handoff_type == "consult":
                    consult_return_stack.append(current_agent)
                current_agent = handoff.target_agent
                agent_index = agent_names.index(current_agent)
            else:
                current_agent, agent_index = await self._select_next_agent(
                    node, agent_names, agent_index, current_agent,
                    conversation, context, mentions, consult_return_stack,
                )

            self._ctx_write(context, conv_key, conversation.model_dump())

            await self._apply_turn_compaction(node, conversation, context, turn)

            if self._check_completion(node, conversation, agent_names):
                completed_via_condition = True
                break

        consensus_reached = (
            node.completion_condition == "consensus" and completed_via_condition
        )

        await context.emit_event(
            event_type="team_completed",
            node_id=node.id,
            node_type="agent_team",
            data={
                "consensus_reached": consensus_reached,
                "total_turns": conversation.turn_count,
            },
        )

        all_errored = total_turns_executed > 0 and error_turns == total_turns_executed
        final = self._build_final_result(
            conversation, agent_contributions, consensus_reached,
        )
        return NodeResult(
            outputs=final,
            status=NodeStatus.FAILED if all_errored else NodeStatus.COMPLETED,
            error="All agent turns failed" if all_errored else None,
            metadata={
                "total_turns": conversation.turn_count,
                "consensus_reached": consensus_reached,
                "agent_count": len(agent_names),
                "error_turns": error_turns,
            },
        )

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _ctx_write(context: ExecutionContext, key: str, value: Any) -> None:
        """Write to shared context, bypassing declaration check."""
        try:
            context.shared_context.write(key, value)
        except KeyError:
            context.shared_context._store[key] = value

    def _build_agent_inputs(
        self,
        node: AgentTeamNode,
        agent_name: str,
        conversation: TeamConversation,
        agent_names: list[str],
        outer_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        inner: dict[str, Any] = {}
        for outer_port, inner_port in node.input_mappings.items():
            if outer_port in outer_inputs:
                inner[inner_port] = outer_inputs[outer_port]

        overrides = node.agent_inputs.get(agent_name, {})
        inner.update(overrides)

        history = [m.model_dump() for m in conversation.messages]
        history = self._compact_history(node, history)
        inner["conversation_history"] = history
        inner["current_turn"] = conversation.turn_count
        inner["team_roster"] = agent_names

        if conversation.handoff_log:
            last_ho = conversation.handoff_log[-1]
            if last_ho.target_agent == agent_name:
                inner["handoff_context"] = last_ho.model_dump()

        return inner

    @staticmethod
    def _compact_history(
        node: AgentTeamNode, history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply CompactionRule to a copy of the conversation history."""
        rule = node.compaction_rule
        if rule is None or rule.strategy == CompactionStrategy.NONE:
            return history
        if not history:
            return history
        if rule.strategy == CompactionStrategy.KEEP_LAST:
            return history[-1:]
        if rule.strategy == CompactionStrategy.SLIDING_WINDOW:
            window = rule.window_size or (len(node.agents) * 3)
            return history[-window:]
        if rule.strategy == CompactionStrategy.DIFF_BASED:
            if len(history) > 2:
                return [history[0], history[-1]]
        return history

    @staticmethod
    async def _apply_turn_compaction(
        node: AgentTeamNode,
        conversation: TeamConversation,
        context: ExecutionContext,
        turn: int,
    ) -> None:
        """Persist evicted messages and emit compaction event after each turn."""
        rule = node.compaction_rule
        if rule is None or rule.strategy == CompactionStrategy.NONE:
            return
        msgs = [m.model_dump() for m in conversation.messages]
        if not msgs:
            return

        compactor = LoopCompactor(
            rule,
            memory=context.short_term_memory,
            state_store=getattr(context, "state_store", None),
        )
        compacted, emit_data = await compactor.compact(
            msgs, loop_node_id=node.id, iteration=turn,
        )
        has_structural_changes = any(
            d.get("__summary__") or d.get("__unchanged__") for d in compacted
        )
        if len(compacted) < len(msgs) or has_structural_changes:
            rebuilt = []
            for d in compacted:
                if d.get("__summary__"):
                    rebuilt.append(TeamMessage(
                        sender="system",
                        content=d.get("description", "Previous messages summarized"),
                        message_type="message",
                        metadata={"__summary__": True, "covered": d.get("covered_iterations", [])},
                    ))
                elif d.get("__unchanged__"):
                    continue
                else:
                    rebuilt.append(TeamMessage(**d))
            conversation.messages = rebuilt
            await context.emit_event(
                event_type="loop_compaction_applied",
                node_id=node.id,
                node_type="agent_team",
                data={"turn": turn, **emit_data},
            )

    @staticmethod
    def _extract_content(agent_output: dict[str, Any]) -> str:
        for key in ("result", "response", "content", "output", "text"):
            if key in agent_output and isinstance(agent_output[key], str):
                return agent_output[key]
        return json.dumps(agent_output, default=str)

    @staticmethod
    def _parse_mentions(text: str, agent_names: list[str]) -> list[str]:
        """Extract @agent_name mentions from text, filtered to known agents."""
        return [m for m in _MENTION_RE.findall(text) if m in agent_names]

    @staticmethod
    def _parse_handoff(
        output: dict[str, Any],
        current_agent: str,
        agent_names: list[str],
    ) -> HandoffRequest | None:
        handoff_data = output.get("handoff")
        if not handoff_data or not isinstance(handoff_data, dict):
            return None
        target = handoff_data.get("target_agent", "")
        if target not in agent_names or target == current_agent:
            return None
        return HandoffRequest(
            source_agent=current_agent,
            target_agent=target,
            reason=handoff_data.get("reason", ""),
            context=handoff_data.get("context", {}),
            handoff_type=handoff_data.get("handoff_type", "transfer"),
        )

    @staticmethod
    def _advance_round_robin(
        agent_names: list[str], current_index: int,
    ) -> tuple[str, int]:
        idx = (current_index + 1) % len(agent_names)
        return agent_names[idx], idx

    async def _select_next_agent(
        self,
        node: AgentTeamNode,
        agent_names: list[str],
        current_index: int,
        current_agent: str,
        conversation: TeamConversation,
        context: ExecutionContext,
        mentions: list[str],
        consult_return_stack: list[str],
    ) -> tuple[str, int]:
        if consult_return_stack:
            next_name = consult_return_stack.pop()
            return next_name, agent_names.index(next_name)

        if node.turn_strategy == "round_robin":
            return self._advance_round_robin(agent_names, current_index)

        if node.turn_strategy == "sequential":
            idx = current_index + 1
            if idx >= len(agent_names):
                return current_agent, current_index
            return agent_names[idx], idx

        if node.turn_strategy == "moderator":
            next_name = await self._call_moderator(
                node, agent_names, conversation, context,
            )
            if next_name and next_name in agent_names:
                return next_name, agent_names.index(next_name)
            return self._advance_round_robin(agent_names, current_index)

        if node.turn_strategy == "free_form":
            if mentions:
                return mentions[0], agent_names.index(mentions[0])
            if node.moderator_model:
                next_name = await self._call_moderator(
                    node, agent_names, conversation, context,
                )
                if next_name and next_name in agent_names:
                    return next_name, agent_names.index(next_name)
            return self._advance_round_robin(agent_names, current_index)

        return self._advance_round_robin(agent_names, current_index)

    @staticmethod
    async def _call_moderator(
        node: AgentTeamNode,
        agent_names: list[str],
        conversation: TeamConversation,
        context: ExecutionContext,
    ) -> str | None:
        model = node.moderator_model or context.config.llm_default_model
        recent = conversation.messages[-10:]
        conv_text = "\n".join(
            f"{m.sender}: {m.content[:200]}" for m in recent
        )
        prompt = (
            f"{node.moderator_prompt}\n\n"
            f"Team members: {', '.join(agent_names)}\n\n"
            f"Recent conversation:\n{conv_text}\n\n"
            f"Who should speak next? Respond with ONLY one agent name from: {agent_names}"
        )
        try:
            result = await RouterExecutor._call_router_llm(context, model, prompt)
            result = result.strip()
            if result in agent_names:
                return result
            for name in agent_names:
                if name.lower() in result.lower():
                    return name
        except Exception:
            pass
        return None

    @staticmethod
    def _check_completion(
        node: AgentTeamNode,
        conversation: TeamConversation,
        agent_names: list[str],
    ) -> bool:
        if node.turn_strategy == "sequential":
            respondents = {m.sender for m in conversation.messages}
            if respondents >= set(agent_names):
                return True

        if node.completion_condition == "max_turns":
            return conversation.turn_count >= node.max_turns

        if node.completion_condition == "all_responded":
            respondents = {m.sender for m in conversation.messages}
            return respondents >= set(agent_names)

        if node.completion_condition == "moderator_halt":
            if conversation.messages:
                return "HALT" in conversation.messages[-1].content.upper()
            return False

        if node.completion_condition == "consensus":
            if conversation.messages:
                return "CONSENSUS" in conversation.messages[-1].content.upper()
            return False

        return False

    @staticmethod
    def _build_final_result(
        conversation: TeamConversation,
        agent_contributions: dict[str, list[str]],
        consensus_reached: bool,
    ) -> dict[str, Any]:
        final_answer = ""
        if conversation.messages:
            final_answer = conversation.messages[-1].content

        summaries = {
            name: "; ".join(msgs) if msgs else "(no contributions)"
            for name, msgs in agent_contributions.items()
        }

        return {
            "result": final_answer,
            "agent_contributions": summaries,
            "consensus_reached": consensus_reached,
            "total_turns": conversation.turn_count,
            "conversation": [m.model_dump() for m in conversation.messages],
        }
