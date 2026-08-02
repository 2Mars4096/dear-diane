"""Tests for DanBlock, BlockDependency, InstalledBlock models."""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.blocks.models import BlockDependency, DanBlock, InstalledBlock


class TestDanBlock:
    def test_valid_minimal(self) -> None:
        b = DanBlock(name="my-block")
        assert b.name == "my-block"
        assert b.version == "0.1.0"
        assert b.block_type == "workflow"

    def test_valid_full(self) -> None:
        b = DanBlock(
            name="analyzer",
            version="1.2.3",
            description="An analysis block",
            author="Alice",
            license="MIT",
            tags=["analysis", "llm"],
            block_type="composite",
            entry_point="graph.json",
            dependencies=[BlockDependency(name="dep-a", version="0.1.0")],
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"y": {"type": "string"}}},
        )
        assert b.version == "1.2.3"
        assert b.block_type == "composite"
        assert len(b.dependencies) == 1

    def test_invalid_version(self) -> None:
        with pytest.raises(ValueError, match="semver"):
            DanBlock(name="bad", version="not-semver")

    def test_invalid_version_partial(self) -> None:
        with pytest.raises(ValueError, match="semver"):
            DanBlock(name="bad", version="1.2")

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            DanBlock(name="")

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            DanBlock(name="   ")

    def test_name_stripped(self) -> None:
        b = DanBlock(name="  hello  ")
        assert b.name == "hello"

    def test_semver_with_prerelease(self) -> None:
        b = DanBlock(name="x", version="1.0.0-alpha.1")
        assert b.version == "1.0.0-alpha.1"

    def test_semver_with_build(self) -> None:
        b = DanBlock(name="x", version="1.0.0+build.123")
        assert b.version == "1.0.0+build.123"

    def test_block_type_enum(self) -> None:
        for bt in ("composite", "workflow", "agent_collection"):
            b = DanBlock(name="x", block_type=bt)
            assert b.block_type == bt


class TestBlockDependency:
    def test_roundtrip(self) -> None:
        d = BlockDependency(name="dep", version="1.0.0")
        assert d.model_dump() == {"name": "dep", "version": "1.0.0"}


class TestInstalledBlock:
    def test_basic(self) -> None:
        m = DanBlock(name="x", version="0.1.0")
        ib = InstalledBlock(
            name="x",
            version="0.1.0",
            install_path=Path("/tmp/x"),
            metadata=m,
        )
        assert ib.block_type == "workflow"
        assert ib.install_path == Path("/tmp/x")
