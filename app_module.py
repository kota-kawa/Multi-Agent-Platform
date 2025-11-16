"""Flask application module that exposes the create_app factory."""

from __future__ import annotations

from multi_agent_app import create_app

app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
