"""MemoryKernel domain consolidation hook (41-4)."""

from __future__ import annotations

from unittest.mock import MagicMock

from dan.engine.memory_kernel import MemoryKernel


def test_consolidate_without_hook_returns_zero(tmp_path) -> None:
    k = MemoryKernel(base_dir=str(tmp_path / "mk"))
    assert k._domain_consolidation_hook is None
    assert k._consolidate_domain_templates() == 0


def test_consolidate_delegates_to_hook(tmp_path) -> None:
    hook = MagicMock(return_value=3)
    k = MemoryKernel(base_dir=str(tmp_path / "mk2"), domain_consolidation_hook=hook)
    assert k._consolidate_domain_templates() == 3
    hook.assert_called_once_with(k)


def test_consolidate_hook_exception_returns_zero(tmp_path) -> None:
    def _boom(kernel: MemoryKernel) -> int:
        raise RuntimeError("fail")

    k = MemoryKernel(base_dir=str(tmp_path / "mk3"), domain_consolidation_hook=_boom)
    assert k._consolidate_domain_templates() == 0
