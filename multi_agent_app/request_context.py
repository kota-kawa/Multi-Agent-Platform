"""Request-scoped context helpers for FastAPI routes."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Iterable, List

_browser_agent_bases: ContextVar[List[str] | None] = ContextVar("browser_agent_bases", default=None)


def set_browser_agent_bases(bases: Iterable[str] | None) -> Token:
    """Set browser agent base overrides for the current request context."""

    normalized = list(bases) if bases is not None else None
    return _browser_agent_bases.set(normalized)


def reset_browser_agent_bases(token: Token) -> None:
    """Reset browser agent base overrides using the provided context token."""

    _browser_agent_bases.reset(token)


def get_browser_agent_bases() -> List[str] | None:
    """Return browser agent base overrides for the current request context."""

    return _browser_agent_bases.get()
