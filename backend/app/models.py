"""SQLAlchemy ORM 模型：告警 → 聚合 → 事件 → 诊断 → 报告 全链路。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import UTCDateTime, utcnow
from app.db import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    dedup_key: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(64), default="webhook")
    host: Mapped[str] = mapped_column(String(128), index=True)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    rule: Mapped[str] = mapped_column(String(256))
    rule_family: Mapped[str] = mapped_column(String(128), default="", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1)
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_clusters.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (Index("ix_alerts_fp_time", "fingerprint", "occurred_at"),)


class AlertCluster(Base):
    __tablename__ = "alert_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    host: Mapped[str] = mapped_column(String(128), index=True)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rule: Mapped[str] = mapped_column(String(256))
    rule_family: Mapped[str] = mapped_column(String(128), default="")
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)
    merge_reason: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("fingerprint", "window_start", name="uq_cluster_fp_window"),
    )


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    host: Mapped[str] = mapped_column(String(128), index=True)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(16), default="ERROR")
    message: Mapped[str] = mapped_column(Text, default="")
    stack: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("alerts.id"), nullable=True, index=True
    )
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (Index("ix_logs_host_time", "host", "ts"),)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    window_start: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    window_end: Mapped[datetime] = mapped_column(UTCDateTime)
    nodes: Mapped[list[str]] = mapped_column(JSON, default=list)
    root_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    topology_linked: Mapped[bool] = mapped_column(Boolean, default=False)
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class IncidentAlert(Base):
    __tablename__ = "incident_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id"), index=True
    )
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alerts.id"), unique=True, index=True
    )


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    source: Mapped[str] = mapped_column(String(16), default="pending")
    root_cause_category: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    root_cause_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    missing_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(128), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("incident_id", "version", name="uq_diagnosis_incident_version"),
    )


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    diagnosis_id: Mapped[int] = mapped_column(
        ForeignKey("diagnoses.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    markdown: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("incident_id", "version", name="uq_report_incident_version"),
    )


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    log_count: Mapped[int] = mapped_column(Integer, default=0)
    source_file: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ReplayBatch(Base):
    __tablename__ = "replay_batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("scenarios.id"), index=True
    )
    seed: Mapped[int] = mapped_column(Integer, default=42)
    compression: Mapped[float] = mapped_column(Float, default=10.0)
    status: Mapped[str] = mapped_column(String(16), default="running")
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )


class EvaluationCase(Base):
    __tablename__ = "evaluation_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("evaluations.id"), index=True
    )
    scenario_id: Mapped[str] = mapped_column(String(64))
    expected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actual: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    top1_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    top3_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    node_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
