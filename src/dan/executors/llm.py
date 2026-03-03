"""LLM executor — multi-provider chat completions with output normalization."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import string
from typing import Any

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.normalizer import OutputNormalizer
from dan.engine.state import NodeStatus
from dan.engine.token_optimization import (
    ContextSelector,
    ContextToolProvider,
    HistoryManager,
    PayloadPruner,
    SummarizationConfig,
    SystemPromptTracker,
    ToolSchemaResolver,
)
from dan.models.nodes import LLMOperator, NodeBase, RetryPolicy
from dan.providers import CompletionResult
from dan.providers.cost_tracker import TokenSaving
from dan.utils.tokens import estimate_tokens

logger = logging.getLogger(__name__)

_LLM_DEFAULT_RETRY = RetryPolicy(max_retries=3)


def _render_template(template: str, variables: dict[str, Any]) -> str:
    """Render a prompt template with Python str.format_map.

    Uses safe_substitute semantics: missing keys are left as-is rather
    than raising KeyError.
    """
    try:
        return template.format_map(variables)
    except (KeyError, IndexError, ValueError):
        return string.Template(template).safe_substitute(variables)


class LLMExecutor:
    """Executes LLMOperator nodes via the provider registry.

    Handles prompt rendering, chat completion calls, output normalization
    (parse/validate/re-prompt loop), and retry policy for API failures.
    Backward compatible: falls back to direct AsyncOpenAI if no provider
    registry is available.
    """

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self._client = client

    def _get_client(self, context: ExecutionContext) -> AsyncOpenAI:
        """Legacy fallback — used only when provider_registry is not available."""
        if self._client is not None:
            return self._client
        return AsyncOpenAI(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )

    def _resolve_provider(self, model: str, context: ExecutionContext):
        """Resolve the LLM provider for a model, with backward compat fallback."""
        if context.provider_registry is not None:
            return context.provider_registry.resolve(model)

        from dan.providers import ProviderConfig
        from dan.providers.openai_provider import OpenAIProvider

        if self._client is not None:
            return OpenAIProvider.from_client(self._client)

        config = ProviderConfig(
            api_key=context.config.llm_api_key,
            base_url=context.config.llm_base_url,
        )
        return OpenAIProvider(config)

    @staticmethod
    async def _get_error_memory_context(
        node: NodeBase,
        rendered_prompt: str,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> str:
        """Retrieve error-memory context for prompt injection.

        Only applies when the node matches the configured target tags or types.
        """
        config = context.config
        node_type = getattr(node, "node_type", "")
        node_tags = set(getattr(node, "tags", []))
        target_tags = set(getattr(config, "error_memory_target_tags", []))
        target_types = getattr(config, "error_memory_target_types", ["llm_operator"])

        if not (node_tags & target_tags) and node_type not in target_types:
            return ""

        error_ctx_provider = getattr(context, "_error_context_provider", None)
        if error_ctx_provider is None:
            return ""

        try:
            workflow_id = getattr(context, "_workflow_id", "") or ""
            cross_wf = getattr(config, "cross_workflow_learning", False)
            return await error_ctx_provider.get_context(
                node, rendered_prompt, inputs, workflow_id,
                cross_workflow=cross_wf,
            )
        except Exception:
            logger.debug(
                "Error memory context retrieval failed", exc_info=True,
            )
            return ""

    async def _assemble_inputs(
        self,
        node: LLMOperator,
        inputs: dict[str, Any],
        context: ExecutionContext,
        model: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply context selection, payload pruning/formatting, and summarization."""
        inline_inputs = dict(inputs)
        deferred_inputs: dict[str, Any] = {}

        for key, value in list(inline_inputs.items()):
            if not self._is_reference_value(value):
                continue
            uri = str(value.get("uri", ""))
            should_resolve = f"{{{key}}}" in node.prompt_template
            if should_resolve and uri and context.artifacts.has(uri):
                try:
                    inline_inputs[key] = context.artifacts.fetch(uri)
                except Exception:
                    inline_inputs[key] = value.get("summary", str(value))
                    deferred_inputs[key] = value
            else:
                inline_inputs[key] = value.get("summary", str(value))
                deferred_inputs[key] = value

        if (
            node.history_policy is not None
            and isinstance(inline_inputs.get("conversation_history"), list)
        ):
            manager = HistoryManager()
            assembled_history, older = manager.assemble_history(
                inline_inputs["conversation_history"],
                node.history_policy,
                context_tools_available=node.agent_context_tools,
            )
            inline_inputs["conversation_history"] = assembled_history
            if older and context.short_term_memory is not None:
                await manager.persist_older_messages(
                    older,
                    context.short_term_memory,
                    node_id=node.id,
                    run_id=getattr(context, "_run_id", ""),
                )

        if node.target_input_tokens is not None:
            selector = ContextSelector()
            inline_inputs, deferred_inputs = selector.select(
                inline_inputs, node.prompt_template, node.target_input_tokens,
            )
            if deferred_inputs:
                inline_tokens = sum(
                    estimate_tokens(str(v)) for v in inline_inputs.values()
                )
                deferred_tokens = sum(
                    estimate_tokens(str(v)) for v in deferred_inputs.values()
                )
                await context.emit_event(
                    event_type="context_deferred",
                    node_id=node.id,
                    node_type="llm_operator",
                    data={
                        "inputs_inline": sorted(inline_inputs),
                        "inputs_deferred": [
                            {
                                "port": name,
                                "summary": str(value)[:200],
                            }
                            for name, value in deferred_inputs.items()
                        ],
                        "tokens_inline": inline_tokens,
                        "tokens_deferred": deferred_tokens,
                    },
                )
                if context.cost_tracker is not None:
                    context.cost_tracker.record_saving(TokenSaving(
                        action="context_deferred",
                        tokens_saved=deferred_tokens,
                        node_id=node.id,
                    ))

        if node.prune_fields or node.input_format != "json":
            pruner = PayloadPruner(node.prune_fields)
            for key, value in list(inline_inputs.items()):
                if not isinstance(value, (dict, list)):
                    continue
                pruned_value = value
                removed = 0
                if node.prune_fields:
                    pruned_value, removed = pruner.prune(value)
                if node.input_format != "json":
                    inline_inputs[key] = pruner.format_compact(pruned_value, node.input_format)
                else:
                    inline_inputs[key] = pruned_value
                if removed > 0:
                    before_tok = estimate_tokens(str(value))
                    after_tok = estimate_tokens(str(pruned_value))
                    await context.emit_event(
                        event_type="payload_pruned",
                        node_id=node.id,
                        node_type="llm_operator",
                        data={
                            "input_port": key,
                            "fields_removed": removed,
                        },
                    )
                    if context.cost_tracker is not None and before_tok > after_tok:
                        context.cost_tracker.record_saving(TokenSaving(
                            action="payload_pruned",
                            tokens_saved=before_tok - after_tok,
                            node_id=node.id,
                            details={"port": key, "fields_removed": removed},
                        ))

        summarize_cfg: SummarizationConfig | None = None
        if node.summarize_inputs:
            summarize_cfg = (
                SummarizationConfig.model_validate(node.summarize_inputs)
                if isinstance(node.summarize_inputs, dict)
                else SummarizationConfig()
            )

        if summarize_cfg is not None:
            for key, value in list(inline_inputs.items()):
                if not isinstance(value, str):
                    continue
                before_tokens = estimate_tokens(value)
                if before_tokens <= summarize_cfg.max_summary_tokens * 2:
                    continue
                if summarize_cfg.preserve_structured and value.strip().startswith(("{", "[")):
                    continue

                summary, usage, summary_model = await self._summarize_text(
                    text=value,
                    config=summarize_cfg,
                    model_hint=model,
                    context=context,
                )
                if summary:
                    inline_inputs[key] = summary
                    after_tokens = estimate_tokens(summary)
                    await context.emit_event(
                        event_type="input_summarized",
                        node_id=node.id,
                        node_type="llm_operator",
                        data={
                            "input_port": key,
                            "tokens_before": before_tokens,
                            "tokens_after": after_tokens,
                            "model_used": summary_model,
                            "persisted_to_memory": summarize_cfg.persist_to_memory,
                        },
                    )
                    if context.cost_tracker is not None and before_tokens > after_tokens:
                        context.cost_tracker.record_saving(TokenSaving(
                            action="input_summarized",
                            tokens_saved=before_tokens - after_tokens,
                            node_id=node.id,
                            details={"port": key},
                        ))
                    if summarize_cfg.persist_to_memory and context.short_term_memory is not None:
                        from dan.engine.memory_pipeline import MemoryItem

                        context.short_term_memory.append(
                            MemoryItem(
                                content=summary,
                                source_node_id=node.id,
                                source_run_id=getattr(context, "_run_id", ""),
                                metadata={
                                    "entry_type": "distilled_fact",
                                    "source_input_port": key,
                                },
                            )
                        )

        if node.target_input_tokens is not None:
            estimated = estimate_tokens(_render_template(node.prompt_template, inline_inputs))
            if estimated > node.target_input_tokens:
                await context.emit_event(
                    event_type="token_budget_advisory",
                    node_id=node.id,
                    node_type="llm_operator",
                    data={
                        "target_input_tokens": node.target_input_tokens,
                        "estimated_input_tokens": estimated,
                    },
                )

        return inline_inputs, deferred_inputs

    async def _summarize_text(
        self,
        *,
        text: str,
        config: SummarizationConfig,
        model_hint: str,
        context: ExecutionContext,
    ) -> tuple[str, dict[str, int] | None, str]:
        """Summarize long text using a lightweight LLM call."""
        summary_model = config.model or context.config.llm_default_model or model_hint
        try:
            provider = self._resolve_provider(summary_model, context)
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Summarize the user text concisely while preserving key facts, "
                        "numbers, and critical constraints."
                    ),
                },
                {"role": "user", "content": text},
            ]
            result = await provider.complete(
                messages=messages,
                model=summary_model,
                temperature=0.0,
                max_tokens=config.max_summary_tokens,
            )
            usage = result.usage
            if usage and context.cost_tracker is not None:
                context.cost_tracker.record(
                    "__summarization__", summary_model, usage,
                    cached_input_tokens=usage.get("cached_input_tokens", 0),
                    cache_write_tokens=usage.get("cache_write_tokens", 0),
                )
            return result.text, usage, summary_model
        except Exception:
            logger.debug("Input summarization failed", exc_info=True)
            return "", None, summary_model

    def _prepare_tools(
        self,
        node: LLMOperator,
        context: ExecutionContext,
        deferred_inputs: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], ContextToolProvider | None, ToolSchemaResolver | None]:
        """Build active tool list, including JIT catalog/context tools when enabled."""
        base_tools = list(node.tools)
        resolver: ToolSchemaResolver | None = None

        if node.jit_tool_loading and len(base_tools) > 3:
            resolver = ToolSchemaResolver(base_tools)
            base_tools = resolver.get_catalog() + [resolver.get_resolver_tool()]

        context_tools: ContextToolProvider | None = None
        if node.agent_context_tools:
            context_tools = ContextToolProvider(
                artifacts=context.artifacts,
                state_store=getattr(context, "state_store", None),
                local_state=context.local_state,
                memory=context.short_term_memory,
                embedding_registry=context.embedding_registry,
                deferred_inputs=deferred_inputs,
                run_scope=getattr(context.state, "run_id", ""),
            )
            base_tools.extend(context_tools.get_tool_definitions())

        return base_tools, context_tools, resolver

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, LLMOperator)
        policy = node.retry_policy or _LLM_DEFAULT_RETRY
        model = node.model or context.config.llm_default_model

        if context.model_selector is not None:
            effective_policy = context.model_selector.resolve_effective_policy(
                node, context.config,
            )
            if effective_policy is not None:
                selected = await context.model_selector.select(
                    effective_policy, node, context,
                )
                if selected:
                    model = selected
                    await context.emit_event(
                        event_type="model_selected",
                        node_id=node.id,
                        node_type="llm_operator",
                        data={"model": model, "policy_strategy": effective_policy.strategy},
                    )

        assembled_inputs, deferred_inputs = await self._assemble_inputs(
            node, inputs, context, model,
        )
        rendered_prompt = _render_template(node.prompt_template, assembled_inputs)

        await context.emit_event(
            event_type="llm_thinking",
            node_id=node.id,
            node_type="llm_operator",
            data={"model": model, "prompt_preview": rendered_prompt[:2000]},
        )

        tracker = getattr(context, "_system_prompt_tracker", None)
        if tracker is None:
            tracker = SystemPromptTracker()
            context._system_prompt_tracker = tracker
        dedup_metrics = (
            tracker.track(node.system_prompt)
            if node.system_prompt
            else {"is_duplicate": False}
        )

        messages: list[dict[str, Any]] = []
        if node.system_prompt:
            messages.append({"role": "system", "content": node.system_prompt})
        messages.append({"role": "user", "content": rendered_prompt})

        # -- 17-1: Error memory context injection ------------------------------
        if getattr(context.config, "error_memory_enabled", False):
            error_ctx = await self._get_error_memory_context(
                node, rendered_prompt, assembled_inputs, context,
            )
            if error_ctx:
                messages.insert(
                    1 if node.system_prompt else 0,
                    {"role": "system", "content": error_ctx},
                )
                await context.emit_event(
                    event_type="error_memory_retrieved",
                    node_id=node.id,
                    node_type="llm_operator",
                    data={
                        "context_chars": len(error_ctx),
                        "tier": "error_memory",
                    },
                )

        # -- 15-1: Hyperedge pre-prompt injection ------------------------------
        if (
            getattr(context, "hyperedge_resolver", None)
            and getattr(context.config, "hyperedge_enforcement", "off") != "off"
        ):
            messages = context.hyperedge_resolver.apply_pre_prompt(node, messages)

        active_tools, context_tool_provider, schema_resolver = self._prepare_tools(
            node, context, deferred_inputs,
        )
        max_norm_retries = context.config.output_norm_max_retries
        has_schema = node.output_json_schema is not None
        has_tools = bool(active_tools)

        last_error: str | None = None
        cumulative_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for attempt in range(1 + max_norm_retries):
            raw_text, api_error, usage, tool_calls = await self._call_llm(
                model, messages, node, context=context, attempt=attempt, tools=active_tools,
            )
            if usage:
                for k in cumulative_usage:
                    cumulative_usage[k] += usage.get(k, 0)
                if context.cost_tracker is not None:
                    context.cost_tracker.record(
                        node.id, model, usage,
                        cached_input_tokens=usage.get("cached_input_tokens", 0),
                        cache_write_tokens=usage.get("cache_write_tokens", 0),
                    )

            if api_error:
                last_error = api_error
                break

            # -- Tool-calling loop ------------------------------------------------
            if has_tools and tool_calls:
                tool_result_text, tool_error, _tool_usage = await self._run_tool_loop(
                    model, messages, node, context, raw_text, tool_calls, cumulative_usage,
                    tools=active_tools,
                    context_tool_provider=context_tool_provider,
                    schema_resolver=schema_resolver,
                )
                if tool_error:
                    last_error = tool_error
                    break
                raw_text = tool_result_text

            meta = {
                "model": model,
                "attempts": attempt + 1,
                "usage": cumulative_usage,
                "system_prompt_dedup": dedup_metrics,
                "deferred_inputs": sorted(deferred_inputs),
            }

            if context.cost_tracker is not None:
                self._record_token_breakdown(
                    node, context, messages, cumulative_usage, assembled_inputs,
                )

            if not has_schema:
                return NodeResult(
                    outputs={"text": raw_text},
                    status=NodeStatus.COMPLETED,
                    metadata=meta,
                )

            result = OutputNormalizer.normalize(raw_text, node.output_json_schema)  # type: ignore[arg-type]
            if result.success:
                data = result.data or {}
                outputs = {**data, "result": data}
                return NodeResult(
                    outputs=outputs,
                    status=NodeStatus.COMPLETED,
                    metadata=meta,
                )

            if attempt < max_norm_retries:
                messages.append({"role": "assistant", "content": raw_text})
                messages.append({"role": "user", "content": result.error_message or ""})
                logger.debug(
                    "Output normalization failed (attempt %d/%d): %s",
                    attempt + 1,
                    max_norm_retries + 1,
                    result.error_message,
                )
            else:
                last_error = result.error_message

        fail_meta = {"model": model, "usage": cumulative_usage}

        if policy.on_failure == "skip":
            return NodeResult(
                outputs={}, status=NodeStatus.SKIPPED, metadata=fail_meta,
            )
        if policy.on_failure == "halt":
            return NodeResult(
                outputs={}, status=NodeStatus.FAILED,
                error=last_error or "LLM execution failed",
                metadata={**fail_meta, "halt": True},
            )
        return NodeResult(
            outputs={}, status=NodeStatus.FAILED,
            error=last_error or "LLM execution failed",
            metadata=fail_meta,
        )

    # -- Tool-calling loop helpers --------------------------------------------

    async def _run_tool_loop(
        self,
        model: str,
        messages: list[dict[str, Any]],
        node: LLMOperator,
        context: ExecutionContext,
        initial_text: str,
        tool_calls: list[dict[str, Any]],
        cumulative_usage: dict[str, int],
        *,
        tools: list[dict[str, Any]] | None = None,
        context_tool_provider: ContextToolProvider | None = None,
        schema_resolver: ToolSchemaResolver | None = None,
    ) -> tuple[str, str | None, dict[str, int]]:
        """Execute the tool-calling loop until the model returns plain text.

        Returns (final_text, error_or_none, cumulative_usage).
        """
        current_text = initial_text
        current_tool_calls = tool_calls

        for round_idx in range(node.max_tool_rounds):
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if current_text:
                assistant_msg["content"] = current_text
            assistant_msg["tool_calls"] = current_tool_calls
            messages.append(assistant_msg)

            for tc in current_tool_calls:
                tc_id = tc.get("id", "")
                fn = tc.get("function", {})
                fn_name = fn.get("name", tc.get("name", ""))
                raw_args = fn.get("arguments", tc.get("arguments", "{}"))

                await context.emit_event(
                    event_type="tool_call_started",
                    node_id=node.id,
                    node_type="llm_operator",
                    data={"tool_name": fn_name, "tool_call_id": tc_id, "round": round_idx + 1},
                )

                tool_result = await self._execute_tool_call(
                    tc,
                    node,
                    context,
                    context_tool_provider=context_tool_provider,
                    schema_resolver=schema_resolver,
                )

                await context.emit_event(
                    event_type="tool_call_result",
                    node_id=node.id,
                    node_type="llm_operator",
                    data={
                        "tool_name": fn_name,
                        "tool_call_id": tc_id,
                        "result_preview": tool_result[:500],
                        "round": round_idx + 1,
                    },
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": tool_result,
                })

            next_text, api_error, usage, next_tool_calls = await self._call_llm(
                model, messages, node, context=context, attempt=0, tools=tools,
            )
            if usage:
                for k in cumulative_usage:
                    cumulative_usage[k] += usage.get(k, 0)
                if context.cost_tracker is not None:
                    context.cost_tracker.record(
                        node.id, model, usage,
                        cached_input_tokens=usage.get("cached_input_tokens", 0),
                        cache_write_tokens=usage.get("cache_write_tokens", 0),
                    )

            if api_error:
                return "", api_error, cumulative_usage

            if not next_tool_calls:
                return next_text, None, cumulative_usage

            current_text = next_text
            current_tool_calls = next_tool_calls

        return current_text, f"Tool-calling loop exceeded max_tool_rounds ({node.max_tool_rounds})", cumulative_usage

    async def _execute_tool_call(
        self,
        tool_call: dict[str, Any],
        node: LLMOperator,
        context: ExecutionContext,
        *,
        context_tool_provider: ContextToolProvider | None = None,
        schema_resolver: ToolSchemaResolver | None = None,
    ) -> str:
        """Resolve and execute a single tool call, returning the result as a string."""
        fn = tool_call.get("function", {})
        fn_name = fn.get("name", tool_call.get("name", ""))
        raw_args = fn.get("arguments", tool_call.get("arguments", "{}"))
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (json.JSONDecodeError, TypeError):
            args = {}

        if context_tool_provider is not None and context_tool_provider.has_tool(fn_name):
            try:
                result = await context_tool_provider.execute_tool(fn_name, args)
                await context.emit_event(
                    event_type="context_tool_called",
                    node_id=node.id,
                    node_type="llm_operator",
                    data={
                        "tool_name": fn_name,
                        "ref_or_query": args.get("ref", args.get("query", args.get("key", ""))),
                        "tokens_loaded": estimate_tokens(result),
                    },
                )
                return result
            except Exception as exc:
                logger.warning("Context tool '%s' execution failed: %s", fn_name, exc)
                return json.dumps({"error": str(exc)})

        if schema_resolver is not None and fn_name == "get_tool_schema":
            tool_name = str(args.get("tool_name", ""))
            schema = schema_resolver.get_full_schema(tool_name)
            if schema is None:
                return json.dumps({"error": f"Unknown tool schema '{tool_name}'"})
            await context.emit_event(
                event_type="jit_schema_loaded",
                node_id=node.id,
                node_type="llm_operator",
                data={
                    "tool_name": tool_name,
                    "tokens_saved": schema_resolver.tokens_saved(),
                },
            )
            return json.dumps(schema, default=str)

        tool_registry = getattr(context, "tool_registry", None)
        if tool_registry is not None and tool_registry.has(fn_name):
            try:
                result = await tool_registry.get(fn_name)(**args)
                if isinstance(result, (dict, list)):
                    return json.dumps(result, default=str)
                return str(result)
            except Exception as exc:
                logger.warning("Tool '%s' execution failed: %s", fn_name, exc)
                return json.dumps({"error": str(exc)})

        return json.dumps({"error": f"Tool '{fn_name}' not found in registry"})

    @staticmethod
    def _extract_usage(obj: Any) -> dict[str, int] | None:
        usage = getattr(obj, "usage", None)
        if usage is None:
            return None
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }

    @staticmethod
    def _record_token_breakdown(
        node: LLMOperator,
        context: ExecutionContext,
        messages: list[dict[str, Any]],
        usage: dict[str, int],
        assembled_inputs: dict[str, Any],
    ) -> None:
        """Build and store a TokenBreakdown in the CostTracker."""
        from dan.providers.cost_tracker import TokenBreakdown

        sys_tok = 0
        user_tok = 0
        asst_tok = 0
        for m in messages:
            role = m.get("role", "")
            tok = estimate_tokens(m.get("content", ""))
            if role == "system":
                sys_tok += tok
            elif role == "user":
                user_tok += tok
            elif role == "assistant":
                asst_tok += tok

        ctx_edge_tok = 0
        mem_tok = 0
        rag_tok = 0
        for port_name, val in assembled_inputs.items():
            tok = estimate_tokens(val if isinstance(val, str) else json.dumps(val, default=str))
            if port_name.startswith("_memory") or port_name.startswith("memory"):
                mem_tok += tok
            elif port_name.startswith("_rag") or port_name.startswith("rag"):
                rag_tok += tok
            else:
                ctx_edge_tok += tok

        total_input = usage.get("prompt_tokens", sys_tok + user_tok)
        total_output = usage.get("completion_tokens", 0)

        bd = TokenBreakdown(
            system_tokens=sys_tok,
            user_tokens=user_tok,
            assistant_tokens=asst_tok,
            output_tokens=total_output,
            context_edge_tokens=ctx_edge_tok,
            hyperedge_tokens=0,
            memory_tokens=mem_tok,
            rag_tokens=rag_tok,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
        )
        context.cost_tracker.record_breakdown(node.id, bd)

    async def _call_llm(
        self,
        model: str,
        messages: list[dict[str, Any]],
        node: LLMOperator,
        context: ExecutionContext | None = None,
        attempt: int = 0,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str | None, dict[str, int] | None, list[dict[str, Any]] | None]:
        """Call the LLM with retry on transient API errors.

        Returns (response_text, error_message, usage_dict, tool_calls).
        On success error_message is None; on exhausted retries response_text is empty.
        """
        policy = node.retry_policy or _LLM_DEFAULT_RETRY
        max_retries = max(policy.max_retries, 1)
        backoff = policy.backoff
        current_model = model

        for retry in range(max_retries):
            try:
                provider = self._resolve_provider(current_model, context) if context else None

                if provider is not None:
                    text, usage, tool_calls = await self._call_via_provider(
                        provider, current_model, messages, node, context, attempt, tools,
                    )
                    return text, None, usage, tool_calls
                else:
                    return "", "No execution context available", None, None

            except (RateLimitError, APITimeoutError) as exc:
                if retry < max_retries - 1:
                    logger.warning(
                        "Transient API error (attempt %d/%d): %s",
                        retry + 1, max_retries, exc,
                    )
                    if context:
                        await context.emit_event(
                            event_type="retry_attempted",
                            node_id=node.id,
                            node_type="llm_operator",
                            data={
                                "attempt": retry + 1,
                                "max_retries": max_retries,
                                "error": str(exc),
                                "model": current_model,
                            },
                        )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, policy.backoff_max)
                else:
                    if policy.fallback_model and current_model != policy.fallback_model:
                        logger.info(
                            "Retries exhausted on '%s', trying fallback '%s'",
                            current_model, policy.fallback_model,
                        )
                        if context:
                            await context.emit_event(
                                event_type="retry_attempted",
                                node_id=node.id,
                                node_type="llm_operator",
                                data={
                                    "attempt": retry + 1,
                                    "max_retries": max_retries,
                                    "error": str(exc),
                                    "model": current_model,
                                    "fallback_model": policy.fallback_model,
                                },
                            )
                        current_model = policy.fallback_model
                        backoff = policy.backoff
                        try:
                            fb_provider = self._resolve_provider(current_model, context) if context else None
                            if fb_provider is not None:
                                fallback_kwargs: dict[str, Any] = {}
                                if tools:
                                    fallback_kwargs["tools"] = self._sorted_tool_schemas(tools)
                                result = await fb_provider.complete(
                                    messages=messages,
                                    model=current_model,
                                    temperature=node.temperature,
                                    max_tokens=node.max_tokens,
                                    **fallback_kwargs,
                                )
                                return result.text, None, result.usage, result.tool_calls
                            return "", f"No provider for fallback model '{current_model}'", None, None
                        except Exception as fb_exc:
                            return "", f"Fallback model '{current_model}' also failed: {fb_exc}", None, None
                    return "", f"API error after {max_retries} retries: {exc}", None, None

            except (APIError,) as exc:
                return "", f"API error: {exc}", None, None

            except Exception as exc:
                return "", f"Unexpected error calling LLM: {exc}", None, None

        return "", "LLM call failed", None, None

    async def _call_via_provider(
        self,
        provider: Any,
        model: str,
        messages: list[dict[str, Any]],
        node: LLMOperator,
        context: ExecutionContext | None,
        attempt: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, int] | None, list[dict[str, Any]] | None]:
        """Call LLM via provider — use complete() when tools are active, else try streaming."""
        if context and getattr(context.config, "prompt_caching_enabled", True):
            from dan.providers import apply_cache_hints
            messages = apply_cache_hints(provider, messages)

        extra_kwargs: dict[str, Any] = {}
        if tools:
            extra_kwargs["tools"] = self._sorted_tool_schemas(tools)

        if tools:
            result = await provider.complete(
                messages=messages,
                model=model,
                temperature=node.temperature,
                max_tokens=node.max_tokens,
                **extra_kwargs,
            )
            return result.text, result.usage, result.tool_calls

        try:
            accumulated = ""
            chunk_count = 0
            last_usage = None
            stream_iter = provider.stream(
                messages=messages,
                model=model,
                temperature=node.temperature,
                max_tokens=node.max_tokens,
            )
            if inspect.isawaitable(stream_iter):
                stream_iter = await stream_iter

            async for chunk in stream_iter:
                accumulated = chunk.accumulated
                chunk_count += 1
                if context and chunk_count % 5 == 0 and not chunk.done:
                    await context.emit_event(
                        event_type="intermediate_text",
                        node_id=node.id,
                        node_type="llm_operator",
                        data={"delta": chunk.delta, "text": accumulated, "attempt": attempt},
                    )
                if chunk.done:
                    last_usage = chunk.usage

            if context and accumulated:
                await context.emit_event(
                    event_type="intermediate_text",
                    node_id=node.id,
                    node_type="llm_operator",
                    data={"delta": "", "text": accumulated, "attempt": attempt, "done": True},
                )
            return accumulated, last_usage, None
        except Exception:
            result = await provider.complete(
                messages=messages,
                model=model,
                temperature=node.temperature,
                max_tokens=node.max_tokens,
            )
            return result.text, result.usage, result.tool_calls

    @staticmethod
    def _sorted_tool_schemas(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return tool schemas sorted by function name for cache stability."""
        return sorted(
            tools,
            key=lambda tool: str(
                (tool.get("function", {}) if isinstance(tool, dict) else {}).get("name", "")
            ),
        )

    @staticmethod
    def _is_reference_value(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and bool(value.get("__ref__"))
            and isinstance(value.get("uri", ""), str)
        )
