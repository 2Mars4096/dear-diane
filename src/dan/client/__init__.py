"""DanClient — shared thin client library for communicating with dan-serve.

All interaction surfaces (CLI, adapters, MCP) use this library
to dispatch workflows, stream events, and interact with HumanNodes
through the server's gateway API.
"""

from dan.client.client import DanClient
from dan.client.errors import (
    ConnectionError as DanConnectionError,
    DanClientError,
    DispatchError,
    NotFoundError,
    ServerError,
)
from dan.client.local import DanClientOrLocal
from dan.client.models import DispatchResult, PendingInput, RunSummary

__all__ = [
    "DanClient",
    "DanClientOrLocal",
    "DanClientError",
    "DanConnectionError",
    "DispatchError",
    "NotFoundError",
    "ServerError",
    "DispatchResult",
    "PendingInput",
    "RunSummary",
]
