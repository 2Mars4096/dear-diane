"""Worker executor — thin compatibility layer over existing compute executors."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from dan.engine.conditions import evaluate_reducer
from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.executors.code import CodeExecutor
from dan.executors.control_flow import GateExecutor, HumanNodeExecutor, ReduceExecutor, RouterExecutor, VoteExecutor
from dan.executors.rag import RAGExecutor
from dan.executors.reflection import ReflectionExecutor
from dan.executors.tool import ToolExecutor
from dan.executors.validator import ValidatorExecutor
from dan.models.context import ContextMode, MergeStrategy
from dan.models.legacy import CodeOperator, LLMOperator, ToolOperator
from dan.models.nodes import NodeBase, RetryPolicy
from dan.tools import get_all_tools
from dan.worker.model import (
    AuthorityPolicy,
    ContextBindings,
    ExecutionSemantics,
    LLMHints,
    Worker,
    WorkerAuthority,
    llm_hints_configured,
)

if TYPE_CHECKING:
    from dan.executors.llm import LLMExecutor

_TASK_TIER_ORDER = {
    "micro": 0,
    "routine": 1,
    "reasoning": 2,
    "critical": 3,
}


class ExecutionMode(str, Enum):
    SCRIPT = "script"
    TOOL = "tool"
    LLM_WITH_TOOLS = "llm_with_tools"
    LLM = "llm"
    ROUTER = "router"
    VALIDATE = "validate"
    REFLECT = "reflect"
    RAG = "rag"
    HUMAN = "human"
    VOTE = "vote"
    REDUCE = "reduce"
    GATE = "gate"
    COMPOSITE = "composite"
    ORCHESTRATE = "orchestrate"
    PASSTHROUGH = "passthrough"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except Exception:
            return str(value)
    return str(value)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _worker_fingerprint(node: Worker) -> str:
    payload = node.model_dump(mode="json")
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _scope_allows_key(scope: str, key: str) -> bool:
    if scope == "*":
        return True
    if key == scope:
        return True
    return key.startswith(f"{scope}.")


@dataclass
class EffectiveWorkerConfig:
    instruction: str
    tool_ids: list[str]
    toolset_refs: list[str]
    memory_policy: dict[str, Any]
    model: str | None
    llm_hints: LLMHints | None
    authority_policy: AuthorityPolicy | None
    execution: ExecutionSemantics | None
    provider_policy: dict[str, Any]
    retry_policy: RetryPolicy | None


class WorkerExecutor:
    """Execute Worker nodes by reusing current compute executors where possible."""

    def __init__(
        self,
        *,
        llm_executor: "LLMExecutor | None" = None,
        tool_executor: ToolExecutor | None = None,
        code_executor: CodeExecutor | None = None,
        gate_executor: GateExecutor | None = None,
        router_executor: RouterExecutor | None = None,
        validator_executor: ValidatorExecutor | None = None,
        reflection_executor: ReflectionExecutor | None = None,
        rag_executor: RAGExecutor | None = None,
        human_executor: HumanNodeExecutor | None = None,
        vote_executor: VoteExecutor | None = None,
        reduce_executor: ReduceExecutor | None = None,
    ) -> None:
        if llm_executor is None:
            from dan.executors.llm import LLMExecutor

            llm_executor = LLMExecutor()
        self._llm = llm_executor
        self._tool = tool_executor
        self._code = code_executor or CodeExecutor()
        self._gate = gate_executor or GateExecutor()
        self._router = router_executor or RouterExecutor()
        self._validator = validator_executor or ValidatorExecutor()
        self._reflection = reflection_executor or ReflectionExecutor()
        self._rag = rag_executor or RAGExecutor()
        self._human = human_executor or HumanNodeExecutor()
        self._vote = vote_executor or VoteExecutor()
        self._reduce = reduce_executor or ReduceExecutor()
        self._static_effective_configs: dict[str, EffectiveWorkerConfig] = {}
        self._static_modes: dict[str, tuple[ExecutionMode, ...]] = {}
        self._static_llm_templates: dict[tuple[str, str], LLMOperator] = {}
        self._code_templates: dict[str, CodeOperator] = {}
        self._tool_templates: dict[tuple[str, str], ToolOperator] = {}
        self._llm_templates: dict[str, LLMOperator] = {}
        self._specialized_templates: dict[str, NodeBase] = {}

    async def execute(
        self,
        node: Worker,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        fast_result = await self._try_execute_static_fast_path(node, inputs, context)
        if fast_result is not None:
            return fast_result
        effective = self._resolve_effective_config(node, context)
        memory_scope_error = self._validate_memory_write_scopes(node, effective)
        if memory_scope_error is not None:
            return NodeResult(outputs={}, status=NodeStatus.FAILED, error=memory_scope_error)
        modes = self._detect_modes(node, effective)

        staged_inputs = dict(inputs)
        if ExecutionMode.SCRIPT in modes:
            code_result = await self._run_code(node, staged_inputs, context, effective)
            if code_result.status != NodeStatus.COMPLETED:
                return code_result
            remaining_modes = [mode for mode in modes if mode != ExecutionMode.SCRIPT]
            if not remaining_modes:
                return code_result
            staged_inputs = self._merge_stage_outputs_into_inputs(staged_inputs, code_result.outputs)
            modes = remaining_modes

        primary_mode = modes[0]
        if primary_mode is ExecutionMode.COMPOSITE:
            return await self._run_body_graph(node, staged_inputs, context)
        if primary_mode is ExecutionMode.ORCHESTRATE:
            return await self._run_sub_workers(node, staged_inputs, context, effective)
        if primary_mode in {
            ExecutionMode.ROUTER,
            ExecutionMode.VALIDATE,
            ExecutionMode.REFLECT,
            ExecutionMode.RAG,
            ExecutionMode.HUMAN,
            ExecutionMode.VOTE,
            ExecutionMode.REDUCE,
            ExecutionMode.GATE,
        }:
            if primary_mode in {ExecutionMode.ROUTER, ExecutionMode.REFLECT, ExecutionMode.VOTE}:
                tier_error = self._validate_task_tier_cap(node, effective)
                if tier_error is not None:
                    return NodeResult(outputs={}, status=NodeStatus.FAILED, error=tier_error)
            return await self._run_specialized_legacy(node, staged_inputs, context, effective)
        if primary_mode in {ExecutionMode.LLM, ExecutionMode.LLM_WITH_TOOLS}:
            tier_error = self._validate_task_tier_cap(node, effective)
            if tier_error is not None:
                return NodeResult(
                    outputs={},
                    status=NodeStatus.FAILED,
                    error=tier_error,
                )
            return await self._run_llm(node, staged_inputs, context, effective)
        if primary_mode is ExecutionMode.TOOL:
            return await self._run_direct_tool(node, staged_inputs, context, effective)
        return self._passthrough(node, staged_inputs)

    def _detect_modes(
        self,
        node: Worker,
        effective: EffectiveWorkerConfig,
    ) -> list[ExecutionMode]:
        modes: list[ExecutionMode] = []
        has_llm = bool(effective.model) or llm_hints_configured(effective.llm_hints)
        if node.code:
            modes.append(ExecutionMode.SCRIPT)
        if node.body_graph:
            modes.append(ExecutionMode.COMPOSITE)
        if node.sub_workers:
            modes.append(ExecutionMode.ORCHESTRATE)
        specialized_mode = self._detect_specialized_mode(node)
        if specialized_mode is not None:
            modes.append(specialized_mode)
        elif has_llm:
            if effective.tool_ids:
                modes.append(ExecutionMode.LLM_WITH_TOOLS)
            else:
                modes.append(ExecutionMode.LLM)
        elif effective.tool_ids:
            modes.append(ExecutionMode.TOOL)
        if not modes:
            modes.append(ExecutionMode.PASSTHROUGH)
        return modes

    @staticmethod
    def _detect_specialized_mode(node: Worker) -> ExecutionMode | None:
        metadata = dict(node.metadata or {})
        if node.control_flow is not None:
            return ExecutionMode.GATE
        if node.role == "vote" or any(
            key in metadata
            for key in (
                "vote_candidates",
                "vote_num_votes",
                "vote_prompt_template",
                "vote_strategy",
            )
        ):
            return ExecutionMode.VOTE
        if node.role == "reduce" or "reduce_expression" in metadata:
            return ExecutionMode.REDUCE
        if node.role in {"human", "human_in_the_loop"} or any(
            key in metadata
            for key in (
                "human_prompt",
                "human_render_mode",
                "human_timeout_seconds",
            )
        ):
            return ExecutionMode.HUMAN
        if node.role in {"rag", "retriever"} or "rag_collection" in metadata:
            return ExecutionMode.RAG
        if node.role == "reflection" or any(
            key in metadata
            for key in (
                "reflection_prompt",
                "reflection_model",
                "reflection_source",
            )
        ):
            return ExecutionMode.REFLECT
        if node.role == "validator" or node.validation_rules or "validation_rules" in metadata:
            return ExecutionMode.VALIDATE
        if node.role == "router" or "route_descriptions" in metadata:
            return ExecutionMode.ROUTER
        return None

    @staticmethod
    def _merge_stage_outputs_into_inputs(
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(inputs)
        merged.update(outputs)
        if "result" in outputs:
            merged["input"] = outputs["result"]
        elif outputs and "input" not in merged:
            merged["input"] = next(iter(outputs.values()))
        return merged

    def _resolve_effective_config(
        self,
        node: Worker,
        context: ExecutionContext,
    ) -> EffectiveWorkerConfig:
        catalog = getattr(getattr(context, "graph", None), "worker_resources", {}) or {}
        bindings = node.context
        node_key = _worker_fingerprint(node)

        if not catalog and self._bindings_are_noop(bindings):
            cached = self._static_effective_configs.get(node_key)
            if cached is None:
                local_instruction = (node.instruction or node.persona).strip()
                cached = EffectiveWorkerConfig(
                    instruction=local_instruction,
                    tool_ids=list(node.tool_ids),
                    toolset_refs=[],
                    memory_policy={},
                    model=node.model,
                    llm_hints=node.llm_hints,
                    authority_policy=node.authority_policy,
                    execution=node.execution,
                    provider_policy={},
                    retry_policy=None,
                )
                self._static_effective_configs[node_key] = cached
            return cached

        bindings = bindings or ContextBindings()

        instruction_parts: list[str] = []
        provider_policy: dict[str, Any] = {}
        memory_policy: dict[str, Any] = {}
        retry_policy_data: dict[str, Any] = {}
        tool_ids = list(node.tool_ids)
        resolved_toolset_refs: list[str] = []
        authority_policy = node.authority_policy
        execution = node.execution

        def _resolve(kind: str, ref: str | None) -> Any:
            if not ref:
                return None
            resources = catalog.get(kind) or {}
            return resources.get(ref)

        if bindings.inherit_defaults:
            default_bundle = catalog.get("defaults") or {}
            if default_bundle.get("instruction"):
                instruction_parts.append(str(default_bundle["instruction"]))
            if isinstance(default_bundle.get("provider_policy"), dict):
                provider_policy = _deep_merge(provider_policy, default_bundle["provider_policy"])
            if isinstance(default_bundle.get("memory_policy"), dict):
                memory_policy = _deep_merge(memory_policy, default_bundle["memory_policy"])
            if isinstance(default_bundle.get("retry_policy"), dict):
                retry_policy_data = _deep_merge(retry_policy_data, default_bundle["retry_policy"])
            tool_ids.extend(list(default_bundle.get("tool_ids") or []))

        instruction_profile = _resolve("instruction_profiles", bindings.instruction_profile_ref)
        if isinstance(instruction_profile, dict):
            if instruction_profile.get("instruction"):
                instruction_parts.append(str(instruction_profile["instruction"]))
            tool_ids.extend(list(instruction_profile.get("tool_ids") or []))

        for bundle_ref in bindings.context_bundle_refs:
            bundle = _resolve("context_bundles", bundle_ref)
            if not isinstance(bundle, dict):
                continue
            if bundle.get("instruction"):
                instruction_parts.append(str(bundle["instruction"]))
            if isinstance(bundle.get("provider_policy"), dict):
                provider_policy = _deep_merge(provider_policy, bundle["provider_policy"])
            if isinstance(bundle.get("memory_policy"), dict):
                memory_policy = _deep_merge(memory_policy, bundle["memory_policy"])
            if isinstance(bundle.get("retry_policy"), dict):
                retry_policy_data = _deep_merge(retry_policy_data, bundle["retry_policy"])
            tool_ids.extend(list(bundle.get("tool_ids") or []))

        memory_ref = _resolve("memory_policies", bindings.memory_policy_ref)
        if isinstance(memory_ref, dict):
            memory_policy = _deep_merge(memory_policy, memory_ref)

        provider_ref = _resolve("provider_policies", bindings.provider_policy_ref)
        if isinstance(provider_ref, dict):
            provider_policy = _deep_merge(provider_policy, provider_ref)

        retry_ref = _resolve("retry_policies", bindings.retry_policy_ref)
        if isinstance(retry_ref, dict):
            retry_policy_data = _deep_merge(retry_policy_data, retry_ref)

        for toolset_ref in bindings.toolset_refs:
            toolset = _resolve("toolsets", toolset_ref)
            if isinstance(toolset, dict):
                resolved_toolset_refs.append(toolset_ref)
                tool_ids.extend(list(toolset.get("tool_ids") or []))

        if authority_policy is None:
            resolved_policy = _resolve("authority_policies", bindings.authority_policy_ref)
            if isinstance(resolved_policy, dict):
                authority_policy = AuthorityPolicy.model_validate(resolved_policy)

        local_instruction = (node.instruction or node.persona).strip()
        if local_instruction:
            instruction_parts.append(local_instruction)

        model = node.model or provider_policy.get("default_model")
        llm_hints = node.llm_hints
        if llm_hints is None and any([model, node.tool_ids]):
            llm_hints = LLMHints()

        deduped_tool_ids: list[str] = []
        seen: set[str] = set()
        for tool_id in tool_ids:
            if tool_id and tool_id not in seen:
                seen.add(tool_id)
                deduped_tool_ids.append(tool_id)

        retry_policy = None
        if retry_policy_data:
            retry_policy = RetryPolicy.model_validate(retry_policy_data)

        return EffectiveWorkerConfig(
            instruction="\n\n".join(part for part in instruction_parts if part),
            tool_ids=deduped_tool_ids,
            toolset_refs=resolved_toolset_refs,
            memory_policy=memory_policy,
            model=model,
            llm_hints=llm_hints,
            authority_policy=authority_policy,
            execution=execution,
            provider_policy=provider_policy,
            retry_policy=retry_policy,
        )

    @staticmethod
    def _bindings_are_noop(bindings: ContextBindings | None) -> bool:
        if bindings is None:
            return True
        return (
            bindings.instruction_profile_ref is None
            and bindings.memory_policy_ref is None
            and bindings.provider_policy_ref is None
            and bindings.retry_policy_ref is None
            and bindings.authority_policy_ref is None
            and not bindings.context_bundle_refs
            and not bindings.toolset_refs
            and bindings.inherit_defaults
        )

    @staticmethod
    def _apply_retry_policy_template(
        template: NodeBase,
        retry_policy: RetryPolicy | None,
    ) -> NodeBase:
        if template.retry_policy == retry_policy:
            return template
        return template.model_copy(update={"retry_policy": retry_policy})

    def _validate_task_tier_cap(
        self,
        node: Worker,
        effective: EffectiveWorkerConfig,
    ) -> str | None:
        policy = effective.authority_policy
        requested = effective.llm_hints.task_tier if effective.llm_hints is not None else None
        cap = policy.task_tier_cap if policy is not None else None
        if not requested or not cap:
            return None
        requested_rank = _TASK_TIER_ORDER.get(requested)
        cap_rank = _TASK_TIER_ORDER.get(cap)
        if requested_rank is None or cap_rank is None or requested_rank <= cap_rank:
            return None
        return (
            f"Worker '{node.id}' requests task tier '{requested}' "
            f"above cap '{cap}'"
        )

    def _validate_toolset_access(
        self,
        node: Worker,
        effective: EffectiveWorkerConfig,
    ) -> str | None:
        policy = effective.authority_policy
        if policy is None or not policy.allowed_toolset_refs:
            return None
        disallowed = [
            ref for ref in effective.toolset_refs
            if ref not in policy.allowed_toolset_refs
        ]
        if not disallowed:
            return None
        return (
            f"Worker '{node.id}' cannot access toolset refs {disallowed!r}; "
            f"allowed refs are {policy.allowed_toolset_refs!r}"
        )

    def _validate_memory_write_scopes(
        self,
        node: Worker,
        effective: EffectiveWorkerConfig,
    ) -> str | None:
        policy = effective.authority_policy
        if policy is None:
            return None
        declared_writes = [
            decl.key
            for decl in node.write_set
            if decl.mode in {ContextMode.WRITE, ContextMode.APPEND}
        ]
        if not declared_writes:
            return None
        allowed_scopes = policy.allow_memory_write_scopes
        disallowed = [
            key
            for key in declared_writes
            if not any(_scope_allows_key(scope, key) for scope in allowed_scopes)
        ]
        if not disallowed:
            return None
        return (
            f"Worker '{node.id}' cannot write memory scopes {disallowed!r}; "
            f"allowed scopes are {allowed_scopes!r}"
        )

    @staticmethod
    def _effective_retry_policy(
        node: Worker,
        effective: EffectiveWorkerConfig,
    ) -> RetryPolicy | None:
        if node.retry_policy is not None:
            return node.retry_policy.model_copy(deep=True)
        if effective.retry_policy is None:
            return None
        return effective.retry_policy.model_copy(deep=True)

    @staticmethod
    def _resolved_model(
        effective: EffectiveWorkerConfig,
        context: ExecutionContext,
    ) -> str:
        return effective.model or context.config.llm_default_model

    @staticmethod
    def _resolved_system_prompt(
        node: Worker,
        effective: EffectiveWorkerConfig,
        hints: LLMHints,
    ) -> str:
        system_parts: list[str] = []
        if hints.system_prompt:
            system_parts.append(hints.system_prompt)
        if node.role:
            system_parts.append(f"Role: {node.role}")
        if effective.instruction:
            system_parts.append(effective.instruction)
        return "\n\n".join(part for part in system_parts if part)

    def _static_llm_template(
        self,
        node: Worker,
        context: ExecutionContext,
        effective: EffectiveWorkerConfig,
    ) -> LLMOperator:
        hints = effective.llm_hints or LLMHints()
        resolved_model = self._resolved_model(effective, context)
        cache_key = (_worker_fingerprint(node), resolved_model)
        template = self._static_llm_templates.get(cache_key)
        if template is not None:
            return template

        prompt_template = hints.prompt_template or "{input}"
        template = LLMOperator(
            id=node.id,
            name=node.name,
            description=node.description,
            input_ports=node.input_ports,
            output_ports=node.output_ports,
            position=node.position,
            ui=node.ui,
            metadata=node.metadata,
            tags=node.tags,
            retry_policy=self._effective_retry_policy(node, effective),
            read_set=node.read_set,
            write_set=node.write_set,
            memoize=node.memoize,
            cache_ttl=node.cache_ttl,
            model=resolved_model,
            prompt_template=prompt_template,
            system_prompt=self._resolved_system_prompt(node, effective, hints),
            temperature=hints.temperature,
            max_tokens=hints.max_tokens,
            output_json_schema=hints.output_json_schema,
            tools=self._resolve_tool_schemas(effective.tool_ids, hints.tools),
            max_tool_rounds=hints.max_tool_rounds,
            history_policy=hints.history_policy,
            task_tier=hints.task_tier,
        )
        self._static_llm_templates[cache_key] = template
        return template

    async def _run_body_graph(
        self,
        node: Worker,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        if not node.body_graph:
            return NodeResult(outputs={}, status=NodeStatus.FAILED, error="Worker missing body_graph")
        mapped_inputs, targeted_inputs = self._map_body_graph_inputs(node, inputs)
        outputs = await context.run_subgraph(
            node.body_graph,
            mapped_inputs,
            parent_node_id=node.id,
            targeted_inputs=targeted_inputs,
        )
        outputs = self._map_body_graph_outputs(node, outputs)
        if "result" not in outputs and len(node.output_ports) == 1:
            port_name = node.output_ports[0].name
            if port_name not in outputs:
                aliased_payload = dict(outputs)
                outputs = dict(outputs)
                outputs[port_name] = aliased_payload
        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)

    async def _run_sub_workers(
        self,
        node: Worker,
        inputs: dict[str, Any],
        context: ExecutionContext,
        effective: EffectiveWorkerConfig,
    ) -> NodeResult:
        tier_error = self._validate_task_tier_cap(node, effective)
        if tier_error is not None:
            return NodeResult(outputs={}, status=NodeStatus.FAILED, error=tier_error)
        toolset_error = self._validate_toolset_access(node, effective)
        if toolset_error is not None:
            return NodeResult(outputs={}, status=NodeStatus.FAILED, error=toolset_error)
        policy = effective.authority_policy
        if node.authority == WorkerAuthority.LEAF and not (policy and policy.allow_delegate):
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Worker '{node.id}' is not allowed to delegate sub-workers",
            )
        if policy and policy.max_spawned_workers is not None:
            if len(node.sub_workers) > policy.max_spawned_workers:
                return NodeResult(
                    outputs={},
                    status=NodeStatus.FAILED,
                    error=(
                        f"Worker '{node.id}' exceeds max_spawned_workers "
                        f"({len(node.sub_workers)} > {policy.max_spawned_workers})"
                    ),
                )
        if node.spawn_policy and node.spawn_policy.max_spawns_per_node is not None:
            if len(node.sub_workers) > node.spawn_policy.max_spawns_per_node:
                return NodeResult(
                    outputs={},
                    status=NodeStatus.FAILED,
                    error=(
                        f"Worker '{node.id}' exceeds spawn_policy.max_spawns_per_node "
                        f"({len(node.sub_workers)} > {node.spawn_policy.max_spawns_per_node})"
                    ),
                )

        async def _invoke(alias: str, graph_ref: str) -> tuple[str, dict[str, Any]]:
            outputs = await context.run_subgraph(graph_ref, inputs, parent_node_id=node.id)
            return alias, outputs

        parallelism = effective.execution.parallelism_override if effective.execution else None
        if parallelism is None:
            parallelism = node.parallelism
        if parallelism == 1 or len(node.sub_workers) <= 1:
            results = [await _invoke(alias, ref) for alias, ref in node.sub_workers.items()]
        else:
            semaphore = asyncio.Semaphore(parallelism or len(node.sub_workers))

            async def _bounded(alias: str, graph_ref: str) -> tuple[str, dict[str, Any]]:
                async with semaphore:
                    return await _invoke(alias, graph_ref)

            results = await asyncio.gather(*[
                _bounded(alias, ref) for alias, ref in node.sub_workers.items()
            ])

        payload = {alias: outputs for alias, outputs in results}
        merged_payload, merge_error = self._merge_subworker_payload(node, results, payload)
        if merge_error is not None:
            return NodeResult(outputs={}, status=NodeStatus.FAILED, error=merge_error)
        outputs: dict[str, Any] = {"results": payload, "result": merged_payload}
        if len(node.output_ports) == 1:
            outputs[node.output_ports[0].name] = merged_payload
        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)

    async def _run_code(
        self,
        node: Worker,
        inputs: dict[str, Any],
        context: ExecutionContext,
        effective: EffectiveWorkerConfig,
    ) -> NodeResult:
        node_key = _worker_fingerprint(node)
        template = self._code_templates.get(node_key)
        if template is None:
            template = CodeOperator(
                id=node.id,
                name=node.name,
                description=node.description,
                input_ports=node.input_ports,
                output_ports=node.output_ports,
                position=node.position,
                ui=node.ui,
                metadata=node.metadata,
                tags=node.tags,
                retry_policy=node.retry_policy.model_copy(deep=True) if node.retry_policy is not None else None,
                read_set=node.read_set,
                write_set=node.write_set,
                memoize=node.memoize,
                cache_ttl=node.cache_ttl,
                code=node.code,
                language=node.language,
                sandbox_config=dict(node.metadata.get("sandbox_config") or {}),
            )
            self._code_templates[node_key] = template
        legacy = self._apply_retry_policy_template(
            template,
            self._effective_retry_policy(node, effective),
        )
        return await self._code.execute(legacy, inputs, context)

    async def _run_direct_tool(
        self,
        node: Worker,
        inputs: dict[str, Any],
        context: ExecutionContext,
        effective: EffectiveWorkerConfig,
    ) -> NodeResult:
        toolset_error = self._validate_toolset_access(node, effective)
        if toolset_error is not None:
            return NodeResult(outputs={}, status=NodeStatus.FAILED, error=toolset_error)
        if len(effective.tool_ids) != 1:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=(
                    f"Worker '{node.id}' has {len(effective.tool_ids)} tool refs but "
                    "no model; direct tool execution requires exactly one tool"
                ),
            )
        tool_id = effective.tool_ids[0]
        tool_executor = self._tool
        if tool_executor is None:
            registry = getattr(context, "tool_registry", None)
            tool_executor = ToolExecutor(registry)
        template_key = (_worker_fingerprint(node), tool_id)
        template = self._tool_templates.get(template_key)
        if template is None:
            template = ToolOperator(
                id=node.id,
                name=node.name,
                description=node.description,
                input_ports=node.input_ports,
                output_ports=node.output_ports,
                position=node.position,
                ui=node.ui,
                metadata=node.metadata,
                tags=node.tags,
                retry_policy=node.retry_policy.model_copy(deep=True) if node.retry_policy is not None else None,
                read_set=node.read_set,
                write_set=node.write_set,
                memoize=node.memoize,
                cache_ttl=node.cache_ttl,
                tool_id=tool_id,
                tool_config=dict(node.metadata.get("tool_config") or {}),
            )
            self._tool_templates[template_key] = template
        legacy = self._apply_retry_policy_template(
            template,
            self._effective_retry_policy(node, effective),
        )
        return await tool_executor.execute(legacy, inputs, context)

    async def _run_llm(
        self,
        node: Worker,
        inputs: dict[str, Any],
        context: ExecutionContext,
        effective: EffectiveWorkerConfig,
    ) -> NodeResult:
        toolset_error = self._validate_toolset_access(node, effective)
        if toolset_error is not None:
            return NodeResult(outputs={}, status=NodeStatus.FAILED, error=toolset_error)
        hints = effective.llm_hints or LLMHints()
        prompt_template = hints.prompt_template or "{input}"

        system_parts: list[str] = []
        if hints.system_prompt:
            system_parts.append(hints.system_prompt)
        if node.role:
            system_parts.append(f"Role: {node.role}")
        if effective.instruction:
            system_parts.append(effective.instruction)
        resolved_retry_policy = self._effective_retry_policy(node, effective)
        resolved_model = effective.model or context.config.llm_default_model
        resolved_system_prompt = "\n\n".join(part for part in system_parts if part)
        resolved_tools = self._resolve_tool_schemas(effective.tool_ids, hints.tools)

        node_key = _worker_fingerprint(node)
        template = self._llm_templates.get(node_key)
        if template is None:
            template = LLMOperator(
                id=node.id,
                name=node.name,
                description=node.description,
                input_ports=node.input_ports,
                output_ports=node.output_ports,
                position=node.position,
                ui=node.ui,
                metadata=node.metadata,
                tags=node.tags,
                retry_policy=node.retry_policy.model_copy(deep=True) if node.retry_policy is not None else None,
                read_set=node.read_set,
                write_set=node.write_set,
                memoize=node.memoize,
                cache_ttl=node.cache_ttl,
                model=node.model or "",
                prompt_template=prompt_template,
                system_prompt="",
                temperature=hints.temperature,
                max_tokens=hints.max_tokens,
                output_json_schema=hints.output_json_schema,
                tools=list(hints.tools),
                max_tool_rounds=hints.max_tool_rounds,
                history_policy=hints.history_policy,
                task_tier=hints.task_tier,
            )
            self._llm_templates[node_key] = template
        if (
            template.retry_policy == resolved_retry_policy
            and template.model == resolved_model
            and template.prompt_template == prompt_template
            and template.system_prompt == resolved_system_prompt
            and template.temperature == hints.temperature
            and template.max_tokens == hints.max_tokens
            and template.output_json_schema == hints.output_json_schema
            and template.tools == resolved_tools
            and template.max_tool_rounds == hints.max_tool_rounds
            and template.history_policy == hints.history_policy
            and template.task_tier == hints.task_tier
        ):
            legacy = template
        else:
            legacy = template.model_copy(
                update={
                    "retry_policy": resolved_retry_policy,
                    "model": resolved_model,
                    "prompt_template": prompt_template,
                    "system_prompt": resolved_system_prompt,
                    "temperature": hints.temperature,
                    "max_tokens": hints.max_tokens,
                    "output_json_schema": hints.output_json_schema,
                    "tools": resolved_tools,
                    "max_tool_rounds": hints.max_tool_rounds,
                    "history_policy": hints.history_policy,
                    "task_tier": hints.task_tier,
                }
            )
        return await self._llm.execute(legacy, inputs, context)

    async def _run_specialized_legacy(
        self,
        node: Worker,
        inputs: dict[str, Any],
        context: ExecutionContext,
        effective: EffectiveWorkerConfig,
    ) -> NodeResult:
        from dan.worker.presets import worker_to_legacy

        node_key = _worker_fingerprint(node)
        legacy = self._specialized_templates.get(node_key)
        if legacy is None:
            legacy = worker_to_legacy(node)
            if legacy is not None:
                self._specialized_templates[node_key] = legacy
        if legacy is None:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Worker '{node.id}' could not be projected to a specialized legacy node",
            )
        legacy = self._apply_retry_policy_template(
            legacy,
            self._effective_retry_policy(node, effective),
        )

        if legacy.node_type == "router":
            return await self._router.execute(legacy, inputs, context)
        if legacy.node_type == "validator":
            return await self._validator.execute(legacy, inputs, context)
        if legacy.node_type == "gate":
            return await self._gate.execute(legacy, inputs, context)
        if legacy.node_type == "reflection":
            return await self._reflection.execute(legacy, inputs, context)
        if legacy.node_type == "rag_operator":
            return await self._rag.execute(legacy, inputs, context)
        if legacy.node_type in {"human", "human_in_the_loop"}:
            return await self._human.execute(legacy, inputs, context)
        if legacy.node_type == "vote":
            return await self._vote.execute(legacy, inputs, context)
        if legacy.node_type == "reduce":
            return await self._reduce.execute(legacy, inputs, context)
        return NodeResult(
            outputs={},
            status=NodeStatus.FAILED,
            error=(
                f"Worker '{node.id}' projected to unsupported specialized node "
                f"type {legacy.node_type!r}"
            ),
        )

    def _resolve_tool_schemas(
        self,
        tool_ids: list[str],
        explicit_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if explicit_tools:
            return list(explicit_tools)
        available = get_all_tools()
        schemas: list[dict[str, Any]] = []
        for tool_id in tool_ids:
            meta = available.get(tool_id, (None, None))[1]
            if isinstance(meta, dict):
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool_id,
                            "description": meta.get("description", ""),
                            "parameters": meta.get("parameters", {"type": "object", "properties": {}}),
                        },
                    }
                )
            else:
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool_id,
                            "description": f"Call tool '{tool_id}'",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                )
        return schemas

    @staticmethod
    def _map_body_graph_inputs(
        node: Worker,
        inputs: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]] | None]:
        if not node.input_mappings:
            return dict(inputs), None

        broadcast_inputs: dict[str, Any] = {}
        targeted_inputs: dict[str, dict[str, Any]] = {}
        for key, value in inputs.items():
            target = node.input_mappings.get(key)
            if target is None:
                broadcast_inputs[key] = value
                continue
            if "::" in target:
                target_node_id, port_name = target.split("::", 1)
                targeted_inputs.setdefault(target_node_id, {})[port_name] = value
            else:
                broadcast_inputs[target] = value
        return broadcast_inputs, targeted_inputs or None

    @staticmethod
    def _map_body_graph_outputs(
        node: Worker,
        body_output: dict[str, Any],
    ) -> dict[str, Any]:
        if not node.output_mappings:
            return dict(body_output)

        mapped_outputs: dict[str, Any] = {}
        mapped_inner_keys: set[str] = set()
        for inner_key, outer_port in node.output_mappings.items():
            resolved_key = inner_key.split("::", 1)[1] if "::" in inner_key else inner_key
            if resolved_key in body_output:
                mapped_outputs[outer_port] = body_output[resolved_key]
                mapped_inner_keys.add(resolved_key)
        for key, value in body_output.items():
            if key not in mapped_inner_keys:
                mapped_outputs[key] = value
        return mapped_outputs

    @staticmethod
    def _merge_subworker_payload(
        node: Worker,
        results: list[tuple[str, dict[str, Any]]],
        payload: dict[str, dict[str, Any]],
    ) -> tuple[Any, str | None]:
        if node.merge_strategy == MergeStrategy.APPEND:
            return payload, None
        if node.merge_strategy == MergeStrategy.LAST_WRITE_WINS:
            merged: dict[str, Any] = {}
            for alias, branch_output in results:
                if isinstance(branch_output, dict):
                    merged.update(branch_output)
                else:
                    merged[alias] = branch_output
            return merged, None
        if node.merge_strategy == MergeStrategy.REDUCER:
            reducer = (
                node.metadata.get("merge_reducer")
                or node.metadata.get("reducer")
                or node.metadata.get("reduce_expression")
            )
            if not reducer:
                return None, (
                    f"Worker '{node.id}' uses merge_strategy='reducer' but "
                    "does not define metadata['merge_reducer']"
                )
            try:
                reduced = evaluate_reducer(str(reducer), [branch_output for _, branch_output in results])
            except Exception as exc:
                return None, f"Worker '{node.id}' reducer merge failed: {exc}"
            return reduced, None
        return payload, None

    async def _try_execute_static_fast_path(
        self,
        node: Worker,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult | None:
        catalog = getattr(getattr(context, "graph", None), "worker_resources", {}) or {}
        if catalog or not self._bindings_are_noop(node.context):
            return None

        effective = self._resolve_effective_config(node, context)
        memory_scope_error = self._validate_memory_write_scopes(node, effective)
        if memory_scope_error is not None:
            return NodeResult(outputs={}, status=NodeStatus.FAILED, error=memory_scope_error)

        node_key = _worker_fingerprint(node)
        modes = self._static_modes.get(node_key)
        if modes is None:
            modes = tuple(self._detect_modes(node, effective))
            self._static_modes[node_key] = modes

        if modes == (ExecutionMode.SCRIPT,):
            return await self._run_code(node, inputs, context, effective)
        if modes == (ExecutionMode.TOOL,):
            return await self._run_direct_tool(node, inputs, context, effective)
        if modes in {(ExecutionMode.LLM,), (ExecutionMode.LLM_WITH_TOOLS,)}:
            tier_error = self._validate_task_tier_cap(node, effective)
            if tier_error is not None:
                return NodeResult(outputs={}, status=NodeStatus.FAILED, error=tier_error)
            toolset_error = self._validate_toolset_access(node, effective)
            if toolset_error is not None:
                return NodeResult(outputs={}, status=NodeStatus.FAILED, error=toolset_error)
            return await self._llm.execute(
                self._static_llm_template(node, context, effective),
                inputs,
                context,
            )
        if modes == (ExecutionMode.PASSTHROUGH,):
            return self._passthrough(node, inputs)
        return None

    def _passthrough(
        self,
        node: Worker,
        inputs: dict[str, Any],
    ) -> NodeResult:
        input_variables = node.metadata.get("input_variables")
        if isinstance(input_variables, list):
            outputs: dict[str, Any] = {}
            aggregate: dict[str, Any] = {}
            resolved_inputs = dict(inputs)
            aggregate_input = inputs.get("input")
            if isinstance(aggregate_input, dict):
                for key, value in aggregate_input.items():
                    resolved_inputs.setdefault(key, value)
            for raw_variable in input_variables:
                if not isinstance(raw_variable, dict):
                    continue
                name = raw_variable.get("name")
                if not isinstance(name, str) or not name:
                    continue
                value = resolved_inputs.get(name, raw_variable.get("default"))
                outputs[name] = value
                aggregate[name] = value
            if "input" not in outputs:
                outputs["input"] = aggregate
            return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)

        outputs: dict[str, Any] = {}
        remaining = {k: v for k, v in inputs.items()}
        for port in node.output_ports:
            if port.name in remaining:
                outputs[port.name] = remaining.pop(port.name)
                continue
            if remaining:
                next_key = next(iter(remaining))
                outputs[port.name] = remaining.pop(next_key)
        if not outputs:
            outputs = dict(inputs)
        return NodeResult(outputs=outputs, status=NodeStatus.COMPLETED)


class LegacyWorkerAdapterExecutor:
    """Execute bridged legacy compute nodes by routing through WorkerExecutor."""

    def __init__(self, worker_executor: WorkerExecutor) -> None:
        self.worker_executor = worker_executor

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        if isinstance(node, Worker):
            return await self.worker_executor.execute(node, inputs, context)

        from dan.worker.presets import legacy_to_worker

        worker = legacy_to_worker(node)
        if worker is None:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=(
                    f"Legacy node type {getattr(node, 'node_type', type(node).__name__)!r} "
                    "does not have a Worker bridge"
                ),
            )
        return await self.worker_executor.execute(worker, inputs, context)
