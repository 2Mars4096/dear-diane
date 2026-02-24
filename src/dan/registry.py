"""Node type registry — maps ``node_type`` strings to Pydantic model classes.

The built-in types are registered automatically on import.  External
code can call ``NodeTypeRegistry.register()`` to add custom node types
that participate in JSON round-trip deserialization.
"""

from __future__ import annotations

from typing import Type

from dan.models.nodes import (
    CodeOperator,
    LLMOperator,
    NodeBase,
    ToolOperator,
)
from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    HumanInTheLoopNode,
    IfElseNode,
    ReduceNode,
    RouterNode,
    WhileLoopNode,
)


class NodeTypeRegistry:
    """Singleton registry of known node types.

    Pydantic's discriminated union already handles deserialization of
    the built-in types.  This registry is for **programmatic discovery**
    and for extending the type system with user-defined node types.
    """

    _registry: dict[str, Type[NodeBase]] = {}

    @classmethod
    def register(cls, node_type: str, node_class: Type[NodeBase]) -> None:
        cls._registry[node_type] = node_class

    @classmethod
    def get(cls, node_type: str) -> Type[NodeBase]:
        try:
            return cls._registry[node_type]
        except KeyError:
            raise KeyError(
                f"Unknown node type '{node_type}'. "
                f"Registered types: {sorted(cls._registry)}"
            )

    @classmethod
    def all_types(cls) -> dict[str, Type[NodeBase]]:
        return dict(cls._registry)

    @classmethod
    def is_registered(cls, node_type: str) -> bool:
        return node_type in cls._registry


# Auto-register built-in types on import.
_BUILTINS: list[tuple[str, Type[NodeBase]]] = [
    ("llm_operator", LLMOperator),
    ("tool_operator", ToolOperator),
    ("code_operator", CodeOperator),
    ("if_else", IfElseNode),
    ("while_loop", WhileLoopNode),
    ("for_each", ForEachNode),
    ("reduce", ReduceNode),
    ("router", RouterNode),
    ("human_in_the_loop", HumanInTheLoopNode),
    ("composite", CompositeNode),
]

for _type_name, _cls in _BUILTINS:
    NodeTypeRegistry.register(_type_name, _cls)
