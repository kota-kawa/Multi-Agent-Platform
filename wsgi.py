"""WSGI entrypoint for running under a server like gunicorn."""

from __future__ import annotations

from app_module import create_app

app = create_app()
