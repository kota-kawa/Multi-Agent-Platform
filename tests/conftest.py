import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from multi_agent_app import create_app


@pytest.fixture()
def app():
    flask_app = create_app()
    flask_app.config.update({"TESTING": True})
    yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()
