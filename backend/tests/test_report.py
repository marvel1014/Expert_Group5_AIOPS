"""报告模块测试：章节完整性、风险标注、版本化、导出与降级报告。"""

from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import AppError
from app.db import session_scope
from app.diagnosis.llm import LLMError
from app.diagnosis.service import run_diagnosis_sync
from app.ingestion.service import ingest_alerts
from app.models import Incident
from app.reporting import service as reporting_service
from app.reporting.service import generate_report
from app.topology.service import correlate_pending_alerts

ALERTS = [
    {
        "host": "db-host-01",
        "service": "mysql-primary",
        "rule": "MySQL connection pool exhausted",
        "severity": "critical",
        "message": "Too many connections (151/151)",
    },
    {
        "host": "db-host-01",
        "service": "mysql-primary",
        "rule": "Query latency high on mysql-primary",
        "severity": "high",
        "message": "p99 latency 2.4s",
    },
]


def _incident_id(client) -> int:
    response = client.post("/api/v1/alerts/webhook", json={"alerts": ALERTS})
    assert response.status_code == 202
    return client.get("/api/v1/incidents").json()["items"][0]["id"]


def _diagnosis(client, incident_id: int) -> dict:
    response = client.post(f"/api/v1/incidents/{incident_id}/diagnose")
    assert response.status_code == 200
    diagnosis_id = response.json()["diagnosis_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        data = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()
        if data["status"] not in ("pending", "running"):
            return data
        time.sleep(0.05)
    raise AssertionError("诊断未在超时时间内完成")


def _create_report(client, incident_id: int, diagnosis_id: int | None = None) -> dict:
    body = {"diagnosis_id": diagnosis_id} if diagnosis_id else {}
    response = client.post(f"/api/v1/incidents/{incident_id}/reports", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_report_sections_and_risk_annotation(client):
    incident_id = _incident_id(client)
    diagnosis = _diagnosis(client, incident_id)
    report = _create_report(client, incident_id, diagnosis["id"])

    assert report["incident_id"] == incident_id
    assert report["diagnosis_id"] == diagnosis["id"]
    assert report["version"] == 1
    assert report["risk_level"] in ("low", "medium", "high")
    content = report["content"]
    for key in (
        "summary",
        "impact",
        "root_cause",
        "evidence",
        "steps",
        "verification",
        "rollback",
        "risk_notice",
    ):
        assert content.get(key), f"缺少章节: {key}"

    steps = content["steps"]
    assert len(steps) >= 3
    assert [step["order"] for step in steps] == list(range(1, len(steps) + 1))
    for step in steps:
        for key in ("title", "action", "expected", "risk", "rollback"):
            assert step.get(key), f"步骤缺少字段 {key}"
        assert step["risk"] in ("low", "medium", "high")

    assert report["risk_level"] == "high"
    assert any(step["risk"] == "high" for step in steps)
    assert any(step.get("manual_confirm") for step in steps if step["risk"] == "high")
    assert "人工确认" in content["risk_notice"]

    markdown = report["markdown"]
    for title in (
        "一、故障摘要",
        "二、影响范围",
        "三、根因结论",
        "四、证据引用",
        "五、分步排查与修复",
        "六、验证方式",
        "七、回滚方案",
        "八、风险提示",
    ):
        assert title in markdown
    assert markdown.startswith("# 故障诊断报告")


def test_report_versioning_keeps_history(client):
    incident_id = _incident_id(client)
    diagnosis = _diagnosis(client, incident_id)
    first = _create_report(client, incident_id, diagnosis["id"])
    second = _create_report(client, incident_id, diagnosis["id"])
    assert first["version"] == 1
    assert second["version"] == 2
    old = client.get(f"/api/v1/reports/{first['id']}")
    assert old.status_code == 200
    assert old.json()["version"] == 1
    assert client.get(f"/api/v1/reports/{second['id']}").json()["version"] == 2


def test_report_export_markdown_and_json(client):
    incident_id = _incident_id(client)
    diagnosis = _diagnosis(client, incident_id)
    report = _create_report(client, incident_id, diagnosis["id"])

    markdown = client.get(f"/api/v1/reports/{report['id']}/export?format=markdown")
    assert markdown.status_code == 200
    disposition = markdown.headers["content-disposition"]
    assert f"report-incident{incident_id}-v{report['version']}.md" in disposition
    text = markdown.content.decode("utf-8")
    assert text.startswith("# 故障诊断报告")
    assert "风险提示" in text

    exported = client.get(f"/api/v1/reports/{report['id']}/export?format=json")
    assert exported.status_code == 200
    payload = json.loads(exported.content.decode("utf-8"))
    assert payload["version"] == report["version"]
    assert payload["incident_id"] == incident_id
    assert payload["content"]["summary"] == report["content"]["summary"]
    assert f"report-incident{incident_id}-v{report['version']}.json" in exported.headers[
        "content-disposition"
    ]


def test_export_missing_or_bad_format(client):
    missing = client.get("/api/v1/reports/999999/export?format=markdown")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    incident_id = _incident_id(client)
    diagnosis = _diagnosis(client, incident_id)
    report = _create_report(client, incident_id, diagnosis["id"])
    bad = client.get(f"/api/v1/reports/{report['id']}/export?format=pdf")
    assert bad.status_code == 422


def test_report_degraded_marks_review(client, db_session, monkeypatch):
    incident_id = _incident_id(client)
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    db_session.expire_all()
    incident = db_session.get(Incident, incident_id)
    assert incident is not None

    class _Failing:
        def complete(self, messages, *, response_format=True):  # noqa: ANN001
            raise LLMError("timeout", kind="timeout", retryable=True)

    diagnosis = run_diagnosis_sync(db_session, incident, mode="llm", client=_Failing())
    assert diagnosis.status == "degraded"

    report = _create_report(client, incident_id, diagnosis.id)
    content = report["content"]
    assert content.get("review_notice")
    assert "复核" in content["review_notice"]
    assert content["diagnosis_source"] == "degraded"
    assert "降级" in content["root_cause"] or "可能" in content["root_cause"]
    assert "复核" in report["markdown"]


def test_report_llm_failure_falls_back_to_template(monkeypatch):
    with session_scope() as db:
        ingest_alerts(db, ALERTS)
        correlate_pending_alerts(db)
        incident = db.execute(select(Incident)).scalars().first()
        assert incident is not None
        run_diagnosis_sync(db, incident, mode="mock")

        def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
            raise LLMError("bad json", kind="invalid_json")

        monkeypatch.setattr(settings, "llm_api_key", "test-key")
        monkeypatch.setattr(settings, "llm_base_url", "http://mock-llm")
        monkeypatch.setattr(reporting_service, "_llm_generate_steps", _boom)
        report = generate_report(db, incident.id, mode="llm")
        assert report.content["steps"]
        assert report.content["llm_enhanced"] is False
        for step in report.content["steps"]:
            assert step["action"] and step["expected"] and step["rollback"]
        assert report.risk_level in ("low", "medium", "high")


def test_generate_report_requires_diagnosis():
    with session_scope() as db:
        ingest_alerts(db, ALERTS)
        correlate_pending_alerts(db)
        incident = db.execute(select(Incident)).scalars().first()
        assert incident is not None
        with pytest.raises(AppError) as exc_info:
            generate_report(db, incident.id)
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "diagnosis_not_found"


def test_report_unknown_diagnosis_returns_404(client):
    incident_id = _incident_id(client)
    response = client.post(
        f"/api/v1/incidents/{incident_id}/reports",
        json={"diagnosis_id": 999999},
    )
    assert response.status_code == 404


def test_report_uses_latest_diagnosis_by_default(client):
    incident_id = _incident_id(client)
    diagnosis = _diagnosis(client, incident_id)
    response = client.post(f"/api/v1/incidents/{incident_id}/reports", json={})
    assert response.status_code == 201
    assert response.json()["diagnosis_id"] == diagnosis["id"]


def test_report_llm_enhanced_steps_force_high_risk(monkeypatch):
    with session_scope() as db:
        ingest_alerts(db, ALERTS)
        correlate_pending_alerts(db)
        incident = db.execute(select(Incident)).scalars().first()
        assert incident is not None
        run_diagnosis_sync(db, incident, mode="mock")

        def _fake_steps(*args, **kwargs):  # noqa: ANN002, ANN003
            return [
                {
                    "title": "检查连接池",
                    "action": "查看 SHOW PROCESSLIST 与 Threads_connected",
                    "expected": "定位占用连接的会话",
                    "risk": "low",
                    "rollback": "只读操作，无需回滚",
                },
                {
                    "title": "重启数据库释放连接",
                    "action": "重启 mysql-primary 服务使连接池配置生效",
                    "expected": "连接数回落，业务恢复",
                    "risk": "medium",
                    "rollback": "恢复原配置并重启服务",
                },
            ]

        monkeypatch.setattr(settings, "llm_api_key", "test-key")
        monkeypatch.setattr(settings, "llm_base_url", "http://mock-llm")
        monkeypatch.setattr(reporting_service, "_llm_generate_steps", _fake_steps)
        report = generate_report(db, incident.id, mode="llm")
        assert report.content["llm_enhanced"] is True
        steps = report.content["steps"]
        assert steps[0]["risk"] == "low"
        high_steps = [step for step in steps if step["risk"] == "high"]
        assert high_steps, "包含『重启』关键词的步骤必须被强制标记为高风险"
        assert high_steps[0]["manual_confirm"] is True
        assert report.risk_level == "high"
