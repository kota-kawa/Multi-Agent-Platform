"""Custom exception classes used throughout the Multi-Agent Platform."""

from __future__ import annotations


class LifestyleAPIError(RuntimeError):
    """Raised when the upstream Life-Assistant API responds with an error."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class BrowserAgentError(RuntimeError):
    """Raised when the Browser Agent request cannot be completed."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class IotAgentError(RuntimeError):
    """Raised when the IoT Agent request fails."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class OrchestratorError(RuntimeError):
    """Raised when the orchestrator cannot complete a request."""
