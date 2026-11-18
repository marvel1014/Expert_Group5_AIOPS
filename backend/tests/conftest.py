"""pytest 公共夹具：临时 SQLite、TestClient 与每用例清理。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="aiops-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR / 'test.db'}"
os.environ["LLM_MODE"] = "mock"
os.environ["LLM_API_KEY"] = ""
os.environ["WEBHOOK_TOKEN"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, get_engine, get_session_factory  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clean_database(client):
    """每个用例前清空业务表；场景库由启动时载入，作为只读种子保留。"""
    engine = get_engine()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name == "scenarios":
                continue
            conn.execute(table.delete())
    yield


@pytest.fixture()
def db_session():
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def post_alert(client):
    """便捷提交告警；默认字段可被覆盖。"""

    def _post(**overrides):
        payload = {
            "source": "webhook",
            "host": "db-host-01",
            "service": "mysql-primary",
            "rule": "MySQL connection pool exhausted",
            "severity": "critical",
            "message": "Too many connections",
            "labels": {"env": "test"},
        }
        payload.update(overrides)
        return client.post("/api/v1/alerts/webhook", json=payload)

    return _post
