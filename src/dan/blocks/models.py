"""Data models for the DAN block packaging system."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[\w.]+)?(?:\+[\w.]+)?$")

MANIFEST_FILENAME = "dan-block.json"
GRAPH_FILENAME = "graph.json"
AGENTS_DIR = "agents"
TARBALL_SUFFIX = ".dan-block.tar.gz"


class BlockDependency(BaseModel):
    """A named dependency on another installed block."""

    name: str
    version: str


class DanBlock(BaseModel):
    """Manifest model stored as ``dan-block.json`` inside a block directory."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    license: str = ""
    tags: list[str] = Field(default_factory=list)
    block_type: Literal["composite", "workflow", "agent_collection"] = "workflow"
    entry_point: str = GRAPH_FILENAME
    dependencies: list[BlockDependency] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"Version must be semver (got {v!r})")
        return v

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Block name must not be empty")
        return v.strip()


class InstalledBlock(BaseModel):
    """Metadata for a block that has been installed locally."""

    name: str
    version: str
    install_path: Path
    block_type: Literal["composite", "workflow", "agent_collection"] = "workflow"
    metadata: DanBlock
