"""Application entrypoint for Polyphony."""

from __future__ import annotations

from app_module import create_app

app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
