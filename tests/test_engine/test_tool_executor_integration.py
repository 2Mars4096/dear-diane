"""ToolExecutor integration tests — end-to-end tool execution via Engine and direct ToolExecutor.

Covers: custom tool via Engine, built-in file_read via ToolExecutor, tool-not-found error,
and retry policy on transient failures.
"""

from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from dan.engine import Engine, EngineConfig
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.executor import ExecutionContext, ExecutorRegistry
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.control_flow import InputNode, InputVariable
from dan.models.edges import DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import RetryPolicy, ToolOperator
from dan.models.ports import InputPort, OutputPort


def _dummy_config() -> EngineConfig:
    return EngineConfig(
        llm_api_key="test",
        llm_base_url="http://localhost:1",
        llm_default_model="test",
        checkpoint_enabled=False,
    )


# ---------------------------------------------------------------------------
# 1. Simple tool execution via Engine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_tool_execution_via_engine():
    """Build graph: InputNode → ToolOperator (custom tool) → output; run via Engine; assert result."""
    tool_registry = ToolRegistry()

    async def upper_case(**kwargs) -> dict:
        val = kwargs.get("input") or kwargs.get("text") or ""
        return {"result": str(val).upper()}

    tool_registry.register("upper_case", upper_case)

    exec_registry = ExecutorRegistry()
    exec_registry.register("tool_operator", ToolExecutor(tool_registry))

    inp = InputNode(
        id="inp",
        name="Input",
        variables=[InputVariable(name="text", type="string")],
        output_ports=[OutputPort(name="text"), OutputPort(name="input")],
    )
    tool = ToolOperator(
        id="upper",
        name="Upper",
        tool_id="upper_case",
        tool_config={},
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="result")],
    )

    graph = Graph(
        metadata=GraphMetadata(name="tool_test"),
        nodes=[inp, tool],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="inp",
                source_port="text",
                target_node_id="upper",
                target_port="input",
            ),
        ],
        entry_points=["inp"],
        exit_points=["upper"],
    )

    engine = Engine(
        config=_dummy_config(),
        executor_registry=exec_registry,
        checkpoint_store=NullCheckpointStore(),
    )
    result = await engine.run(graph, inputs={"text": "hello"})

    assert result.success
    # ToolExecutor exposes full dict on "result" port; inner "result" key holds the value
    assert result.outputs.get("result", {}).get("result") == "HELLO"


# ---------------------------------------------------------------------------
# 2. Built-in tool (file_read) via ToolExecutor directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_read_via_tool_executor_directly():
    """Create temp file, register file_read, execute via ToolExecutor; assert output contains content."""
    from dan.tools.file_read import file_read

    # Create file in cwd so file_read's validate_path (workspace root) can resolve it
    tmp_path = Path.cwd() / "test_tool_executor_integration_temp.txt"
    tmp_path.write_text("hello from temp file\nline two")

    try:
        rel_path = tmp_path.name

        registry = ToolRegistry()
        registry.register("file_read", file_read)

        node = ToolOperator(
            id="reader",
            name="Reader",
            tool_id="file_read",
            tool_config={"path": rel_path},
            input_ports=[],
            output_ports=[OutputPort(name="content"), OutputPort(name="result")],
        )

        state = ExecutionState(Graph(metadata=GraphMetadata(name="x"), nodes=[], edges=[]), "run1")
        context = ExecutionContext(
            state=state,
            config=_dummy_config(),
            shared_context=SharedContextStore({}),
            artifacts=ArtifactStore(),
            local_state=LocalStateManager(),
        )

        executor = ToolExecutor(registry)
        result = await executor.execute(node, inputs={}, context=context)

        assert result.status == NodeStatus.COMPLETED
        assert "content" in result.outputs
        assert "hello from temp file" in result.outputs["content"]
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_read_accepts_file_path_alias():
    from dan.tools.file_read import file_read

    tmp_path = Path.cwd() / "test_tool_executor_file_read_alias_temp.txt"
    tmp_path.write_text("hello from alias\n", encoding="utf-8")

    try:
        result = await file_read(file_path=tmp_path.name)
        assert "hello from alias" in result["content"]
        assert result["path"] == tmp_path.name
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_read_allows_large_file_line_ranges():
    from dan.tools.file_read import MAX_FILE_SIZE, file_read

    tmp_path = Path.cwd() / "test_tool_executor_large_file_read_temp.txt"
    line = "0123456789abcdefghijklmnopqrstuvwxyz\n"
    line_count = (MAX_FILE_SIZE // len(line)) + 5000
    tmp_path.write_text(line * line_count, encoding="utf-8")

    try:
        result = await file_read(
            path=tmp_path.name,
            start_line=100,
            end_line=102,
        )
        assert result["path"] == tmp_path.name
        assert result["line_count"] == 3
        assert result["content"] == line * 3
        assert result["size"] == len((line * 3).encode("utf-8"))
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_write_accepts_file_path_alias():
    from dan.tools.file_write import file_write

    tmp_path = Path.cwd() / "test_tool_executor_file_write_alias_temp.txt"
    tmp_path.unlink(missing_ok=True)

    try:
        result = await file_write(file_path=tmp_path.name, content="hello write alias\n")
        assert result["path"] == tmp_path.name
        assert tmp_path.read_text(encoding="utf-8") == "hello write alias\n"
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_replaces_a_line_range():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_temp.txt"
    tmp_path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    try:
        result = await file_edit(
            path=tmp_path.name,
            start_line=2,
            end_line=2,
            content="BETA patched\n",
            mode="replace",
        )
        assert result["path"] == tmp_path.name
        assert result["mode"] == "replace"
        assert tmp_path.read_text(encoding="utf-8") == "alpha\nBETA patched\ngamma\n"
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_treats_repeated_identical_replace_as_noop():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_noop_repeat_temp.txt"
    tmp_path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    try:
        await file_edit(
            path=tmp_path.name,
            start_line=2,
            end_line=2,
            content="BETA patched\n",
            mode="replace",
        )
        second_result = await file_edit(
            path=tmp_path.name,
            start_line=2,
            end_line=2,
            content="BETA patched\n",
            mode="replace",
        )
        assert second_result["path"] == tmp_path.name
        assert second_result["mode"] == "replace"
        assert second_result["total_lines_before"] == 3
        assert second_result["total_lines_after"] == 3
        assert tmp_path.read_text(encoding="utf-8") == "alpha\nBETA patched\ngamma\n"
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_accepts_file_path_alias():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_alias_temp.txt"
    tmp_path.write_text("one\ntwo\n", encoding="utf-8")

    try:
        result = await file_edit(
            file_path=tmp_path.name,
            start_line=1,
            content="zero\n",
            mode="insert_before",
        )
        assert result["path"] == tmp_path.name
        assert tmp_path.read_text(encoding="utf-8") == "zero\none\ntwo\n"
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_supports_batched_non_overlapping_edits():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_batch_temp.txt"
    tmp_path.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")

    try:
        result = await file_edit(
            path=tmp_path.name,
            edits=[
                {
                    "start_line": 2,
                    "end_line": 2,
                    "content": "BETA patched\n",
                    "mode": "replace",
                },
                {
                    "start_line": 4,
                    "content": "omega\n",
                    "mode": "insert_after",
                },
            ],
        )
        assert result["path"] == tmp_path.name
        assert result["mode"] == "batch"
        assert result["edit_count"] == 2
        assert tmp_path.read_text(encoding="utf-8") == (
            "alpha\nBETA patched\ngamma\ndelta\nomega\n"
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_infers_multiline_replace_range_from_content():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_multiline_temp.txt"
    tmp_path.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")

    try:
        result = await file_edit(
            path=tmp_path.name,
            start_line=2,
            content="BETA patched\nGAMMA patched\n",
            mode="replace",
        )
        assert result["path"] == tmp_path.name
        assert result["mode"] == "replace"
        assert result["end_line"] == 3
        assert tmp_path.read_text(encoding="utf-8") == (
            "alpha\nBETA patched\nGAMMA patched\ndelta\n"
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_accepts_old_string_new_string_compatibility_args():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_compat_temp.txt"
    tmp_path.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")

    try:
        result = await file_edit(
            path=tmp_path.name,
            old_string="beta\ngamma\n",
            new_string="BETA patched\nGAMMA patched\n",
        )
        assert result["path"] == tmp_path.name
        assert result["mode"] == "replace"
        assert result["start_line"] == 2
        assert result["end_line"] == 3
        assert tmp_path.read_text(encoding="utf-8") == (
            "alpha\nBETA patched\nGAMMA patched\ndelta\n"
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_preserves_line_boundary_when_content_omits_final_newline():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_newline_temp.txt"
    tmp_path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    try:
        result = await file_edit(
            path=tmp_path.name,
            start_line=2,
            end_line=2,
            content="BETA patched",
            mode="replace",
        )
        assert result["path"] == tmp_path.name
        assert result["end_line"] == 2
        assert tmp_path.read_text(encoding="utf-8") == "alpha\nBETA patched\ngamma\n"
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_reinterprets_single_line_anchor_replace_as_insert_after():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_anchor_insert_temp.txt"
    tmp_path.write_text("def thing():\n    body()\n", encoding="utf-8")

    try:
        result = await file_edit(
            path=tmp_path.name,
            start_line=1,
            end_line=1,
            content="def thing():\n    guard()\n",
            mode="replace",
        )
        assert result["path"] == tmp_path.name
        assert result["mode"] == "insert_after"
        assert result["start_line"] == 1
        assert result["end_line"] == 1
        assert tmp_path.read_text(encoding="utf-8") == "def thing():\n    guard()\n    body()\n"
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_reinterprets_off_by_one_anchor_replace_as_insert_after():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_off_by_one_anchor_temp.txt"
    tmp_path.write_text("alpha\nheader\nbody\ntrailer\n", encoding="utf-8")

    try:
        result = await file_edit(
            path=tmp_path.name,
            start_line=3,
            end_line=4,
            content="header\nguard\nbody\n",
            mode="replace",
        )
        assert result["path"] == tmp_path.name
        assert result["mode"] == "insert_after"
        assert result["start_line"] == 2
        assert result["end_line"] == 2
        assert tmp_path.read_text(encoding="utf-8") == "alpha\nheader\nguard\nbody\ntrailer\n"
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_reinterprets_previous_line_plus_target_prefix_as_insert_after():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_target_prefix_temp.txt"
    tmp_path.write_text("alpha\nheader\nbody1\nbody2\ntrailer\n", encoding="utf-8")

    try:
        result = await file_edit(
            path=tmp_path.name,
            start_line=3,
            end_line=4,
            content="header\nguard1\nguard2\nbody1\nbody2\n",
            mode="replace",
        )
        assert result["path"] == tmp_path.name
        assert result["mode"] == "insert_after"
        assert result["start_line"] == 2
        assert result["end_line"] == 2
        assert (
            tmp_path.read_text(encoding="utf-8")
            == "alpha\nheader\nguard1\nguard2\nbody1\nbody2\ntrailer\n"
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_rejects_overlapping_batched_edits():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_batch_overlap_temp.txt"
    tmp_path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    try:
        with pytest.raises(
            ValueError,
            match="batched file_edit calls require non-overlapping edits",
        ):
            await file_edit(
                path=tmp_path.name,
                edits=[
                    {
                        "start_line": 2,
                        "end_line": 3,
                        "content": "two-three\n",
                        "mode": "replace",
                    },
                    {
                        "start_line": 3,
                        "content": "inserted\n",
                        "mode": "insert_before",
                    },
                ],
            )
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_edit_rejects_syntax_breaking_structural_python_replace():
    from dan.tools.file_edit import file_edit

    tmp_path = Path.cwd() / "test_tool_executor_file_edit_structural_syntax_temp.py"
    tmp_path.write_text(
        textwrap.dedent(
            """
            def alpha():
                line_1 = 1
                line_2 = 2
                line_3 = 3
                line_4 = 4
                line_5 = 5
                line_6 = 6
                line_7 = 7
                line_8 = 8
                return (
                    line_1
                    + line_2
                    + line_3
                    + line_4
                    + line_5
                    + line_6
                    + line_7
                    + line_8
                )

            def beta():
                line_1 = 1
                line_2 = 2
                line_3 = 3
                line_4 = 4
                line_5 = 5
                line_6 = 6
                line_7 = 7
                line_8 = 8
                return (
                    line_1
                    + line_2
                    + line_3
                    + line_4
                    + line_5
                    + line_6
                    + line_7
                    + line_8
                )
            """
        ).lstrip(),
        encoding="utf-8",
    )

    try:
        with pytest.raises(
            ValueError,
            match="spans multiple top-level Python blocks and leaves the file syntactically invalid",
        ):
            await file_edit(
                path=tmp_path.name,
                start_line=1,
                end_line=25,
                content=(
                    "def broken(:\n"
                    "    # filler 1\n"
                    "    # filler 2\n"
                    "    # filler 3\n"
                    "    # filler 4\n"
                    "    # filler 5\n"
                    "    # filler 6\n"
                    "    # filler 7\n"
                    "    # filler 8\n"
                    "    # filler 9\n"
                    "    # filler 10\n"
                ),
                mode="replace",
            )
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 3. Tool not found error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_not_found_returns_failed():
    """ToolOperator with tool_id='nonexistent' → NodeResult.status == FAILED."""
    registry = ToolRegistry()
    exec_registry = ExecutorRegistry()
    exec_registry.register("tool_operator", ToolExecutor(registry))

    inp = InputNode(
        id="inp",
        name="Input",
        variables=[InputVariable(name="x", type="string")],
        output_ports=[OutputPort(name="x"), OutputPort(name="input")],
    )
    tool = ToolOperator(
        id="bad",
        name="Bad",
        tool_id="nonexistent",
        tool_config={},
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="result")],
    )

    graph = Graph(
        metadata=GraphMetadata(name="tool_not_found"),
        nodes=[inp, tool],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="inp",
                source_port="x",
                target_node_id="bad",
                target_port="input",
            ),
        ],
        entry_points=["inp"],
        exit_points=["bad"],
    )

    engine = Engine(
        config=_dummy_config(),
        executor_registry=exec_registry,
        checkpoint_store=NullCheckpointStore(),
    )
    result = await engine.run(graph, inputs={"x": "ignored"})

    assert not result.success
    assert "bad" in result.node_statuses
    assert result.node_statuses["bad"] == "failed"
    assert "nonexistent" in (result.errors.get("bad") or "")


# ---------------------------------------------------------------------------
# 4. Tool with retry policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_retry_policy_succeeds_on_second_attempt():
    """Register tool that fails (OSError) on first call, succeeds on second; max_retries=1 → assert success."""
    call_count = [0]
    tool_registry = ToolRegistry()

    async def flaky_tool(**kwargs) -> dict:
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("simulated transient failure")
        return {"result": "ok"}

    tool_registry.register("flaky", flaky_tool)

    exec_registry = ExecutorRegistry()
    exec_registry.register("tool_operator", ToolExecutor(tool_registry))

    inp = InputNode(
        id="inp",
        name="Input",
        variables=[InputVariable(name="x", type="string")],
        output_ports=[OutputPort(name="x"), OutputPort(name="input")],
    )
    tool = ToolOperator(
        id="flaky_node",
        name="Flaky",
        tool_id="flaky",
        tool_config={},
        retry_policy=RetryPolicy(max_retries=1),
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="result")],
    )

    graph = Graph(
        metadata=GraphMetadata(name="retry_test"),
        nodes=[inp, tool],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="inp",
                source_port="x",
                target_node_id="flaky_node",
                target_port="input",
            ),
        ],
        entry_points=["inp"],
        exit_points=["flaky_node"],
    )

    engine = Engine(
        config=_dummy_config(),
        executor_registry=exec_registry,
        checkpoint_store=NullCheckpointStore(),
    )
    result = await engine.run(graph, inputs={"x": "ignored"})

    assert result.success
    assert result.outputs.get("result", {}).get("result") == "ok"
    assert call_count[0] == 2
