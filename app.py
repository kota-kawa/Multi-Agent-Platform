"""Application entrypoint for Symphony."""

from __future__ import annotations

from app_module import create_app

app = create_app()


if __name__ == "__main__":
    import os
    import uvicorn

    reload_enabled = os.environ.get("UVICORN_RELOAD", "").lower() in {"1", "true", "yes", "on"}
    uvicorn.run("app:app", host="0.0.0.0", port=5050, reload=reload_enabled)
