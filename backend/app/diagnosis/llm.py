"""大模型适配层：OpenAI 兼容 /chat/completions 客户端与确定性 Mock 客户端。

- 只依赖 httpx，不引入厂商 SDK；
- 超时 / 429 / 5xx 归类为可降级错误，401/403 归类为鉴权错误（不重试）；
- 响应统一为 :class:`LLMResponse`，真实模式与 Mock 模式共享同一结构；
- Mock 客户端返回由调用方注入的确定性 JSON，便于评估与测试。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RETRIES = 1

DEFAULT_MOCK_PAYLOAD: dict[str, Any] = {
    "root_cause_category": "dependency_timeout",
    "root_cause_node": None,
    "confidence": 0.3,
    "reasoning": "Mock 模式：未配置大模型凭据，返回确定性占位结论。",
    "affected_nodes": [],
    "evidence_alert_ids": [],
    "evidence_log_ids": [],
    "missing_evidence": ["未配置大模型，未执行真实推理"],
    "needs_review": True,
}


class LLMError(RuntimeError):
    """大模型调用错误，kind 用于归类与降级原因说明。"""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status_code = status_code
        self.retryable = retryable

    def __str__(self) -> str:
        if self.status_code is not None:
            return f"{self.kind}({self.status_code}): {self.message}"
        return f"{self.kind}: {self.message}"


@dataclass
class LLMResponse:
    """统一的大模型响应结构。"""

    content: str
    model: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    attempts: int = 1


def _normalize_usage(raw: Any) -> dict[str, int]:
    usage = raw if isinstance(raw, dict) else {}
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            result[key] = int(value)
    return result


class OpenAICompatibleClient:
    """OpenAI 兼容 /chat/completions 客户端（同步）。

    ``transport`` 可用于测试注入 ``httpx.MockTransport``。
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        temperature: float | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = float(timeout if timeout is not None else settings.llm_timeout_seconds)
        self.max_retries = int(max_retries if max_retries is not None else settings.llm_max_retries)
        self.temperature = float(
            temperature if temperature is not None else settings.llm_temperature
        )
        self._client = httpx.Client(timeout=self.timeout, transport=transport)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAICompatibleClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        return self._client.post(self.endpoint, json=payload, headers=self._headers())

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: bool = True,
    ) -> LLMResponse:
        """调用模型并返回统一结构；失败抛出 :class:`LLMError`。"""
        if not self.base_url:
            raise LLMError("LLM_BASE_URL 未配置", kind="not_configured")
        if not self.api_key:
            raise LLMError("LLM_API_KEY 未配置", kind="not_configured")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = {"type": "json_object"}

        # 至少尝试 max_retries+1 次；response_format 不被支持时额外允许一次降级重试
        max_attempts = max(2, self.max_retries + 1)
        last_error: LLMError | None = None
        started = time.monotonic()

        for attempt in range(1, max_attempts + 1):
            try:
                response = self._post(payload)
            except httpx.TimeoutException as exc:
                last_error = LLMError(
                    f"大模型调用超时（{self.timeout}s）",
                    kind="timeout",
                    retryable=True,
                )
                logger.warning("llm timeout: %s", exc)
            except httpx.HTTPError as exc:
                last_error = LLMError(
                    f"大模型网络错误: {exc}",
                    kind="network_error",
                    retryable=True,
                )
                logger.warning("llm network error: %s", exc)
            else:
                status = response.status_code
                if status == 200:
                    return self._parse_response(
                        response, started=started, attempts=attempt
                    )
                if status == 400 and payload.get("response_format"):
                    # 端点不支持 response_format：去掉后重试一次（纯文本解析）
                    payload.pop("response_format", None)
                    last_error = LLMError(
                        "端点不支持 response_format，已降级为纯文本解析",
                        kind="bad_request",
                        status_code=status,
                        retryable=True,
                    )
                elif status == 429:
                    last_error = LLMError(
                        "大模型限流（429）", kind="rate_limited", status_code=status,
                        retryable=True,
                    )
                elif status == 401 or status == 403:
                    last_error = LLMError(
                        "大模型鉴权失败", kind="auth_error", status_code=status,
                        retryable=False,
                    )
                elif status >= 500:
                    last_error = LLMError(
                        f"大模型服务端错误（{status}）",
                        kind="server_error",
                        status_code=status,
                        retryable=True,
                    )
                else:
                    last_error = LLMError(
                        f"大模型请求失败（{status}）: {response.text[:200]}",
                        kind="bad_request",
                        status_code=status,
                        retryable=False,
                    )

            if not last_error.retryable or attempt >= max_attempts:
                break
            logger.info("llm retry %s/%s: %s", attempt, max_attempts - 1, last_error)

        assert last_error is not None
        raise last_error

    def _parse_response(
        self, response: httpx.Response, *, started: float, attempts: int
    ) -> LLMResponse:
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError(
                "大模型响应不是合法 JSON", kind="bad_response", retryable=False
            ) from exc
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"大模型响应结构非法: {str(data)[:200]}",
                kind="bad_response",
                retryable=False,
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("大模型返回空内容", kind="bad_response", retryable=False)
        return LLMResponse(
            content=content,
            model=str(data.get("model") or self.model),
            token_usage=_normalize_usage(data.get("usage")),
            latency_ms=int((time.monotonic() - started) * 1000),
            attempts=attempts,
        )


class MockLLMClient:
    """确定性 Mock 客户端：返回调用方注入的固定 JSON（评估/测试/无凭据演示）。"""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        latency_ms: int = 0,
        model: str = "mock-rule-fusion",
    ) -> None:
        self.payload = payload if payload is not None else dict(DEFAULT_MOCK_PAYLOAD)
        self.latency_ms = int(latency_ms)
        self.model = model
        self.calls = 0

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: bool = True,
    ) -> LLMResponse:
        self.calls += 1
        content = json.dumps(self.payload, ensure_ascii=False, sort_keys=True)
        return LLMResponse(
            content=content,
            model=self.model,
            token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            latency_ms=self.latency_ms,
            attempts=1,
        )


def build_llm_client(
    mode: str | None = None,
    *,
    mock_payload: dict[str, Any] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> OpenAICompatibleClient | MockLLMClient:
    """按模式构建客户端；未配置凭据时返回 Mock，保证演示链路可用。"""
    effective = mode or settings.effective_llm_mode
    if effective == "mock" or not settings.llm_configured:
        return MockLLMClient(mock_payload)
    return OpenAICompatibleClient(transport=transport)


def parse_json_object(content: str) -> dict[str, Any]:
    """从模型输出中解析 JSON 对象；容忍 Markdown 代码块与前后说明文字。"""
    text = (content or "").strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 包裹
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    if not text:
        raise LLMError("模型输出为空", kind="invalid_json")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("模型输出不是合法 JSON 对象", kind="invalid_json")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"模型输出不是合法 JSON 对象: {exc}", kind="invalid_json"
            ) from exc
    if not isinstance(data, dict):
        raise LLMError("模型输出 JSON 顶层必须是对象", kind="invalid_json")
    return data
