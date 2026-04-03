"""Port definitions — the typed interface contract for every node."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, Field


class InputPort(BaseModel):
    """A named, typed input slot on a node.

    The ``json_schema`` field holds a standard JSON Schema object that
    describes the data this port accepts.  Schema compatibility between
    connected ports is checked at graph-construction time.
    """

    name: str
    json_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema defining the expected data shape",
        validation_alias=AliasChoices("json_schema", "schema"),
    )
    required: bool = True
    description: str = ""


class OutputPort(BaseModel):
    """A named, typed output slot on a node."""

    name: str
    json_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema defining the produced data shape",
        validation_alias=AliasChoices("json_schema", "schema"),
    )
    description: str = ""
