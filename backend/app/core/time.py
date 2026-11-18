"""时间工具：统一使用 UTC 感知时间。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """SQLite 读出的时间可能丢失时区，统一补成 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def floor_to_window(value: datetime, window_seconds: int) -> datetime:
    """把时间向下取整到固定窗口起点。"""
    value = as_utc(value)
    epoch = int(value.timestamp())
    window_seconds = max(1, int(window_seconds))
    start = epoch - (epoch % window_seconds)
    return datetime.fromtimestamp(start, tz=timezone.utc)


class UTCDateTime(TypeDecorator):
    """统一以 UTC 存取：SQLite 中存 naive UTC，读回时补上 UTC 时区。"""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        return as_utc(value).replace(tzinfo=None)

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        return as_utc(value)
