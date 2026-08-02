from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys

import httpx
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "measure_raw_llm_latency",
    Path(__file__).resolve().parents[2] / "scripts" / "measure_raw_llm_latency.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


class _DelayedTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        payload = (await request.aread()).decode("utf-8")
        if '"stream":false' not in payload.lower():
            raise AssertionError("expected non-streaming chat completions payload")
        await asyncio.sleep(0.01)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": "gpt-latency-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9},
            },
        )


class _AsyncChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, delay_seconds: float = 0.0) -> None:
        self._chunks = list(chunks)
        self._delay_seconds = float(delay_seconds)

    async def __aiter__(self):
        for chunk in self._chunks:
            if self._delay_seconds > 0:
                await asyncio.sleep(self._delay_seconds)
            yield chunk

    async def aclose(self) -> None:
        return None


class _StreamingTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        payload = (await request.aread()).decode("utf-8")
        if '"stream":true' not in payload.lower():
            raise AssertionError("expected streaming chat completions payload")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_AsyncChunkStream(
                [
                    b'data: {"id":"chatcmpl-stream","model":"gpt-latency-test","choices":[{"delta":{"content":"O"}}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"K"}}]}\n\n',
                    b'data: {"usage":{"prompt_tokens":8,"completion_tokens":1,"total_tokens":9},"choices":[]}\n\n',
                    b"data: [DONE]\n\n",
                ],
                delay_seconds=0.01,
            ),
        )


class _ReasoningStreamingTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        payload = (await request.aread()).decode("utf-8")
        if '"stream":true' not in payload.lower():
            raise AssertionError("expected streaming chat completions payload")
        if '"thinking":{"type":"enabled"}' not in payload.replace(" ", "").lower():
            raise AssertionError("expected explicit enabled thinking payload")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_AsyncChunkStream(
                [
                    b'data: {"id":"chatcmpl-stream","model":"gpt-latency-test","choices":[{"delta":{"reasoning_content":"Think"}}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n',
                    b'data: {"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11},"choices":[]}\n\n',
                    b"data: [DONE]\n\n",
                ],
                delay_seconds=0.01,
            ),
        )


@pytest.mark.asyncio
async def test_probe_latency_reports_parallel_summary() -> None:
    report = await _MODULE.probe_latency(
        base_url="https://example.test/v1",
        endpoint_path="/chat/completions",
        api_key="test-key",
        payload={
            "model": "gpt-latency-test",
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": False,
        },
        count=4,
        concurrency=4,
        timeout=5.0,
        transport=_DelayedTransport(),
    )

    assert report["request"]["count"] == 4
    assert report["request"]["concurrency"] == 4
    assert report["summary"]["success_count"] == 4
    assert report["summary"]["error_count"] == 0
    assert report["summary"]["avg_ms"] is not None
    assert report["summary"]["median_ms"] is not None
    assert report["summary"]["p95_ms"] is not None
    assert len(report["calls"]) == 4
    assert all(call["ok"] is True for call in report["calls"])
    assert all(call["status_code"] == 200 for call in report["calls"])
    assert all(call["response_model"] == "gpt-latency-test" for call in report["calls"])
    assert all(call["total_tokens"] == 9 for call in report["calls"])


@pytest.mark.asyncio
async def test_probe_latency_reports_streaming_first_chunk_and_text() -> None:
    report = await _MODULE.probe_latency(
        base_url="https://example.test/v1",
        endpoint_path="/chat/completions",
        api_key="test-key",
        payload={
            "model": "gpt-latency-test",
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        count=2,
        concurrency=2,
        timeout=5.0,
        transport=_StreamingTransport(),
    )

    assert report["request"]["stream"] is True
    assert report["summary"]["success_count"] == 2
    assert report["summary"]["avg_ms"] is not None
    assert report["summary"]["avg_first_chunk_ms"] is not None
    assert report["summary"]["avg_first_text_ms"] is not None
    assert report["summary"]["avg_first_answer_text_ms"] is not None
    assert len(report["calls"]) == 2
    assert all(call["ok"] is True for call in report["calls"])
    assert all(call["first_chunk_ms"] is not None for call in report["calls"])
    assert all(call["first_text_ms"] is not None for call in report["calls"])
    assert all(call["response_model"] == "gpt-latency-test" for call in report["calls"])
    assert all(call["total_tokens"] == 9 for call in report["calls"])
    assert all(call["output_chars"] == 2 for call in report["calls"])
    assert all(call["reasoning_chars"] == 0 for call in report["calls"])


@pytest.mark.asyncio
async def test_probe_latency_treats_reasoning_content_as_first_visible_text() -> None:
    args = _MODULE.build_parser().parse_args(
        [
            "--model",
            "gpt-latency-test",
            "--stream",
            "--thinking-mode",
            "enabled",
        ]
    )
    payload = _MODULE._build_payload(args)

    report = await _MODULE.probe_latency(
        base_url="https://example.test/v1",
        endpoint_path="/chat/completions",
        api_key="test-key",
        payload=payload,
        count=1,
        concurrency=1,
        timeout=5.0,
        transport=_ReasoningStreamingTransport(),
    )

    assert report["request"]["thinking_mode"] == "enabled"
    assert report["summary"]["success_count"] == 1
    assert report["summary"]["avg_first_text_ms"] is not None
    assert report["summary"]["avg_first_answer_text_ms"] is not None
    call = report["calls"][0]
    assert call["first_text_ms"] is not None
    assert call["first_answer_text_ms"] is not None
    assert call["first_text_ms"] <= call["first_answer_text_ms"]
    assert call["output_chars"] == 2
    assert call["reasoning_chars"] == 5
