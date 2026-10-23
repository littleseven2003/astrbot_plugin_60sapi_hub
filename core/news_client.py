"""每日新闻的异步 HTTP 客户端，集中管理超时、大小和有限重试。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from .news import (
    NewsClientClosedError,
    NewsHTTPStatusError,
    NewsRedirectError,
    NewsTimeoutError,
    NewsTooLargeError,
    NewsTransportError,
)

DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_DELAY_SECONDS = 0.25
DEFAULT_MAX_CONCURRENT_REQUESTS = 2
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
MAX_ERROR_BODY_BYTES = 8 * 1024
RETRYABLE_STATUS_CODES = frozenset({408, 429, 502, 503, 504})
USER_AGENT = "astrbot-plugin-60sapi-hub/0.1"


class NewsHttpClient:
    """共享 ``httpx.AsyncClient`` 的每日新闻请求器。

    重定向一律不跟随，避免把原站点 Authorization 发往其他来源。响应体先按
    Content-Length 预检，再在流式读取中累计限制大小。所有重试都受同一个整体
    超时约束，不会无限延长操作。
    """

    def __init__(
        self,
        *,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
        max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be at least 1")
        self._max_response_bytes = max_response_bytes
        self._max_attempts = max_attempts
        self._retry_delay_seconds = max(0.0, retry_delay_seconds)
        self._max_concurrent_requests = max_concurrent_requests
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def is_open(self) -> bool:
        return self._client is not None

    @property
    def max_response_bytes(self) -> int:
        return self._max_response_bytes

    async def open(self) -> None:
        if self._client is not None:
            return
        self._semaphore = asyncio.Semaphore(
            self._max_concurrent_requests,
        )
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
            limits=httpx.Limits(
                max_connections=self._max_concurrent_requests,
                max_keepalive_connections=self._max_concurrent_requests,
            ),
        )

    async def close(self) -> None:
        client, self._client = self._client, None
        self._semaphore = None
        if client is not None:
            await client.aclose()

    async def fetch_json(
        self,
        url: str,
        *,
        bearer_token: str | None,
        timeout_seconds: float,
    ) -> bytes:
        """读取 JSON 响应体；调用方负责后续解析。"""
        client = self._client
        semaphore = self._semaphore
        if client is None or semaphore is None:
            raise NewsClientClosedError("上游客户端当前不可用。")
        if timeout_seconds <= 0:
            raise NewsTimeoutError("获取每日新闻超时。")

        try:
            async with asyncio.timeout(timeout_seconds):
                async with semaphore:
                    return await self._fetch_with_retries(
                        client,
                        url,
                        bearer_token=bearer_token,
                        timeout_seconds=timeout_seconds,
                    )
        except TimeoutError as exc:
            raise NewsTimeoutError("获取每日新闻超时。") from exc

    async def _fetch_with_retries(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        bearer_token: str | None,
        timeout_seconds: float,
    ) -> bytes:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._fetch_once(
                    client,
                    url,
                    bearer_token=bearer_token,
                    timeout_seconds=timeout_seconds,
                )
            except NewsHTTPStatusError as exc:
                if not exc.retryable or attempt >= self._max_attempts:
                    raise
            except httpx.TimeoutException as exc:
                if attempt >= self._max_attempts:
                    raise NewsTimeoutError("获取每日新闻超时。") from exc
            except httpx.TransportError as exc:
                if attempt >= self._max_attempts:
                    raise NewsTransportError("无法连接上游站点。") from exc

            if self._retry_delay_seconds:
                await asyncio.sleep(self._retry_delay_seconds)

        raise NewsTransportError("无法连接上游站点。")

    async def _fetch_once(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        bearer_token: str | None,
        timeout_seconds: float,
    ) -> bytes:
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

        request_timeout = httpx.Timeout(
            timeout_seconds,
            connect=min(DEFAULT_CONNECT_TIMEOUT_SECONDS, timeout_seconds),
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )
        async with client.stream(
            "GET",
            url,
            headers=headers,
            timeout=request_timeout,
        ) as response:
            status = response.status_code
            if 300 <= status < 400:
                raise NewsRedirectError(status)
            if not 200 <= status < 300:
                body = await self._read_bounded(
                    response,
                    MAX_ERROR_BODY_BYTES,
                    too_large_error=False,
                )
                raise NewsHTTPStatusError(
                    status,
                    _extract_error_detail(body),
                    retryable=status in RETRYABLE_STATUS_CODES,
                )

            content_length = _content_length(response)
            if (
                content_length is not None
                and content_length > self._max_response_bytes
            ):
                raise NewsTooLargeError(self._max_response_bytes)
            return await self._read_bounded(
                response,
                self._max_response_bytes,
                too_large_error=True,
            )

    @staticmethod
    async def _read_bounded(
        response: httpx.Response,
        limit: int,
        *,
        too_large_error: bool,
    ) -> bytes:
        data = bytearray()
        async for chunk in response.aiter_bytes():
            remaining = limit - len(data)
            if len(chunk) > remaining:
                if too_large_error:
                    raise NewsTooLargeError(limit)
                data.extend(chunk[:remaining])
                break
            data.extend(chunk)
        return bytes(data)


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


def _extract_error_detail(body: bytes) -> str | None:
    if not body:
        return None
    try:
        parsed: Any = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        for key in ("message", "error", "detail"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
        return None
    text = body.decode("utf-8", errors="replace").strip()
    if not text or text.startswith("<"):
        return None
    return text[:300]


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_CONCURRENT_REQUESTS",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_RETRY_DELAY_SECONDS",
    "NewsHttpClient",
    "RETRYABLE_STATUS_CODES",
]
