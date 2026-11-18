"""诊断模块测试：决策模型、证据预算、脱敏、LLM 降级、状态机与审计字段。"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from app.core.config import settings
from app.core.errors import AppError
from app.diagnosis.decision import load_decision_model, score_incident
from app.diagnosis.llm import (
    LLMError,
    LLMResponse,
    OpenAICompatibleClient,
    parse_json_object,
)
from app.diagnosis.service import (
    PROMPT_FILE,
    build_evidence,
    get_latest_diagnosis,
    load_prompt,
    recover_stale_diagnoses,
    run_diagnosis_sync,
    sanitize_text,
    serialize_diagnosis,
    start_diagnosis,
)
from app.models import Diagnosis, Incident


def _now() -> datetime:
    return datetime.now(timezone.utc)


DB_ALERTS = [
    {
        "host": "db-host-01",
        "service": "mysql-primary",
        "rule": "MySQL connection pool exhausted",
        "severity": "critical",
        "message": "Too many connections (151/151) while acquiring pooled connection",
    },
    {
        "host": "db-host-01",
        "service": "mysql-primary",
        "rule": "Query latency high on mysql-primary",
        "severity": "high",
        "message": "p99 latency 2.4s",
    },
]


def _db_logs() -> list[dict]:
    now = _now()
    return [
        {
            "ts": (now - timedelta(seconds=30)).isoformat(),
            "host": "db-host-01",
            "service": "mysql-primary",
            "level": "ERROR",
            "message": "TooManyConnectionsError: too many connections (151/151)",
            "stack": "Traceback (most recent call last):\n  File \"/srv/app/db.py\", line 88\nTooManyConnectionsError: too many connections\npassword=ProdPass2026! token=sk-live-abc123 phone 13800138000",
            "trace_id": "trace-db-001",
        }
    ]


def _create_incident(client, alerts=None, logs=None) -> int:
    response = client.post(
        "/api/v1/alerts/webhook", json={"alerts": alerts or DB_ALERTS}
    )
    assert response.status_code == 202, response.text
    if logs:
        log_response = client.post("/api/v1/logs", json={"logs": logs})
        assert log_response.status_code == 202, log_response.text
    items = client.get("/api/v1/incidents").json()["items"]
    assert items, "告警未关联出故障事件"
    return items[0]["id"]


def _poll_diagnosis(client, diagnosis_id: int, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    data = {}
    while time.time() < deadline:
        data = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()
        if data["status"] not in ("pending", "running"):
            return data
        time.sleep(0.05)
    return data


def _incident(db_session, incident_id: int) -> Incident:
    db_session.expire_all()
    incident = db_session.get(Incident, incident_id)
    assert incident is not None
    return incident


# ------------------------------ 决策模型 ------------------------------


def test_fast_diagnosis_deterministic_and_fast(client):
    incident_id = _create_incident(client)
    first = client.get(f"/api/v1/incidents/{incident_id}/fast-diagnosis")
    second = client.get(f"/api/v1/incidents/{incident_id}/fast-diagnosis")
    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert payload["model_version"]
    assert payload["incident_id"] == incident_id
    candidates = payload["candidates"]
    assert candidates and len(candidates) <= 3
    assert candidates[0]["category"] == "db_connection_pool_exhausted"
    scores = [item["score"] for item in candidates]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= item["confidence"] <= 1 for item in candidates)
    assert candidates[0]["features"]


def test_decision_model_config_validation(tmp_path):
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("version: bad\ncategories: {}\n", encoding="utf-8")
    with pytest.raises(AppError) as exc_info:
        load_decision_model(str(invalid))
    assert exc_info.value.code == "invalid_decision_model"
    assert exc_info.value.details

    valid = tmp_path / "valid.yaml"
    valid.write_text(
        "version: test-v1\n"
        "defaults: {no_match_prior: 0.01, severity_boost: 0.05, max_score: 1.0, top_n: 3}\n"
        "categories:\n"
        "  cpu_overload:\n"
        "    label: CPU 过载\n"
        "    rule_keywords:\n"
        "      - {pattern: 'cpu usage', weight: 0.5}\n",
        encoding="utf-8",
    )
    model = load_decision_model(str(valid))
    assert model.version == "test-v1"
    assert "cpu_overload" in model.categories


def test_decision_model_default_config_loads():
    model = load_decision_model()
    assert model.version
    for name in (
        "db_connection_pool_exhausted",
        "network_partition",
        "disk_full",
        "cpu_overload",
        "service_crash",
        "config_error",
        "dependency_timeout",
    ):
        assert name in model.categories


def test_score_incident_latency(client, db_session):
    incident_id = _create_incident(client)
    incident = _incident(db_session, incident_id)
    started = time.monotonic()
    result = score_incident(db_session, incident)
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert result["candidates"]


# ------------------------------ 诊断主链路 ------------------------------


def test_mock_diagnosis_audit_and_redaction(client):
    incident_id = _create_incident(client, logs=_db_logs())
    response = client.post(f"/api/v1/incidents/{incident_id}/diagnose")
    assert response.status_code == 200
    body = response.json()
    assert body["incident_id"] == incident_id
    assert body["status"] in ("pending", "running", "succeeded", "degraded")

    diagnosis = _poll_diagnosis(client, body["diagnosis_id"])
    assert diagnosis["status"] == "succeeded"
    assert diagnosis["source"] == "mock"
    assert diagnosis["root_cause_category"] == "db_connection_pool_exhausted"
    assert diagnosis["root_cause_node"] == "mysql-primary"
    assert 0 <= diagnosis["confidence"] <= 1
    assert diagnosis["reasoning"]
    assert diagnosis["prompt_version"].startswith("rca_v1:")
    assert diagnosis["model"]
    assert diagnosis["latency_ms"] >= 0
    assert isinstance(diagnosis["token_usage"], dict)
    assert diagnosis["config_snapshot"]
    assert len(diagnosis["evidence"]["alerts"]) <= settings.evidence_max_alerts
    assert len(diagnosis["evidence"]["logs"]) <= settings.evidence_max_log_lines
    assert diagnosis["evidence"]["refs"]["alert_ids"]
    assert diagnosis["evidence"]["cited"]["alert_ids"]

    serialized = json.dumps(diagnosis, ensure_ascii=False)
    assert "ProdPass2026" not in serialized
    assert "sk-live-abc123" not in serialized
    assert "13800138000" not in serialized


def test_prompt_version_matches_content_hash():
    version, system, user = load_prompt()
    digest = hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest()[:8]
    assert version == f"rca_v1:{digest}"
    assert system and user and "{{payload}}" in user


def test_diagnosis_idempotent_reuse_and_rerun(client):
    incident_id = _create_incident(client)
    first = client.post(f"/api/v1/incidents/{incident_id}/diagnose").json()
    _poll_diagnosis(client, first["diagnosis_id"])
    second = client.post(f"/api/v1/incidents/{incident_id}/diagnose").json()
    assert second["diagnosis_id"] == first["diagnosis_id"]

    rerun = client.post(
        f"/api/v1/incidents/{incident_id}/diagnose?rerun=true"
    ).json()
    assert rerun["diagnosis_id"] != first["diagnosis_id"]
    _poll_diagnosis(client, rerun["diagnosis_id"])

    history = client.get(f"/api/v1/incidents/{incident_id}/diagnoses").json()
    assert history["total"] == 2
    versions = [item["version"] for item in history["items"]]
    assert versions == sorted(versions, reverse=True)
    assert versions[0] == 2
    assert history["items"][0]["id"] == rerun["diagnosis_id"]


def test_missing_logs_marks_missing_evidence(client, db_session):
    incident_id = _create_incident(client)
    incident = _incident(db_session, incident_id)
    diagnosis = run_diagnosis_sync(db_session, incident, mode="mock")
    assert diagnosis.status == "succeeded"
    assert diagnosis.missing_evidence
    assert any("日志" in item for item in diagnosis.missing_evidence)
    assert diagnosis.needs_review is True


def test_evidence_budget_limits(client, db_session):
    words = [
        "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
        "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
        "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
        "victor", "whiskey", "xray", "yankee", "zulu", "anchor", "beacon",
        "cinder", "drift", "ember", "fable", "grove", "harbor", "ivory",
        "jolly", "karma", "lumen", "mango", "nectar",
    ]
    alerts = [
        {
            "host": "db-host-01",
            "service": "mysql-primary",
            "rule": f"Custom alert {word} detected",
            "severity": "high",
            "message": f"custom noisy alert {word} " + "x" * 80,
        }
        for word in words
    ]
    logs = [
        {
            "ts": (_now() - timedelta(seconds=index)).isoformat(),
            "host": "db-host-01",
            "service": "mysql-primary",
            "level": "ERROR",
            "message": f"error sample {index} " + "y" * 100,
            "stack": "Traceback (most recent call last):\n" + "frame\n" * 40,
        }
        for index in range(60)
    ]
    incident_id = _create_incident(client, alerts=alerts, logs=logs)
    incident = _incident(db_session, incident_id)
    evidence = build_evidence(db_session, incident)
    assert 0 < len(evidence["alerts"]) <= settings.evidence_max_alerts
    assert 0 < len(evidence["logs"]) <= settings.evidence_max_log_lines
    assert evidence["budget"]["used_chars"] <= settings.evidence_max_chars
    alert_ids = {item["id"] for item in evidence["alerts"]}
    log_ids = {item["id"] for item in evidence["logs"]}
    assert set(evidence["refs"]["alert_ids"]) == alert_ids
    assert set(evidence["refs"]["log_ids"]) == log_ids


# ------------------------------ LLM 适配层 ------------------------------


def test_llm_client_timeout_classified():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = OpenAICompatibleClient(
        base_url="http://mock-llm",
        api_key="test-key",
        model="mock-model",
        timeout=1.0,
        max_retries=1,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMError) as exc_info:
        client.complete([{"role": "user", "content": "hi"}])
    assert exc_info.value.kind == "timeout"
    assert exc_info.value.retryable is True


def test_llm_client_rate_limit_retry_then_success():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200,
            json={
                "model": "mock-model",
                "choices": [{"message": {"content": "{\"ok\": true}"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
        )

    client = OpenAICompatibleClient(
        base_url="http://mock-llm",
        api_key="test-key",
        model="mock-model",
        timeout=1.0,
        max_retries=1,
        transport=httpx.MockTransport(handler),
    )
    response = client.complete([{"role": "user", "content": "hi"}])
    assert calls["count"] == 2
    assert response.content == '{"ok": true}'
    assert response.token_usage["total_tokens"] == 7
    assert response.attempts == 2


class _FailingClient:
    def __init__(self, error: LLMError) -> None:
        self.error = error
        self.calls = 0

    def complete(self, messages, *, response_format=True):  # noqa: ANN001
        self.calls += 1
        raise self.error


class _ScriptedClient:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls = 0

    def complete(self, messages, *, response_format=True):  # noqa: ANN001
        content = self.contents[min(self.calls, len(self.contents) - 1)]
        self.calls += 1
        return LLMResponse(content=content, model="scripted", token_usage={})


def _llm_ready(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_base_url", "http://mock-llm")


def test_llm_timeout_degrades(client, db_session, monkeypatch):
    _llm_ready(monkeypatch)
    incident_id = _create_incident(client)
    incident = _incident(db_session, incident_id)
    fake = _FailingClient(LLMError("timeout", kind="timeout", retryable=True))
    diagnosis = run_diagnosis_sync(
        db_session, incident, mode="llm", client=fake
    )
    assert fake.calls == 1
    assert diagnosis.status == "degraded"
    assert diagnosis.source == "degraded"
    assert diagnosis.confidence <= settings.degraded_confidence_cap
    assert diagnosis.error
    assert "timeout" in (diagnosis.config_snapshot.get("degraded_reason") or "")
    assert diagnosis.needs_review is True
    assert diagnosis.root_cause_category


def test_invalid_llm_output_repair_then_degrade(client, db_session, monkeypatch):
    _llm_ready(monkeypatch)
    incident_id = _create_incident(client)
    incident = _incident(db_session, incident_id)
    fake = _ScriptedClient(["这不是 JSON", '{"root_cause_category": "unknown"}'])
    diagnosis = run_diagnosis_sync(db_session, incident, mode="llm", client=fake)
    assert fake.calls == 2  # 首次 + 一次修复重试
    assert diagnosis.status == "degraded"
    assert diagnosis.source == "degraded"
    errors = diagnosis.config_snapshot.get("validation_errors") or []
    assert errors
    assert diagnosis.root_cause_category == "db_connection_pool_exhausted"


def test_llm_not_configured_degrades(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    incident_id = _create_incident(client)
    incident = _incident(db_session, incident_id)
    diagnosis = run_diagnosis_sync(db_session, incident, mode="llm")
    assert diagnosis.status == "degraded"
    assert diagnosis.config_snapshot.get("degraded_reason") == "llm_not_configured"


def test_mock_mode_is_deterministic(client, db_session):
    incident_id = _create_incident(client)
    first = run_diagnosis_sync(db_session, _incident(db_session, incident_id), mode="mock")
    second = run_diagnosis_sync(db_session, _incident(db_session, incident_id), mode="mock")
    assert first.root_cause_category == second.root_cause_category
    assert first.root_cause_node == second.root_cause_node
    assert first.confidence == second.confidence
    assert first.reasoning == second.reasoning
    assert first.source == second.source == "mock"


# ------------------------------ 状态机 ------------------------------


def test_start_diagnosis_sync_context(client, db_session):
    incident_id = _create_incident(client)
    incident = _incident(db_session, incident_id)
    diagnosis = start_diagnosis(db_session, incident_id)
    assert diagnosis.id is not None
    latest = get_latest_diagnosis(db_session, incident_id)
    assert latest is not None
    assert latest.status in ("succeeded", "degraded")
    assert serialize_diagnosis(latest)["incident_id"] == incident_id


def test_recover_stale_diagnoses(client, db_session):
    incident_id = _create_incident(client)
    stale = Diagnosis(
        incident_id=incident_id,
        version=99,
        status="running",
        source="pending",
    )
    db_session.add(stale)
    db_session.commit()
    count = recover_stale_diagnoses(db_session)
    assert count >= 1
    db_session.expire_all()
    assert db_session.get(Diagnosis, stale.id).status == "failed"


def test_redaction_placeholders():
    raw = (
        "password=ProdPass2026! token: sk-live-abc123 api_key=abc "
        "phone 13800138000 id 110101199001011234 Authorization: Bearer abcdefgh12345678"
    )
    clean = sanitize_text(raw)
    for secret in (
        "ProdPass2026",
        "sk-live-abc123",
        "13800138000",
        "110101199001011234",
        "abcdefgh12345678",
    ):
        assert secret not in clean
    assert "***REDACTED***" in clean
    assert "***PHONE***" in clean
    assert "***ID***" in clean


def test_parse_json_object_tolerates_fences():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('前缀 {"a": 2} 后缀') == {"a": 2}
    with pytest.raises(LLMError):
        parse_json_object("not json at all")
