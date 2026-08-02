"""Tests for input node executor aggregate output compatibility."""

from __future__ import annotations

import pytest

from dan.executors.input import InputExecutor
from dan.models.control_flow import InputNode, InputVariable
from dan.models.ports import OutputPort


@pytest.mark.asyncio
async def test_input_executor_emits_variable_outputs_and_aggregate_input():
    node = InputNode(
        id="node_input",
        name="Inputs",
        variables=[
            InputVariable(name="ticker", default="AAPL"),
            InputVariable(name="horizon", default="3m"),
        ],
        output_ports=[
            OutputPort(name="input"),
            OutputPort(name="ticker"),
            OutputPort(name="horizon"),
        ],
    )
    result = await InputExecutor().execute(
        node=node,
        inputs={"ticker": "MSFT"},
        context=None,  # InputExecutor does not read context.
    )
    assert result.outputs["ticker"] == "MSFT"
    assert result.outputs["horizon"] == "3m"
    assert result.outputs["input"] == {"ticker": "MSFT", "horizon": "3m"}


@pytest.mark.asyncio
async def test_input_executor_does_not_override_explicit_input_variable():
    node = InputNode(
        id="node_input",
        name="Inputs",
        variables=[InputVariable(name="input", default="from-default")],
        output_ports=[OutputPort(name="input")],
    )
    result = await InputExecutor().execute(
        node=node,
        inputs={"input": "from-user"},
        context=None,  # InputExecutor does not read context.
    )
    assert result.outputs["input"] == "from-user"
