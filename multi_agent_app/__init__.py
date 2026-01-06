"""Application factory for Polyphony (FastAPI)."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from jinja2 import pass_context
from starlette.templating import Jinja2Templates

from .routes import router

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent.parent


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI()
    app.state.base_dir = BASE_DIR

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    @pass_context
    def _url_for(context, name: str, **params):  # type: ignore[no-untyped-def]
        request = context.get("request")
        if name == "static":
            filename = params.get("filename") or params.get("path") or ""
            filename = str(filename).lstrip("/")
            return f"/assets/{filename}"
        if request is None:
            return ""

        path_params: dict[str, Any] = {}
        query_params: dict[str, Any] = {}
        route = next((r for r in request.app.router.routes if getattr(r, "name", None) == name), None)
        if route and getattr(route, "param_convertors", None):
            for key, value in params.items():
                if value is None:
                    continue
                if key in route.param_convertors:
                    path_params[key] = value
                else:
                    query_params[key] = value
        else:
            path_params = {k: v for k, v in params.items() if v is not None}

        url = request.url_for(name, **path_params)
        if query_params:
            return f"{url}?{urlencode(query_params, doseq=True)}"
        return str(url)

    templates.env.globals["url_for"] = _url_for
    app.state.templates = templates

    app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="static")
    app.include_router(router)
    return app
