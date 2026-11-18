"""接警与日志入参 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AlertIn(BaseModel):
    source: str = "webhook"
    host: str = Field(min_length=1, max_length=128)
    service: str | None = Field(default=None, max_length=128)
    rule: str = Field(min_length=1, max_length=256)
    severity: str = "medium"
    message: str = ""
    labels: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    batch_id: str | None = None


class LogIn(BaseModel):
    ts: datetime | None = None
    host: str = Field(min_length=1, max_length=128)
    service: str | None = Field(default=None, max_length=128)
    level: str = "ERROR"
    message: str = ""
    stack: str = ""
    trace_id: str | None = Field(default=None, max_length=64)
    alert_id: int | None = None
    batch_id: str | None = None
