from __future__ import annotations

import asyncio

import pytest

from dan.engine.behavior_store import ParameterDecisionLogger


class _AsyncStore:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(self, event: dict[str, object]) -> None:
        self.events.append(event)


def test_parameter_decision_logger_sync_caller_runs_async_record():
    store = _AsyncStore()
    logger = ParameterDecisionLogger(store)

    logger.log_decision(
        "heuristics/reuse.reuse_threshold",
        0.8,
        decision="reuse",
        outcome="accepted",
        metadata={"score": 0.92},
    )

    assert len(store.events) == 1
    event = store.events[0]
    assert event["event_type"] == "parameter_decision"
    assert event["parameter_key"] == "heuristics/reuse.reuse_threshold"
    assert event["parameter_value"] == "0.8"
    assert event["metadata"]["decision"] == "reuse"
    assert event["metadata"]["outcome"] == "accepted"
    assert event["metadata"]["score"] == 0.92


@pytest.mark.asyncio
async def test_parameter_decision_logger_running_loop_schedules_async_record():
    store = _AsyncStore()
    logger = ParameterDecisionLogger(store)

    logger.log_decision(
        "heuristics/reuse.reuse_threshold",
        0.7,
        decision="adapt",
        metadata={"score": 0.74},
    )
    await asyncio.sleep(0)

    assert len(store.events) == 1
    event = store.events[0]
    assert event["event_type"] == "parameter_decision"
    assert event["parameter_value"] == "0.7"
    assert event["metadata"]["decision"] == "adapt"
    assert event["metadata"]["score"] == 0.74


def test_parameter_decision_logger_append_sink_preserves_legacy_payload():
    sink: list[dict] = []

    class _AppendStore:
        def append(self, event: dict) -> None:
            sink.append(event)

    logger = ParameterDecisionLogger(_AppendStore())
    logger.log_decision(
        "heuristics/reuse.reuse_threshold",
        0.6,
        decision="generate",
        metadata={"score": 0.2},
    )

    assert len(sink) == 1
    assert sink[0]["type"] == "parameter_decision"
    assert sink[0]["decision"] == "generate"
    assert sink[0]["metadata"]["score"] == 0.2
