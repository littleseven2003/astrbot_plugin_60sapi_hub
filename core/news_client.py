"""每日新闻的异步 HTTP 客户端，集中管理超时、大小和有限重试。"""

from __future__ import annotations

import asyncio
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
        self._state_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[bytes]] = set()
        self._closed = True
        self._generation = 0
        self._closed_generation: int | None = None
        self._close_task: asyncio.Task[None] | None = None

    @property
    def is_open(self) -> bool:
        return self._client is not None and not self._closed

    @property
    def max_response_bytes(self) -> int:
        return self._max_response_bytes

    @property
    def active_request_count(self) -> int:
        """当前由本客户端创建、尚未结束的请求任务数。"""
        return len(self._tasks)

    async def open(self) -> None:
        while True:
            async with self._state_lock:
                if self._client is not None and not self._closed:
                    return
                close_task = self._close_task
            if close_task is not None and not close_task.done():
                await asyncio.shield(close_task)
                continue
            async with self._state_lock:
                if self._client is not None and not self._closed:
                    return
                if self._close_task is not None and not self._close_task.done():
                    continue
                self._generation += 1
                self._closed_generation = None
                self._closed = False
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
                return

    async def close(self) -> None:
        async with self._state_lock:
            close_task = self._close_task
            if close_task is None:
                if self._client is None and not self._tasks:
                    self._closed = True
                    return
                self._closed = True
                self._closed_generation = self._generation
                client = self._client
                self._client = None
                self._semaphore = None
                tasks = tuple(self._tasks)
                close_task = asyncio.create_task(
                    self._finish_close(client, tasks),
                )
                self._close_task = close_task
        await asyncio.shield(close_task)

    async def _finish_close(
        self,
        client: httpx.AsyncClient | None,
        tasks: tuple[asyncio.Task[bytes], ...],
    ) -> None:
        current = asyncio.current_task()
        try:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if client is not None:
                await client.aclose()
        finally:
            async with self._state_lock:
                if self._close_task is current:
                    self._close_task = None

    async def fetch_json(
        self,
        url: str,
        *,
        bearer_token: str | None,
        timeout_seconds: float,
    ) -> bytes:
        """读取 JSON 响应体；调用方负责后续解析。"""
        async with self._state_lock:
            client = self._client
            semaphore = self._semaphore
            if self._closed or client is None or semaphore is None:
                raise NewsClientClosedError("上游客户端当前不可用。")
            if timeout_seconds <= 0:
                raise NewsTimeoutError("获取每日新闻超时。")
            generation = self._generation
            task = asyncio.create_task(
                self._run_request(
                    client,
                    semaphore,
                    generation,
                    url,
                    bearer_token=bearer_token,
                    timeout_seconds=timeout_seconds,
                ),
            )
            self._tasks.add(task)

        try:
            return await task
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            self._tasks.discard(task)

    async def _run_request(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        generation: int,
        url: str,
        *,
        bearer_token: str | None,
        timeout_seconds: float,
    ) -> bytes:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with semaphore:
                    return await self._fetch_with_retries(
                        client,
                        url,
                        bearer_token=bearer_token,
                        timeout_seconds=timeout_seconds,
                    )
        except asyncio.CancelledError:
            if self._closed_generation == generation:
                raise NewsClientClosedError("上游客户端当前不可用。") from None
            raise
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
                await self._sleep(self._retry_delay_seconds)

        raise NewsTransportError("无法连接上游站点。")

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

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
                # 只丢弃有限错误正文，不解析、不记录、不返回上游文本。
                await self._read_bounded(
                    response,
                    MAX_ERROR_BODY_BYTES,
                    too_large_error=False,
                )
                raise NewsHTTPStatusError(
                    status,
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


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_CONCURRENT_REQUESTS",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_RETRY_DELAY_SECONDS",
    "NewsHttpClient",
    "RETRYABLE_STATUS_CODES",
]
