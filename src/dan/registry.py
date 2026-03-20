"""Node type registry — maps ``node_type`` strings to Pydantic model classes.

The built-in types are registered automatically on import.  The registry
is for **programmatic discovery** (e.g. listing available node types in
a UI palette) and for user code that needs to resolve a type string to
its class.  JSON round-trip deserialization of built-in types is handled
by the Pydantic discriminated union in ``dan.models.graph.Node``.
"""

from __future__ import annotations

from typing import Type

from dan.models.node_taxonomy import RUNTIME_NODE_TYPE_MAP
from dan.models.nodes import NodeBase


class NodeTypeRegistry:
    """Singleton registry of known node types.

    Pydantic's discriminated union in ``Graph`` handles deserialization
    of the built-in types.  This registry is for **programmatic
    discovery** — e.g. populating a UI palette with available node types,
    or resolving a type string to a class at runtime.

    User-defined node types can be registered here for discovery, but
    they will not participate in ``Graph.model_validate()`` unless the
    ``Node`` union in ``graph.py`` is also extended.
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


# Auto-register built-in runtime types on import from the canonical taxonomy.
for _type_name, _cls in RUNTIME_NODE_TYPE_MAP.items():
    NodeTypeRegistry.register(_type_name, _cls)
