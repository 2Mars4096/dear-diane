"""Tests for FeedbackSelector filtering in loops (Plan 16-4).

Covers:
  - FeedbackSelector model validation and serialization
  - _apply_feedback_selector helper (include, exclude, rename, transform)
  - Gate while-loop feedback filtering via engine scheduling
  - WhileLoopExecutor feedback filtering
  - artifact_ports accumulation across iterations
  - Backward compatibility (no selector = unchanged behavior)
  - state_schema and feedback_selector cooperation
"""

from __future__ import annotations

import pytest

from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.context_runtime import LocalStateManager
from dan.engine.executor import ExecutionContext, ExecutorRegistry
from dan.executors.control_flow import WhileLoopExecutor, _apply_feedback_selector
from dan.models.context import FeedbackSelector
from dan.models.control_flow import GateNode, WhileLoopNode
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, NodeBase
from dan.models.ports import InputPort, OutputPort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config():
    return EngineConfig(checkpoint_enabled=False)


def _engine(registry: ExecutorRegistry | None = None):
    return Engine(
        config=_config(),
        checkpoint_store=NullCheckpointStore(),
        executor_registry=registry,
    )


class _MultiOutputBodyExecutor:
    """Body executor that outputs score, draft, debug_log.

    Tracks received inputs for each call so tests can verify
    which keys were fed back on subsequent iterations.
    """

    def __init__(self, score_increment: float = 0.3):
        self.received_inputs: list[dict] = []
        self.score_increment = score_increment

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        if node.id == "src":
            return NodeResult(outputs={"score": 0.1, "draft": "initial"})
        if node.id == "body":
            self.received_inputs.append(dict(inputs))
            score = inputs.get("score", 0) + self.score_increment
            iteration = len(self.received_inputs)
            return NodeResult(outputs={
                "score": score,
                "draft": f"draft_v{iteration}",
                "debug_log": f"log_{iteration}",
            })
        if node.id == "sink":
            return NodeResult(outputs={"result": inputs.get("data", inputs)})
        return NodeResult(outputs=inputs)


class _ScoreGateExecutor:
    """Gate executor that continues while score < threshold."""

    def __init__(self, threshold: float = 0.9):
        self.threshold = threshold
        self.call_count = 0

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        self.call_count += 1
        score = inputs.get("score", 0)
        if score < self.threshold:
            return NodeResult(outputs={"continue": inputs})
        return NodeResult(outputs={"done": inputs})


class _MockWhileContext:
    """Minimal context for direct WhileLoopExecutor tests."""

    def __init__(self, body_fn):
        self.local_state = LocalStateManager()
        self._body_fn = body_fn
        self.subgraph_calls: list[dict] = []

    async def emit_event(self, **kwargs):
        pass

    async def run_subgraph(self, sub_graph_key, inputs, parent_node_id=None, targeted_inputs=None):
        self.subgraph_calls.append(dict(inputs))
        return self._body_fn(inputs, len(self.subgraph_calls))


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def _feedback_gate_loop_graph(
    feedback_selector: FeedbackSelector | None = None,
    artifact_ports: list[str] | None = None,
    max_iterations: int = 10,
) -> Graph:
    """src -> gate(while) --continue--> body -> gate (cycle), --done--> sink.

    Matches the canonical gate-loop topology where gate comes before body.
    On the initial pass: src feeds gate, gate evaluates and outputs continue
    (to body) or done (to sink). Body feeds back to gate for re-evaluation.
    FeedbackSelector filters what gate's continue port passes to body.
    """
    src = CodeOperator(
        id="src", name="Source", code="...",
        output_ports=[OutputPort(name="score"), OutputPort(name="draft")],
    )
    gate = GateNode(
        id="gate", name="Loop Gate", condition="score < 0.9",
        gate_mode="while", max_iterations=max_iterations,
        feedback_selector=feedback_selector,
        artifact_ports=artifact_ports,
        input_ports=[
            InputPort(name="score"),
            InputPort(name="draft", required=False),
            InputPort(name="debug_log", required=False),
        ],
        output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
    )
    body = CodeOperator(
        id="body", name="Body", code="...",
        input_ports=[
            InputPort(name="score", required=False),
            InputPort(name="draft", required=False),
            InputPort(name="debug_log", required=False),
        ],
        output_ports=[
            OutputPort(name="score"),
            OutputPort(name="draft"),
            OutputPort(name="debug_log"),
        ],
    )
    sink = CodeOperator(
        id="sink", name="Sink", code="...",
        input_ports=[InputPort(name="data", required=False)],
        output_ports=[OutputPort(name="result")],
    )

    return Graph(
        nodes=[src, gate, body, sink],
        edges=[
            DataEdge(id="e_src_score", source_node_id="src", source_port="score",
                     target_node_id="gate", target_port="score"),
            DataEdge(id="e_src_draft", source_node_id="src", source_port="draft",
                     target_node_id="gate", target_port="draft"),
            DataEdge(id="e_gate_continue", source_node_id="gate", source_port="continue",
                     target_node_id="body", target_port="score"),
            DataEdge(id="e_body_score", source_node_id="body", source_port="score",
                     target_node_id="gate", target_port="score"),
            DataEdge(id="e_body_draft", source_node_id="body", source_port="draft",
                     target_node_id="gate", target_port="draft"),
            DataEdge(id="e_body_debug", source_node_id="body", source_port="debug_log",
                     target_node_id="gate", target_port="debug_log"),
            DataEdge(id="e_done", source_node_id="gate", source_port="done",
                     target_node_id="sink", target_port="data"),
        ],
        entry_points=["src"],
        exit_points=["sink"],
    )


# ---------------------------------------------------------------------------
# 1. FeedbackSelector model tests
# ---------------------------------------------------------------------------


class TestFeedbackSelectorModel:
    def test_serialization_roundtrip(self):
        fs = FeedbackSelector(include=["score", "draft"], rename={"improved": "draft"})
        data = fs.model_dump()
        restored = FeedbackSelector(**data)
        assert restored.include == ["score", "draft"]
        assert restored.rename == {"improved": "draft"}
        assert restored.exclude is None
        assert restored.transform is None

    def test_mutual_exclusion_raises(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            FeedbackSelector(include=["a"], exclude=["b"])

    def test_none_defaults(self):
        fs = FeedbackSelector()
        assert fs.include is None
        assert fs.exclude is None
        assert fs.rename is None
        assert fs.transform is None


# ---------------------------------------------------------------------------
# 2. _apply_feedback_selector unit tests
# ---------------------------------------------------------------------------


class TestApplyFeedbackSelector:
    def test_include_filtering(self):
        data = {"score": 0.5, "draft": "text", "debug_log": "log"}
        result = _apply_feedback_selector(data, FeedbackSelector(include=["score"]))
        assert result == {"score": 0.5}

    def test_exclude_filtering(self):
        data = {"score": 0.5, "draft": "text", "debug_log": "log"}
        result = _apply_feedback_selector(data, FeedbackSelector(exclude=["debug_log"]))
        assert result == {"score": 0.5, "draft": "text"}

    def test_rename(self):
        data = {"improved": "new_text", "score": 0.8}
        result = _apply_feedback_selector(
            data, FeedbackSelector(rename={"improved": "draft"}),
        )
        assert result == {"draft": "new_text", "score": 0.8}

    def test_transform(self):
        data = {"score": 0.5, "text": "hello world"}
        result = _apply_feedback_selector(
            data, FeedbackSelector(transform="{'summary': inputs['text'][:5]}"),
        )
        assert result == {"summary": "hello"}

    def test_include_then_rename(self):
        data = {"improved": "v2", "score": 0.8, "debug": "x"}
        result = _apply_feedback_selector(
            data,
            FeedbackSelector(include=["improved", "score"], rename={"improved": "draft"}),
        )
        assert result == {"draft": "v2", "score": 0.8}

    def test_noop_passthrough(self):
        data = {"a": 1, "b": 2, "c": 3}
        result = _apply_feedback_selector(data, FeedbackSelector())
        assert result == data

    def test_transform_non_dict_wraps_in_result(self):
        data = {"x": 10}
        result = _apply_feedback_selector(
            data, FeedbackSelector(transform="inputs['x'] * 2"),
        )
        assert result == {"result": 20}

    def test_include_nonexistent_key_returns_empty(self):
        """Including keys that don't exist in data yields an empty dict."""
        data = {"score": 0.5, "draft": "text"}
        result = _apply_feedback_selector(
            data, FeedbackSelector(include=["nonexistent_key"]),
        )
        assert result == {}

    def test_include_mix_existing_and_nonexistent(self):
        """Only existing keys are retained; missing ones silently dropped."""
        data = {"score": 0.5, "draft": "text"}
        result = _apply_feedback_selector(
            data, FeedbackSelector(include=["score", "ghost"]),
        )
        assert result == {"score": 0.5}

    def test_exclude_nonexistent_key_is_noop(self):
        """Excluding a key that doesn't exist leaves data unchanged."""
        data = {"score": 0.5, "draft": "text"}
        result = _apply_feedback_selector(
            data, FeedbackSelector(exclude=["nonexistent"]),
        )
        assert result == {"score": 0.5, "draft": "text"}


# ---------------------------------------------------------------------------
# 3. Gate while-loop: include filtering (engine-level)
# ---------------------------------------------------------------------------


class TestGateLoopFeedbackInclude:
    @pytest.mark.asyncio
    async def test_include_filters_feedback(self):
        """Only 'score' feeds back; body should not see 'draft' or 'debug_log' on iterations.

        Note: body is skipped on the initial pass (back-edge from gate not yet
        produced) and only runs inside _iterate_cycle.  The first body call
        receives empty/partial feedback; subsequent calls get filtered data.
        """
        graph = _feedback_gate_loop_graph(
            feedback_selector=FeedbackSelector(include=["score"]),
            max_iterations=10,
        )

        body_exec = _MultiOutputBodyExecutor(score_increment=0.3)
        gate_exec = _ScoreGateExecutor(threshold=0.9)

        reg = ExecutorRegistry()
        reg.register("code_operator", body_exec)
        reg.register("gate", gate_exec)

        engine = _engine(registry=reg)
        result = await engine.run(graph, inputs={"score": 0.1, "draft": "initial"})

        assert result.success
        assert len(body_exec.received_inputs) >= 2

        # After the first iteration produces feedback, subsequent body calls
        # should only receive "score" (the only included key).
        for call_inputs in body_exec.received_inputs[1:]:
            assert "score" in call_inputs
            assert "draft" not in call_inputs
            assert "debug_log" not in call_inputs


# ---------------------------------------------------------------------------
# 4. Gate while-loop: exclude filtering (engine-level)
# ---------------------------------------------------------------------------


class TestGateLoopFeedbackExclude:
    @pytest.mark.asyncio
    async def test_exclude_filters_feedback(self):
        """debug_log is excluded from feedback; score and draft feed back normally."""
        graph = _feedback_gate_loop_graph(
            feedback_selector=FeedbackSelector(exclude=["debug_log"]),
            max_iterations=10,
        )

        body_exec = _MultiOutputBodyExecutor(score_increment=0.3)
        gate_exec = _ScoreGateExecutor(threshold=0.9)

        reg = ExecutorRegistry()
        reg.register("code_operator", body_exec)
        reg.register("gate", gate_exec)

        engine = _engine(registry=reg)
        result = await engine.run(graph, inputs={"score": 0.1, "draft": "initial"})

        assert result.success
        assert len(body_exec.received_inputs) >= 2

        for call_inputs in body_exec.received_inputs[1:]:
            assert "score" in call_inputs
            assert "draft" in call_inputs
            assert "debug_log" not in call_inputs


# ---------------------------------------------------------------------------
# 5. Gate while-loop: artifact_ports accumulation (engine-level)
# ---------------------------------------------------------------------------


class TestGateLoopArtifactPorts:
    @pytest.mark.asyncio
    async def test_artifact_accumulation(self):
        """artifact_ports extracts debug_log per iteration and accumulates."""
        graph = _feedback_gate_loop_graph(
            artifact_ports=["debug_log"],
            max_iterations=10,
        )

        body_exec = _MultiOutputBodyExecutor(score_increment=0.3)
        gate_exec = _ScoreGateExecutor(threshold=0.9)

        reg = ExecutorRegistry()
        reg.register("code_operator", body_exec)
        reg.register("gate", gate_exec)

        engine = _engine(registry=reg)
        result = await engine.run(graph, inputs={"score": 0.1, "draft": "initial"})

        assert result.success
        assert len(body_exec.received_inputs) >= 2

        # debug_log should not appear in body feedback on subsequent iterations
        for call_inputs in body_exec.received_inputs[1:]:
            assert "debug_log" not in call_inputs


# ---------------------------------------------------------------------------
# 6. Gate while-loop: backward compatibility
# ---------------------------------------------------------------------------


class TestGateLoopBackwardCompat:
    @pytest.mark.asyncio
    async def test_no_selector_all_outputs_feed_back(self):
        """Without feedback_selector, all body outputs feed back (existing behavior)."""
        graph = _feedback_gate_loop_graph(
            feedback_selector=None,
            artifact_ports=None,
            max_iterations=10,
        )

        body_exec = _MultiOutputBodyExecutor(score_increment=0.3)
        gate_exec = _ScoreGateExecutor(threshold=0.9)

        reg = ExecutorRegistry()
        reg.register("code_operator", body_exec)
        reg.register("gate", gate_exec)

        engine = _engine(registry=reg)
        result = await engine.run(graph, inputs={"score": 0.1, "draft": "initial"})

        assert result.success
        assert len(body_exec.received_inputs) >= 2

        # After first iteration produces output, ALL keys feed back unfiltered
        for call_inputs in body_exec.received_inputs[1:]:
            assert "score" in call_inputs
            assert "draft" in call_inputs
            assert "debug_log" in call_inputs


# ---------------------------------------------------------------------------
# 7. WhileLoopExecutor: include filtering
# ---------------------------------------------------------------------------


class TestWhileLoopExecFeedbackInclude:
    @pytest.mark.asyncio
    async def test_include_filtering(self):
        """WhileLoopExecutor only feeds back included keys."""
        call_count = 0

        def body_fn(inputs, n):
            nonlocal call_count
            call_count += 1
            score = inputs.get("score", 0) + 0.3
            return {"score": score, "draft": f"v{n}", "debug_log": f"log_{n}"}

        ctx = _MockWhileContext(body_fn)

        node = WhileLoopNode(
            id="loop1", name="Test Loop",
            condition="score < 0.9",
            body_graph="body",
            max_iterations=10,
            feedback_selector=FeedbackSelector(include=["score"]),
        )

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await WhileLoopExecutor().execute(node, {"score": 0.1, "draft": "init"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        # "draft" from initial inputs persists (it's in working_data from start)
        # but body's "draft" output doesn't overwrite it (not in feedback)
        assert result.outputs.get("draft") == "init"
        # debug_log never enters working_data
        assert "debug_log" not in result.outputs

        # Second+ subgraph calls should still have "draft" = "init" (from initial working_data)
        # but "debug_log" should never appear
        for call_inputs in ctx.subgraph_calls[1:]:
            assert "debug_log" not in call_inputs


# ---------------------------------------------------------------------------
# 8. WhileLoopExecutor: exclude filtering
# ---------------------------------------------------------------------------


class TestWhileLoopExecFeedbackExclude:
    @pytest.mark.asyncio
    async def test_exclude_filtering(self):
        """WhileLoopExecutor excludes debug_log from feedback."""
        def body_fn(inputs, n):
            score = inputs.get("score", 0) + 0.3
            return {"score": score, "draft": f"v{n}", "debug_log": f"log_{n}"}

        ctx = _MockWhileContext(body_fn)

        node = WhileLoopNode(
            id="loop1", name="Test Loop",
            condition="score < 0.9",
            body_graph="body",
            max_iterations=10,
            feedback_selector=FeedbackSelector(exclude=["debug_log"]),
        )

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await WhileLoopExecutor().execute(node, {"score": 0.1}, ctx)

        assert result.status == NodeStatus.COMPLETED
        # draft should be in final output (not excluded)
        assert "draft" in result.outputs
        # debug_log should not propagate
        assert "debug_log" not in result.outputs


# ---------------------------------------------------------------------------
# 9. WhileLoopExecutor: rename mapping
# ---------------------------------------------------------------------------


class TestWhileLoopExecRename:
    @pytest.mark.asyncio
    async def test_rename_mapping(self):
        """FeedbackSelector rename maps body output keys before feedback."""
        def body_fn(inputs, n):
            score = inputs.get("score", 0) + 0.3
            # Body outputs "improved" but loop expects "draft"
            return {"score": score, "improved": f"v{n}"}

        ctx = _MockWhileContext(body_fn)

        node = WhileLoopNode(
            id="loop1", name="Test Loop",
            condition="score < 0.9",
            body_graph="body",
            max_iterations=10,
            feedback_selector=FeedbackSelector(rename={"improved": "draft"}),
        )

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await WhileLoopExecutor().execute(node, {"score": 0.1, "draft": "init"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        # "improved" should be renamed to "draft" in working_data
        assert "draft" in result.outputs
        # The renamed value should reflect the body's last output
        assert result.outputs["draft"].startswith("v")
        # "improved" should not be a separate key (it's renamed)
        assert "improved" not in result.outputs


# ---------------------------------------------------------------------------
# 10. WhileLoopExecutor: artifact_ports accumulation
# ---------------------------------------------------------------------------


class TestWhileLoopExecArtifacts:
    @pytest.mark.asyncio
    async def test_artifact_ports_accumulation(self):
        """artifact_ports are extracted per iteration and collected in final output."""
        def body_fn(inputs, n):
            score = inputs.get("score", 0) + 0.3
            return {"score": score, "draft": f"v{n}", "trace": f"trace_{n}"}

        ctx = _MockWhileContext(body_fn)

        node = WhileLoopNode(
            id="loop1", name="Test Loop",
            condition="score < 0.9",
            body_graph="body",
            max_iterations=10,
            artifact_ports=["trace"],
        )

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await WhileLoopExecutor().execute(node, {"score": 0.1}, ctx)

        assert result.status == NodeStatus.COMPLETED
        # "trace" should not be in working_data feedback
        assert "trace" not in {k for k in result.outputs if k != "artifacts"}
        # Accumulated artifacts should be in final output
        artifacts = result.outputs.get("artifacts", [])
        assert len(artifacts) >= 2
        assert all("trace" in item for item in artifacts)

        # Subsequent subgraph calls should not receive "trace"
        for call_inputs in ctx.subgraph_calls[1:]:
            assert "trace" not in call_inputs


# ---------------------------------------------------------------------------
# 11. WhileLoopExecutor: backward compatibility
# ---------------------------------------------------------------------------


class TestWhileLoopExecBackwardCompat:
    @pytest.mark.asyncio
    async def test_no_selector_all_outputs_merge(self):
        """Without feedback_selector, behavior is identical to baseline."""
        def body_fn(inputs, n):
            score = inputs.get("score", 0) + 0.3
            return {"score": score, "draft": f"v{n}", "debug_log": f"log_{n}"}

        ctx = _MockWhileContext(body_fn)

        node = WhileLoopNode(
            id="loop1", name="Test Loop",
            condition="score < 0.9",
            body_graph="body",
            max_iterations=10,
        )

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await WhileLoopExecutor().execute(node, {"score": 0.1}, ctx)

        assert result.status == NodeStatus.COMPLETED
        # All keys should be in final output
        assert "score" in result.outputs
        assert "draft" in result.outputs
        assert "debug_log" in result.outputs
        # No artifacts key (no artifact_ports set)
        assert "artifacts" not in result.outputs


# ---------------------------------------------------------------------------
# 12. WhileLoopExecutor: state_schema + feedback_selector cooperation
# ---------------------------------------------------------------------------


class TestWhileLoopStateSchemaCooperation:
    @pytest.mark.asyncio
    async def test_state_schema_and_feedback_selector(self):
        """state_schema manages typed state variables; feedback_selector filters additional data."""
        def body_fn(inputs, n):
            score = inputs.get("score", 0) + 0.3
            return {"score": score, "draft": f"v{n}", "debug_log": f"log_{n}"}

        ctx = _MockWhileContext(body_fn)

        node = WhileLoopNode(
            id="loop1", name="Test Loop",
            condition="score < 0.9",
            body_graph="body",
            max_iterations=10,
            state_schema={"score": {"type": "number"}},
            state_defaults={"score": 0.0},
            feedback_selector=FeedbackSelector(include=["draft"]),
        )

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await WhileLoopExecutor().execute(node, {"score": 0.1}, ctx)

        assert result.status == NodeStatus.COMPLETED
        # score is managed by state_schema — should be updated from body_output
        assert result.outputs["score"] >= 0.9
        # draft is managed by feedback_selector — should be present
        assert "draft" in result.outputs
        # debug_log is not in include nor state_schema — should not propagate
        assert "debug_log" not in result.outputs
