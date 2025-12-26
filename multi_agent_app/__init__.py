"""Application factory for Polyphony."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask

from .routes import bp

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent.parent


def create_app() -> Flask:
    """Create and configure the Flask application."""

    flask_app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "assets"),
        static_url_path="/assets",
        template_folder=str(BASE_DIR / "templates"),
    )
    flask_app.config["APP_BASE_PATH"] = str(BASE_DIR)
    flask_app.register_blueprint(bp)
    return flask_app
