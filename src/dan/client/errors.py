"""Error hierarchy for DanClient."""


class DanClientError(Exception):
    """Base error for all DanClient operations."""


class ConnectionError(DanClientError):
    """Server is unreachable."""


class DispatchError(DanClientError):
    """Dispatch failed (4xx from server)."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class NotFoundError(DanClientError):
    """Resource not found (404)."""


class ServerError(DanClientError):
    """Server-side error (5xx)."""


class RunLostError(DanClientError):
    """Server connection lost mid-run — run cannot be recovered locally."""

    def __init__(self, run_id: str):
        super().__init__(
            f"Server connection lost. Run {run_id} was executing on the "
            f"server and cannot be recovered locally."
        )
        self.run_id = run_id
