"""Compatibility shim for agent-runtime token helpers."""

from __future__ import annotations

import sys

from dan.agent_runtime import tokens as _tokens

sys.modules[__name__] = _tokens
