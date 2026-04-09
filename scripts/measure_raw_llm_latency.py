#!/usr/bin/env python3
"""Measure raw OpenAI-compatible chat-completions latency in parallel."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Sequence

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass


def _resolve_value(explicit: str | None, env_keys: Sequence[str], default: str = "") -> str:
    text = str(explicit or "").strip()
    if text:
        return text
    for key in env_keys:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return default


def _resolve_url(base_url: str, endpoint_path: str) -> str:
    normalized_base = str(base_url or "").rstrip("/")
    normalized_path = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
    return f"{normalized_base}{normalized_path}"


def _build_payload(args: argparse.Namespace) -> dict[str, Any]:
    thinking = _thinking_payload(str(args.thinking_mode or "auto"))
    if args.payload_file:
        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("--payload-file must contain one JSON object")
        if args.model:
            payload["model"] = args.model
        if thinking is not None and "thinking" not in payload:
            payload["thinking"] = thinking
        if args.stream:
            payload["stream"] = True
            payload.setdefault("stream_options", {"include_usage": True})
        return payload

    payload: dict[str, Any] = {
        "model": args.model,
        "messages": [],
        "temperature": float(args.temperature),
        "max_tokens": int(args.max_tokens),
        "stream": bool(args.stream),
    }
    if args.stream:
        payload["stream_options"] = {"include_usage": True}
    if thinking is not None:
        payload["thinking"] = thinking
    system_prompt = str(args.system_prompt or "").strip()
    if system_prompt:
        payload["messages"].append({"role": "system", "content": system_prompt})
    payload["messages"].append({"role": "user", "content": str(args.prompt)})
    return payload


def _parse_headers(raw_headers: Sequence[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw in raw_headers:
        text = str(raw or "").strip()
        if not text:
            continue
        if "=" not in text:
            raise ValueError(f"Invalid header '{text}'. Use KEY=VALUE.")
        key, value = text.split("=", 1)
        headers[key.strip()] = value.strip()
    return headers


def _thinking_payload(mode: str) -> dict[str, str] | None:
    normalized = str(mode or "auto").strip().lower()
    if normalized == "auto":
        return None
    if normalized not in {"enabled", "disabled"}:
        raise ValueError(f"Unsupported thinking mode: {mode}")
    return {"type": normalized}


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[rank]


@dataclass(slots=True)
class CallMeasurement:
    index: int
    ok: bool
    duration_ms: float
    first_chunk_ms: float | None = None
    first_text_ms: float | None = None
    first_answer_text_ms: float | None = None
    status_code: int | None = None
    response_model: str | None = None
    total_tokens: int | None = None
    output_chars: int = 0
    reasoning_chars: int = 0
    error: str | None = None


async def probe_latency(
    *,
    base_url: str,
    endpoint_path: str,
    api_key: str,
    payload: dict[str, Any],
    count: int = 10,
    concurrency: int | None = None,
    timeout: float = 60.0,
    headers: dict[str, str] | None = None,
    auth_header: str = "Authorization",
    auth_prefix: str = "Bearer ",
    transport: httpx.AsyncBaseTransport | None = None,
    stream_printer: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    if count <= 0:
        raise ValueError("count must be >= 1")
    resolved_concurrency = max(1, min(int(concurrency or count), int(count)))
    url = _resolve_url(base_url, endpoint_path)
    request_headers = {
        "Content-Type": "application/json",
        **dict(headers or {}),
    }
    if api_key:
        request_headers[auth_header] = f"{auth_prefix}{api_key}" if auth_prefix else api_key

    semaphore = asyncio.Semaphore(resolved_concurrency)
    measurements: list[CallMeasurement] = []
    started_at = perf_counter()

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        async def _one_call(index: int) -> CallMeasurement:
            async with semaphore:
                call_started = perf_counter()
                try:
                    if payload.get("stream"):
                        return await _stream_one_call(
                            client=client,
                            url=url,
                            headers=request_headers,
                            payload=payload,
                            index=index,
                            call_started=call_started,
                            stream_printer=stream_printer,
                        )
                    response = await client.post(url, headers=request_headers, json=payload)
                    return _response_measurement(
                        index=index,
                        response=response,
                        duration_ms=(perf_counter() - call_started) * 1000.0,
                    )
                except Exception as exc:
                    return CallMeasurement(
                        index=index,
                        ok=False,
                        duration_ms=(perf_counter() - call_started) * 1000.0,
                        error=f"{type(exc).__name__}: {exc}",
                    )

        measurements = list(await asyncio.gather(*[_one_call(index) for index in range(1, count + 1)]))

    wall_ms = (perf_counter() - started_at) * 1000.0
    successes = [item.duration_ms for item in measurements if item.ok]
    first_chunk_successes = [
        item.first_chunk_ms for item in measurements if item.ok and item.first_chunk_ms is not None
    ]
    first_text_successes = [
        item.first_text_ms for item in measurements if item.ok and item.first_text_ms is not None
    ]
    failures = [item for item in measurements if not item.ok]

    return {
        "request": {
            "url": url,
            "count": int(count),
            "concurrency": resolved_concurrency,
            "timeout_seconds": float(timeout),
            "model": payload.get("model"),
            "stream": bool(payload.get("stream")),
            "thinking_mode": (
                str((payload.get("thinking") or {}).get("type") or "auto")
                if isinstance(payload.get("thinking"), dict)
                else "auto"
            ),
        },
        "summary": {
            "success_count": len(successes),
            "error_count": len(failures),
            "wall_time_ms": round(wall_ms, 2),
            "avg_ms": round(sum(successes) / len(successes), 2) if successes else None,
            "median_ms": round(_percentile(successes, 0.5), 2) if successes else None,
            "p95_ms": round(_percentile(successes, 0.95), 2) if successes else None,
            "min_ms": round(min(successes), 2) if successes else None,
            "max_ms": round(max(successes), 2) if successes else None,
            "avg_first_chunk_ms": (
                round(sum(first_chunk_successes) / len(first_chunk_successes), 2)
                if first_chunk_successes
                else None
            ),
            "median_first_chunk_ms": (
                round(_percentile(first_chunk_successes, 0.5), 2)
                if first_chunk_successes
                else None
            ),
            "p95_first_chunk_ms": (
                round(_percentile(first_chunk_successes, 0.95), 2)
                if first_chunk_successes
                else None
            ),
            "avg_first_text_ms": (
                round(sum(first_text_successes) / len(first_text_successes), 2)
                if first_text_successes
                else None
            ),
            "median_first_text_ms": (
                round(_percentile(first_text_successes, 0.5), 2)
                if first_text_successes
                else None
            ),
            "p95_first_text_ms": (
                round(_percentile(first_text_successes, 0.95), 2)
                if first_text_successes
                else None
            ),
            "avg_first_answer_text_ms": (
                round(sum(item.first_answer_text_ms for item in measurements if item.ok and item.first_answer_text_ms is not None) / len([item for item in measurements if item.ok and item.first_answer_text_ms is not None]), 2)
                if any(item.ok and item.first_answer_text_ms is not None for item in measurements)
                else None
            ),
            "median_first_answer_text_ms": (
                round(_percentile([item.first_answer_text_ms for item in measurements if item.ok and item.first_answer_text_ms is not None], 0.5), 2)
                if any(item.ok and item.first_answer_text_ms is not None for item in measurements)
                else None
            ),
            "p95_first_answer_text_ms": (
                round(_percentile([item.first_answer_text_ms for item in measurements if item.ok and item.first_answer_text_ms is not None], 0.95), 2)
                if any(item.ok and item.first_answer_text_ms is not None for item in measurements)
                else None
            ),
        },
        "calls": [asdict(item) for item in sorted(measurements, key=lambda item: item.index)],
    }


def _response_measurement(*, index: int, response: httpx.Response, duration_ms: float) -> CallMeasurement:
    response_json: dict[str, Any] = {}
    try:
        response_json = response.json()
    except Exception:
        response_json = {}
    usage = response_json.get("usage") if isinstance(response_json, dict) else {}
    output_text = _chat_completion_text(response_json)
    return CallMeasurement(
        index=index,
        ok=response.is_success,
        duration_ms=duration_ms,
        status_code=response.status_code,
        response_model=str(response_json.get("model") or "") or None,
        total_tokens=(
            int(usage.get("total_tokens"))
            if isinstance(usage, dict) and usage.get("total_tokens") is not None
            else None
        ),
        output_chars=len(output_text),
        error=None if response.is_success else _extract_error(response_json, response),
    )


def _chat_completion_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    return ""


async def _iter_sse_payloads(response: httpx.Response):
    data_lines: list[str] = []
    async for raw_line in response.aiter_lines():
        line = str(raw_line or "")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        yield "\n".join(data_lines)


def _stream_delta_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice, dict) else {}
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        return "".join(text_parts)
    return ""


def _stream_delta_reasoning_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice, dict) else {}
    if not isinstance(delta, dict):
        return ""
    reasoning = delta.get("reasoning_content")
    if isinstance(reasoning, str):
        return reasoning
    if isinstance(reasoning, list):
        text_parts: list[str] = []
        for item in reasoning:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        return "".join(text_parts)
    return ""


async def _stream_one_call(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    index: int,
    call_started: float,
    stream_printer: Callable[[int, str], None] | None = None,
) -> CallMeasurement:
    first_chunk_ms: float | None = None
    first_text_ms: float | None = None
    first_answer_text_ms: float | None = None
    response_model: str | None = None
    total_tokens: int | None = None
    accumulated = ""
    reasoning_accumulated = ""
    status_code: int | None = None

    async with client.stream("POST", url, headers=headers, json=payload) as response:
        status_code = response.status_code
        if not response.is_success:
            error_text = (await response.aread()).decode("utf-8", errors="replace")
            duration_ms = (perf_counter() - call_started) * 1000.0
            return CallMeasurement(
                index=index,
                ok=False,
                duration_ms=duration_ms,
                status_code=status_code,
                error=f"{status_code}: {error_text[:400].strip() or 'request failed'}",
            )

        async for raw_payload in _iter_sse_payloads(response):
            if raw_payload == "[DONE]":
                break
            if first_chunk_ms is None:
                first_chunk_ms = (perf_counter() - call_started) * 1000.0
            parsed = json.loads(raw_payload)
            if response_model is None:
                response_model = str(parsed.get("model") or "") or None
            usage = parsed.get("usage")
            if isinstance(usage, dict) and usage.get("total_tokens") is not None:
                total_tokens = int(usage.get("total_tokens"))
            answer_delta = _stream_delta_text(parsed)
            reasoning_delta = _stream_delta_reasoning_text(parsed)
            visible_delta = answer_delta or reasoning_delta
            if visible_delta and first_text_ms is None:
                first_text_ms = (perf_counter() - call_started) * 1000.0
            if answer_delta:
                accumulated += answer_delta
                if first_answer_text_ms is None:
                    first_answer_text_ms = (perf_counter() - call_started) * 1000.0
            if reasoning_delta:
                reasoning_accumulated += reasoning_delta
            if visible_delta and stream_printer is not None:
                stream_printer(index, visible_delta)

    return CallMeasurement(
        index=index,
        ok=True,
        duration_ms=(perf_counter() - call_started) * 1000.0,
        first_chunk_ms=first_chunk_ms,
        first_text_ms=first_text_ms,
        first_answer_text_ms=first_answer_text_ms,
        status_code=status_code,
        response_model=response_model,
        total_tokens=total_tokens,
        output_chars=len(accumulated),
        reasoning_chars=len(reasoning_accumulated),
    )


def _extract_error(response_json: dict[str, Any], response: httpx.Response) -> str:
    error = response_json.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        if message:
            return f"{response.status_code}: {message}"
    text = response.text.strip()
    if text:
        return f"{response.status_code}: {text[:400]}"
    return f"{response.status_code}: request failed"


def _print_human_report(report: dict[str, Any]) -> None:
    request = dict(report.get("request") or {})
    summary = dict(report.get("summary") or {})
    calls = list(report.get("calls") or [])

    print("Raw LLM Latency Probe")
    print(f"url: {request.get('url')}")
    print(f"model: {request.get('model')}")
    print(f"thinking: {request.get('thinking_mode')}")
    print(
        "calls: "
        f"{request.get('count')} total, "
        f"concurrency={request.get('concurrency')}, "
        f"success={summary.get('success_count')}, "
        f"errors={summary.get('error_count')}"
    )
    print(
        "latency: "
        f"avg={summary.get('avg_ms')} ms, "
        f"median={summary.get('median_ms')} ms, "
        f"p95={summary.get('p95_ms')} ms, "
        f"min={summary.get('min_ms')} ms, "
        f"max={summary.get('max_ms')} ms"
    )
    if request.get("stream"):
        print(
            "stream: "
            f"first_chunk_avg={summary.get('avg_first_chunk_ms')} ms, "
            f"first_chunk_median={summary.get('median_first_chunk_ms')} ms, "
            f"first_chunk_p95={summary.get('p95_first_chunk_ms')} ms, "
            f"first_visible_text_avg={summary.get('avg_first_text_ms')} ms, "
            f"first_visible_text_median={summary.get('median_first_text_ms')} ms, "
            f"first_visible_text_p95={summary.get('p95_first_text_ms')} ms, "
            f"first_answer_text_avg={summary.get('avg_first_answer_text_ms')} ms, "
            f"first_answer_text_median={summary.get('median_first_answer_text_ms')} ms, "
            f"first_answer_text_p95={summary.get('p95_first_answer_text_ms')} ms"
        )
    print(f"wall time: {summary.get('wall_time_ms')} ms")
    print("\nPer call:")
    for item in calls:
        status = "ok" if item.get("ok") else "error"
        line = f"- #{item.get('index'):02d} {status} {item.get('duration_ms')} ms"
        if item.get("first_chunk_ms") is not None:
            line += f" first_chunk={item.get('first_chunk_ms')} ms"
        if item.get("first_text_ms") is not None:
            line += f" first_visible_text={item.get('first_text_ms')} ms"
        if item.get("first_answer_text_ms") is not None:
            line += f" first_answer_text={item.get('first_answer_text_ms')} ms"
        if item.get("status_code") is not None:
            line += f" status={item.get('status_code')}"
        if item.get("total_tokens") is not None:
            line += f" tokens={item.get('total_tokens')}"
        if item.get("output_chars") is not None:
            line += f" chars={item.get('output_chars')}"
        if item.get("reasoning_chars") is not None:
            line += f" reasoning_chars={item.get('reasoning_chars')}"
        if item.get("error"):
            line += f" error={item.get('error')}"
        print(line)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure raw OpenAI-compatible chat-completions latency with parallel calls.",
    )
    parser.add_argument("--model", default=None, help="Model id. Falls back to DAN_MODEL / DAN_LLM_MODEL.")
    parser.add_argument("--api-key", default=None, help="API key. Falls back to DAN_LLM_API_KEY / OPENAI_API_KEY.")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL ending at the API root, for example https://api.openai.com/v1.",
    )
    parser.add_argument(
        "--endpoint-path",
        default="/chat/completions",
        help="Endpoint path appended to --base-url. Defaults to /chat/completions.",
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly OK.",
        help="User prompt for the default request body.",
    )
    parser.add_argument(
        "--system-prompt",
        default="You are a latency probe. Respond briefly.",
        help="System prompt for the default request body.",
    )
    parser.add_argument(
        "--payload-file",
        default=None,
        help="Optional JSON file used as the exact request payload instead of the default prompt-based body.",
    )
    parser.add_argument("--count", type=int, default=10, help="Total request count. Defaults to 10.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Concurrent in-flight request count. Defaults to --count.",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-request timeout in seconds.")
    parser.add_argument("--temperature", type=float, default=0.0, help="Temperature for the default payload.")
    parser.add_argument("--max-tokens", type=int, default=16, help="max_tokens for the default payload.")
    parser.add_argument(
        "--thinking-mode",
        choices=["auto", "enabled", "disabled"],
        default="auto",
        help="Set provider thinking mode when the backend supports it. Defaults to auto.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Use streaming chat completions and report first-chunk / first-text latency.",
    )
    parser.add_argument(
        "--print-stream",
        action="store_true",
        help="When --stream is enabled, print streamed text chunks live with per-call prefixes.",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="Additional request header in KEY=VALUE form. Repeat as needed.",
    )
    parser.add_argument(
        "--auth-header",
        default="Authorization",
        help="Header name used for --api-key. Defaults to Authorization.",
    )
    parser.add_argument(
        "--auth-prefix",
        default="Bearer ",
        help="Prefix added before the API key when auth is injected. Defaults to 'Bearer '.",
    )
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _load_env()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    args.model = _resolve_value(args.model, ("DAN_MODEL", "DAN_LLM_MODEL"))
    args.api_key = _resolve_value(args.api_key, ("DAN_LLM_API_KEY", "OPENAI_API_KEY"))
    args.base_url = _resolve_value(
        args.base_url,
        ("DAN_LLM_BASE_URL", "DAN_BASE_URL", "OPENAI_BASE_URL"),
        default="https://api.openai.com/v1",
    )
    if not args.model:
        parser.error("model is required via --model, DAN_MODEL, or DAN_LLM_MODEL")

    payload = _build_payload(args)
    report = asyncio.run(
        probe_latency(
            base_url=str(args.base_url),
            endpoint_path=str(args.endpoint_path),
            api_key=str(args.api_key),
            payload=payload,
            count=int(args.count),
            concurrency=args.concurrency,
            timeout=float(args.timeout),
            headers=_parse_headers(args.header),
            auth_header=str(args.auth_header),
            auth_prefix=str(args.auth_prefix),
            stream_printer=(
                (lambda index, delta: print(f"[stream #{index:02d}] {delta}", end="", flush=True))
                if args.stream and args.print_stream
                else None
            ),
        )
    )
    if args.stream and args.print_stream:
        print()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        _print_human_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
