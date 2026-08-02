"""Tests for Plan 14-2 — Context Scoping & Boundary Enforcement.

Covers:
  - BoundaryContract / SignalSpec model validation
  - ScopedContextView read/write isolation and propagation
  - Engine integration with boundary_enforcement=True
  - CompositeNode with boundary_contract isolation
  - Signal emission (sticky / non-sticky)
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from dan.models.context import (
    BoundaryContract,
    ContextProjection,
    SharedContextDeclaration,
    SignalSpec,
)
from dan.engine.context_runtime import (
    ScopedContextView,
    SharedContextStore,
)


# =====================================================================
# Model tests
# =====================================================================


class TestBoundaryContractModel:
    def test_defaults(self):
        bc = BoundaryContract()
        assert bc.accepts is None
        assert bc.returns is None
        assert bc.signals == []
        assert bc.reads_global == []
        assert bc.writes_global == []

    def test_full_spec(self):
        bc = BoundaryContract(
            accepts={"type": "object", "properties": {"x": {"type": "number"}}},
            returns={"type": "object", "properties": {"y": {"type": "string"}}},
            signals=[SignalSpec(name="done", sticky=True, severity="info")],
            reads_global=["config", "plan"],
            writes_global=["result"],
        )
        assert bc.reads_global == ["config", "plan"]
        assert len(bc.signals) == 1
        assert bc.signals[0].sticky is True

    def test_serialization_round_trip(self):
        bc = BoundaryContract(
            reads_global=["a"], writes_global=["b"],
            signals=[SignalSpec(name="s1", payload_schema={"type": "string"})],
        )
        data = bc.model_dump()
        restored = BoundaryContract.model_validate(data)
        assert restored.reads_global == ["a"]
        assert restored.signals[0].name == "s1"


class TestSignalSpec:
    def test_defaults(self):
        s = SignalSpec(name="alert")
        assert s.sticky is False
        assert s.severity == "info"
        assert s.payload_schema == {}

    def test_sticky_signal(self):
        s = SignalSpec(name="halt", sticky=True, severity="error")
        assert s.sticky is True


# =====================================================================
# ScopedContextView tests
# =====================================================================


class TestScopedContextView:
    @staticmethod
    def _parent_store() -> SharedContextStore:
        """Create a parent store with some declared keys and values."""
        decls = [
            SharedContextDeclaration(key="config"),
            SharedContextDeclaration(key="plan"),
            SharedContextDeclaration(key="result"),
            SharedContextDeclaration(key="secret"),
        ]
        store = SharedContextStore(decls)
        store.write("config", {"model": "gpt-4"})
        store.write("plan", "do stuff")
        store.write("secret", "top-secret")
        return store

    def test_no_contract_full_passthrough(self):
        """Without reads/writes_global, scoped view is a full copy."""
        parent = self._parent_store()
        view = ScopedContextView(parent, parent._declarations.values())
        assert view.read("config") == {"model": "gpt-4"}
        assert view.read("secret") == "top-secret"

    def test_reads_global_restriction(self):
        """Only whitelisted keys are visible in the scoped view."""
        parent = self._parent_store()
        view = ScopedContextView(
            parent, [],
            reads_global=["config", "plan"],
        )
        assert view.read("config") == {"model": "gpt-4"}
        assert view.read("plan") == "do stuff"
        with pytest.raises(KeyError, match="not in reads_global"):
            view.read("secret")

    def test_writes_global_restriction(self):
        """Only whitelisted keys propagate back to parent."""
        parent = self._parent_store()
        view = ScopedContextView(
            parent, [],
            reads_global=["config"],
            writes_global=["result"],
        )
        view.write("result", "success")
        view.write("local_only", "mine")

        assert view.has("result")
        assert view.has("local_only")

        view.propagate_to_parent()

        assert parent.read("result") == "success"
        assert not parent.has("local_only")

    def test_child_isolation(self):
        """Writes to non-global keys stay in the child's local store."""
        parent = self._parent_store()
        view = ScopedContextView(
            parent, [],
            reads_global=["config"],
            writes_global=[],
        )
        view.write("child_data", 42)
        assert view.has("child_data")

        view.propagate_to_parent()
        assert not parent.has("child_data")

    def test_snapshot_includes_all(self):
        """Snapshot captures both global and local state."""
        parent = self._parent_store()
        view = ScopedContextView(
            parent, [],
            reads_global=["config"],
            writes_global=["result"],
        )
        view.write("result", "ok")
        view.write("local", "mine")
        snap = view.snapshot()
        assert "result" in snap
        assert "config" in snap

    def test_append_to_global_key(self):
        parent = self._parent_store()
        view = ScopedContextView(
            parent, [],
            reads_global=["result"],
            writes_global=["result"],
        )
        view.append("result", "a")
        view.append("result", "b")
        assert view.read("result") == ["a", "b"]

        view.propagate_to_parent()
        assert parent.read("result") == ["a", "b"]

    def test_no_mutation_of_parent_without_propagate(self):
        """Parent stays clean until propagate_to_parent() is called."""
        parent = self._parent_store()
        view = ScopedContextView(
            parent, [],
            reads_global=["config"],
            writes_global=["result"],
        )
        view.write("result", "new_value")
        assert not parent.has("result")
        view.propagate_to_parent()
        assert parent.read("result") == "new_value"


# =====================================================================
# Engine integration — boundary enforcement
# =====================================================================


class TestEngineBoundaryEnforcement:
    """Test that boundary_enforcement=True isolates sub-graph contexts."""

    @staticmethod
    def _composite_graph():
        """Graph with a composite node that has a boundary contract."""
        from dan.models.graph import Graph
        from dan.models.control_flow import CompositeNode
        from dan.models.context import BoundaryContract, SharedContextDeclaration
        from dan.models.nodes import CodeOperator
        from dan.models.ports import InputPort, OutputPort

        inner_node = CodeOperator(
            id="inner_code",
            name="inner_code",
            code="result = trigger + '_processed'",
            input_ports=[InputPort(name="trigger")],
            output_ports=[OutputPort(name="result")],
        )
        inner_graph = Graph(
            nodes=[inner_node],
            edges=[],
            entry_points=["inner_code"],
            exit_points=["inner_code"],
        )

        composite = CompositeNode(
            id="comp1",
            name="comp1",
            body_graph="inner",
            input_mappings={"trigger": "trigger"},
            output_mappings={"result": "result"},
            boundary_contract=BoundaryContract(
                reads_global=["config"],
                writes_global=["result"],
            ),
            input_ports=[InputPort(name="trigger")],
            output_ports=[OutputPort(name="result")],
        )

        outer = Graph(
            nodes=[composite],
            edges=[],
            entry_points=["comp1"],
            exit_points=["comp1"],
            sub_graphs={"inner": inner_graph},
            shared_context=[
                SharedContextDeclaration(key="config"),
                SharedContextDeclaration(key="result"),
                SharedContextDeclaration(key="secret"),
            ],
        )
        return outer

    @pytest.mark.asyncio
    async def test_boundary_enforcement_on(self):
        """With boundary_enforcement=True, the composite's
        boundary_contract should limit what the child sees."""
        from dan.engine import Engine, EngineConfig

        config = EngineConfig(
            llm_api_key="test",
            checkpoint_enabled=False,
            memory_enabled=False,
            boundary_enforcement=True,
        )
        engine = Engine(config=config)
        graph = self._composite_graph()
        result = await engine.run(graph, inputs={"trigger": "hello"})
        assert result.success
        assert result.outputs.get("result") == "hello_processed"

    @pytest.mark.asyncio
    async def test_boundary_enforcement_off(self):
        """With boundary_enforcement=False (default), full pass-through."""
        from dan.engine import Engine, EngineConfig

        config = EngineConfig(
            llm_api_key="test",
            checkpoint_enabled=False,
            memory_enabled=False,
            boundary_enforcement=False,
        )
        engine = Engine(config=config)
        graph = self._composite_graph()
        result = await engine.run(graph, inputs={"trigger": "hello"})
        assert result.success


class TestSignalEmission:
    """Test ExecutionContext.emit_signal()."""

    def test_sticky_signal_writes_to_shared_context(self):
        from dan.engine.executor import ExecutionContext, EngineConfig
        from dan.engine.state import ExecutionState
        from dan.engine.context_runtime import (
            ArtifactStore, LocalStateManager, SharedContextStore,
        )
        from dan.models.context import SharedContextDeclaration
        from dan.models.graph import Graph

        graph = Graph(nodes=[], edges=[])
        state = ExecutionState(graph)
        sc = SharedContextStore()
        ctx = ExecutionContext(
            state=state,
            config=EngineConfig(llm_api_key="test"),
            shared_context=sc,
            artifacts=ArtifactStore(),
            local_state=LocalStateManager(),
        )

        ctx.emit_signal("halt", payload={"reason": "done"}, sticky=True)
        assert sc._store.get("signal:halt") == {"reason": "done"}

    def test_non_sticky_signal_does_not_write_context(self):
        from dan.engine.executor import ExecutionContext, EngineConfig
        from dan.engine.state import ExecutionState
        from dan.engine.context_runtime import (
            ArtifactStore, LocalStateManager, SharedContextStore,
        )
        from dan.models.graph import Graph

        graph = Graph(nodes=[], edges=[])
        state = ExecutionState(graph)
        sc = SharedContextStore()
        ctx = ExecutionContext(
            state=state,
            config=EngineConfig(llm_api_key="test"),
            shared_context=sc,
            artifacts=ArtifactStore(),
            local_state=LocalStateManager(),
        )

        ctx.emit_signal("info", payload={"msg": "hi"}, sticky=False)
        assert "signal:info" not in sc._store
