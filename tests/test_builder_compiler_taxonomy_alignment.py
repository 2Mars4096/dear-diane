from __future__ import annotations

from dan.builder.compiler import _PendingNode, _build_node
from dan.models.node_taxonomy import RUNTIME_NODE_TYPES
from dan.models.ports import InputPort, OutputPort
from dan.server.graph_mutator import _default_node_config, _default_ports


def test_builder_compiler_can_build_every_runtime_node_type() -> None:
    for node_type in sorted(RUNTIME_NODE_TYPES):
        defaults = _default_node_config(node_type)
        input_ports, output_ports = _default_ports(node_type, defaults)
        pending = _PendingNode(
            id=f"{node_type}_node",
            node_type=node_type,
            kwargs={
                "name": node_type,
                **defaults,
            },
            explicit_input_ports=[InputPort(**port) for port in input_ports],
            explicit_output_ports=[OutputPort(**port) for port in output_ports],
        )

        built = _build_node(pending)

        assert built.node_type == node_type
