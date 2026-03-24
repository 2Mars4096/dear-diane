from __future__ import annotations

import importlib.util
from pathlib import Path

import dan.server.concierge.runtime as runtime_module


def test_runtime_import_path_resolves_to_package_module() -> None:
    spec = importlib.util.find_spec("dan.server.concierge.runtime")

    assert spec is not None
    assert spec.origin is not None

    origin = Path(spec.origin).resolve()
    module_file = Path(runtime_module.__file__).resolve()

    assert origin == module_file
    assert origin.name == "__init__.py"
    assert origin.parent.name == "runtime"
    assert hasattr(runtime_module, "Concierge")
    assert hasattr(runtime_module, "build_concierge")
    assert hasattr(runtime_module, "build_memory_services")
