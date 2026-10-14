"""Test setup: an isolated data directory and a known owner password, set before the app is imported."""

import os
import tempfile
from datetime import date
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="fa-test-"))
os.environ.setdefault("FA_DATA_DIR", str(_tmp))
os.environ.setdefault("FA_PASSWORD", "correct horse battery")
os.environ.setdefault("FA_USERNAME", "owner")
os.environ.setdefault("FA_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("FA_DATA_KEY", "test-data-key")
os.environ.setdefault("FA_JOBS", "false")
os.environ.setdefault("FA_DEMO", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import auth  # noqa: E402
from app.api import routes  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402

TODAY = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    init_db()
    auth.reset_lockouts()
    routes.set_today_override(TODAY)
    yield
    routes.set_today_override(None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def owner(client: TestClient) -> TestClient:
    response = client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
    assert response.status_code == 200, response.text
    client.headers["X-Requested-With"] = "FinancialAuthority"
    return client
