"""全局配置：环境变量 / .env 读取与路径解析。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AIOps 机房智能告警诊断平台"
    version: str = "0.1.0"
    environment: str = "dev"
    log_level: str = "INFO"

    # 存储：留空则使用仓库根目录 data/aiops.db
    database_url: str = ""

    # 时间窗（秒）
    dedup_window_seconds: int = 300
    cluster_window_seconds: int = 300
    cluster_cooldown_seconds: int = 600
    cluster_reopen_seconds: int = 1800
    correlation_window_seconds: int = 180
    incident_cooldown_seconds: int = 600

    # LLM
    llm_mode: Literal["auto", "llm", "mock"] = "auto"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 1
    llm_temperature: float = 0.1

    # 证据预算与置信度
    evidence_max_alerts: int = 20
    evidence_max_log_lines: int = 40
    evidence_max_chars: int = 8000
    review_confidence_threshold: float = 0.45
    degraded_confidence_cap: float = 0.6

    # 安全
    webhook_token: str = ""
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://127.0.0.1:8000"
    )

    # 文件路径（相对于仓库根目录）
    topology_file: str = "config/topology.yaml"
    rules_file: str = "config/rules.yaml"
    decision_model_file: str = "config/decision_model.yaml"
    scenarios_dir: str = "scenarios"
    frontend_dist: str = "frontend/dist"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url.strip():
            return self.database_url.strip()
        db_path = REPO_ROOT / "data" / "aiops.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path}"

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key.strip() and self.llm_base_url.strip())

    @property
    def effective_llm_mode(self) -> str:
        """auto 模式下按是否配置凭据解析出实际模式。"""
        if self.llm_mode == "auto":
            return "llm" if self.llm_configured else "mock"
        return self.llm_mode


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
