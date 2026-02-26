"""Language adapters for subprocess sandbox execution.

Each adapter knows how to write a script file and produce the command
line needed to run user-supplied code in a given language.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Protocol

from dan.sandbox import SandboxConfig


class LanguageAdapter(Protocol):
    """Prepares a script file and returns the command to execute it."""

    def prepare(
        self, code: str, config: SandboxConfig, temp_dir: Path
    ) -> tuple[list[str], str]:
        """Return ``(command_args, script_filename)``."""
        ...


_PYTHON_BOOTSTRAP = """\
import json, sys
_inputs = json.loads(open("_inputs.json").read())
locals().update(_inputs)
# --- user code below ---
{user_code}
# --- user code above ---
if "result" in dir():
    json.dump(result, open("_result.json", "w"))
"""


class PythonAdapter:
    """Writes a Python script with bootstrap preamble for input/output."""

    def prepare(
        self, code: str, config: SandboxConfig, temp_dir: Path
    ) -> tuple[list[str], str]:
        script_name = "_script.py"
        script_path = temp_dir / script_name
        script_path.write_text(_PYTHON_BOOTSTRAP.format(user_code=code), encoding="utf-8")
        return [sys.executable, "-u", str(script_path)], script_name


class ShellAdapter:
    """Writes a shell script; inputs are available as environment variables."""

    def prepare(
        self, code: str, config: SandboxConfig, temp_dir: Path
    ) -> tuple[list[str], str]:
        script_name = "_script.sh"
        script_path = temp_dir / script_name
        script_path.write_text(code, encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
        return ["/bin/sh", str(script_path)], script_name


ADAPTERS: dict[str, LanguageAdapter] = {
    "python": PythonAdapter(),
    "shell": ShellAdapter(),
}
