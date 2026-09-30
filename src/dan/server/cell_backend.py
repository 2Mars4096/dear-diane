"""Agent V2 adapter for a bounded, tool-free Universal Cell brief."""
from __future__ import annotations
import asyncio
import json
import logging

from dan.providers import LLMProvider
from dan.providers.retrying_provider import RetryingLLMProvider, ProviderRetryPolicy
from dan.server.chat_v2 import AgentRunEvent
from dan.server.chat_v2_backend import AgentBackendRunResult
from dan.worker.brief import WorkerBrief, request_from_brief
from dan.worker.cell import build_cell
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.interfaces import CompletionResponse


class BriefCellAdapter:
    backend_name = 'brief_cell'

    def __init__(self, brief: WorkerBrief, model: str, provider: LLMProvider, *, timeout: float = 60, completion_options: dict | None = None, max_input_bytes: int = 131072, before_completion=None, max_model_calls: int = 2, charge_completion=None, billing=None):
        if brief.tool_policy.allowed_tool_ids:
            raise ValueError('This adapter accepts tool-free briefs only')
        if isinstance(provider, RetryingLLMProvider):
            provider = provider.with_retry_policy(ProviderRetryPolicy(max_attempts=1))
        self.brief, self.model, self.provider, self.timeout = brief, model, provider, timeout
        self.completion_options = completion_options or {}
        self.max_input_bytes = max_input_bytes
        self.before_completion = before_completion
        if not 1 <= max_model_calls <= 2:
            raise ValueError('A bounded brief permits one or two model calls')
        self.max_model_calls = max_model_calls
        self.charge_completion = charge_completion
        self.billing = billing

    async def run(self, request, emit_event, runtime=None):
        adapter = self
        calls = 0
        usage = {}

        class Completion:
            async def complete(self, call):
                nonlocal calls
                if calls >= adapter.max_model_calls or call.tools:
                    raise ValueError('Bounded cell call/tool limit exceeded')
                if runtime:
                    runtime.raise_if_interrupted('brief_cell.before_completion')
                messages = [{'role': 'system', 'content': call.system_prompt}, {'role': 'user', 'content': call.user_prompt}]
                if sum(len(message['content'].encode('utf-8')) for message in messages) > adapter.max_input_bytes:
                    raise ValueError('The extraction input exceeds its reserved budget size')
                if adapter.before_completion and not adapter.before_completion():
                    raise ValueError('This extraction is no longer authorized to spend')
                if adapter.charge_completion:
                    adapter.charge_completion(sum(len(message['content'].encode('utf-8')) for message in messages))
                receipt = adapter.billing.begin(sum(len(message['content'].encode('utf-8')) for message in messages)) if adapter.billing else None
                calls += 1
                try:
                    response = await adapter.provider.complete(
                        messages=messages,
                        model=adapter.model, temperature=0, max_tokens=4096, **adapter.completion_options,
                    )
                except Exception as exc:
                    # Do not log exception text, response bodies, credentials or prompts.
                    status = getattr(exc, 'status_code', None)
                    logging.getLogger(__name__).warning('Model completion failed: type=%s status=%s', type(exc).__name__, status if isinstance(status, int) else 'unknown')
                    if status == 429:
                        raise ValueError('The configured model provider is rate limited') from None
                    raise ValueError('The configured model provider is unavailable') from None
                if adapter.billing:
                    adapter.billing.record(receipt, response.provider_metadata or {})
                if response.tool_calls:
                    raise ValueError('The model requested a tool in a tool-free task')
                for key, value in (response.usage or {}).items():
                    if isinstance(value, int):
                        usage[key] = usage.get(key, 0) + value
                if runtime:
                    runtime.raise_if_interrupted('brief_cell.after_completion')
                return CompletionResponse(text=response.text)

        emit_event(AgentRunEvent(type='worker_started', summary='Reading the supplied evidence', source_event_type='brief_cell.started'))
        worker = build_cell(self.model, {'profile': 'deterministic', 'max_tokens': 4096}, self.brief.role.role_label)
        result = await asyncio.wait_for(WorkerCoreExecutor(completion_provider=Completion()).execute(worker, request_from_brief(self.brief)), timeout=self.timeout)
        parsed = result.outputs.get('result')
        summary = parsed if isinstance(parsed, str) else json.dumps(parsed, ensure_ascii=False)
        status = 'completed' if result.status == 'completed' else 'failed'
        emit_event(AgentRunEvent(type=status, summary='Evidence extraction complete; review the proposed details' if status == 'completed' else 'The model could not produce a valid result', source_event_type='brief_cell.finished', token_usage_total=usage))
        return AgentBackendRunResult(status=status, backend=self.backend_name, summary=summary,
            token_usage=usage, raw_result={'model_calls': calls, 'result': parsed})
