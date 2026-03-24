"""Compatibility shim for agent-runtime graph summary helpers."""

from __future__ import annotations

import sys

from dan.agent_runtime import graph_summary as _graph_summary

sys.modules[__name__] = _graph_summary
