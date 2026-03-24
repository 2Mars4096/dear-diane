"""Compatibility wrapper for the canonical runtime-owned text helper."""

from __future__ import annotations

import sys

from dan.agent_runtime import text_runtime as _text_runtime

sys.modules[__name__] = _text_runtime
