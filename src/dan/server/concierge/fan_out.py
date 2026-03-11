"""Fan-out concurrency utility for running independent async sub-tasks."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")

SubTask = Callable[[], Awaitable[Any]]

_PARALLEL_TASK_LIMIT = int(os.environ.get("DAN_PARALLEL_TASK_LIMIT", "8"))
_global_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(_PARALLEL_TASK_LIMIT)
    return _global_semaphore


TelemetryCallback = Callable[[str, float, bool], Awaitable[None] | None] | None


async def _run_task(
    task: SubTask,
    *,
    timeout_per: float | None = None,
    use_semaphore: bool = True,
) -> Any:
    """Invoke a sub-task safely so one bad task cannot block the rest."""
    try:
        coro = task()
    except Exception:
        logger.debug("fan_out task failed before awaiting", exc_info=True)
        raise
    sem = _get_semaphore() if use_semaphore else None
    if sem is not None:
        async with sem:
            if timeout_per is not None:
                return await asyncio.wait_for(coro, timeout=timeout_per)
            return await coro
    else:
        if timeout_per is not None:
            return await asyncio.wait_for(coro, timeout=timeout_per)
        return await coro


async def _timed_run_task(
    name: str,
    task: SubTask,
    *,
    timeout_per: float | None = None,
    use_semaphore: bool = True,
    telemetry_emit: TelemetryCallback = None,
) -> Any:
    start = time.monotonic()
    success = True
    try:
        result = await _run_task(
            task, timeout_per=timeout_per, use_semaphore=use_semaphore
        )
        if isinstance(result, Exception):
            success = False
        return result
    except Exception:
        success = False
        raise
    finally:
        duration_ms = (time.monotonic() - start) * 1000
        if telemetry_emit is not None:
            try:
                ret = telemetry_emit(name, duration_ms, success)
                if asyncio.iscoroutine(ret):
                    await ret
            except Exception:
                logger.debug("Telemetry emit failed for task %s", name, exc_info=True)


async def fan_out(
    tasks: list[SubTask],
    *,
    timeout_per: float | None = None,
    return_exceptions: bool = True,
) -> list[Any]:
    """Run independent async tasks concurrently. Failed tasks return their exception."""
    if not tasks:
        return []
    coros = [_run_task(task, timeout_per=timeout_per) for task in tasks]
    results = list(await asyncio.gather(*coros, return_exceptions=return_exceptions))
    if return_exceptions:
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.debug("fan_out task %d returned exception", idx, exc_info=result)
    return results


async def fan_out_dict(
    tasks: dict[str, SubTask],
    *,
    timeout_per: float | None = None,
    return_exceptions: bool = True,
    telemetry_emit: TelemetryCallback = None,
) -> dict[str, Any]:
    """Like fan_out but with named tasks. Returns dict of name -> result."""
    if not tasks:
        return {}
    keys = list(tasks.keys())
    coros = [
        _timed_run_task(
            name=k,
            task=tasks[k],
            timeout_per=timeout_per,
            telemetry_emit=telemetry_emit,
        )
        for k in keys
    ]
    results = list(await asyncio.gather(*coros, return_exceptions=return_exceptions))
    if return_exceptions:
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.debug("fan_out task %s returned exception", keys[idx], exc_info=result)
    return dict(zip(keys, results))
