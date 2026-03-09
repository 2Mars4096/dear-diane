"""Fan-out concurrency utility for running independent async sub-tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")

SubTask = Callable[[], Awaitable[Any]]


async def _run_task(task: SubTask, *, timeout_per: float | None = None) -> Any:
    """Invoke a sub-task safely so one bad task cannot block the rest."""
    try:
        coro = task()
    except Exception:
        logger.debug("fan_out task failed before awaiting", exc_info=True)
        raise
    if timeout_per is not None:
        return await asyncio.wait_for(coro, timeout=timeout_per)
    return await coro


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
) -> dict[str, Any]:
    """Like fan_out but with named tasks. Returns dict of name -> result."""
    if not tasks:
        return {}
    keys = list(tasks.keys())
    results = await fan_out(
        [tasks[k] for k in keys],
        timeout_per=timeout_per,
        return_exceptions=return_exceptions,
    )
    return dict(zip(keys, results))
