from __future__ import annotations

import asyncio
import unittest

import httpx

from core.news import (
    NewsClientClosedError,
    NewsHTTPStatusError,
    NewsRedirectError,
    NewsTimeoutError,
    NewsTooLargeError,
    NewsTransportError,
)
from core.news_client import NewsHttpClient

URL = "http://example.test/prefix/v2/60s"


class NewsHttpClientTest(unittest.IsolatedAsyncioTestCase):
    async def _client(self, handler, **kwargs):
        client = NewsHttpClient(
            transport=httpx.MockTransport(handler),
            retry_delay_seconds=0,
            **kwargs,
        )
        await client.open()
        self.addAsyncCleanup(client.close)
        return client

    async def test_success_sends_headers_and_returns_body(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                content=b'{"code":200,"data":{"news":[]}}',
                headers={"content-type": "application/json"},
            )

        client = await self._client(handler)
        body = await client.fetch_json(
            URL,
            bearer_token="secret-token",
            timeout_seconds=2,
        )
        self.assertEqual(body, b'{"code":200,"data":{"news":[]}}')
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0].headers["authorization"],
            "Bearer secret-token",
        )
        self.assertEqual(requests[0].headers["accept"], "application/json")

    async def test_redirect_is_not_followed_and_does_not_forward_token(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                302,
                headers={"location": "https://other.test/steal"},
            )

        client = await self._client(handler)
        with self.assertRaises(NewsRedirectError):
            await client.fetch_json(
                URL,
                bearer_token="secret-token",
                timeout_seconds=2,
            )
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0].headers["authorization"],
            "Bearer secret-token",
        )

    async def test_retryable_status_then_success(self) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    503,
                    content=b'{"message":"temporary"}',
                )
            return httpx.Response(200, content=b"ok")

        client = await self._client(handler)
        body = await client.fetch_json(
            URL,
            bearer_token=None,
            timeout_seconds=2,
        )
        self.assertEqual(body, b"ok")
        self.assertEqual(attempts, 2)

    async def test_non_retryable_status_is_not_retried(self) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(400, content=b'{"message":"bad request"}')

        client = await self._client(handler)
        with self.assertRaises(NewsHTTPStatusError) as context:
            await client.fetch_json(
                URL,
                bearer_token=None,
                timeout_seconds=2,
            )
        self.assertEqual(context.exception.status_code, 400)
        self.assertFalse(context.exception.retryable)
        self.assertEqual(attempts, 1)

    async def test_retry_budget_is_bounded(self) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(429, content=b'{"message":"slow down"}')

        client = await self._client(handler, max_attempts=2)
        with self.assertRaises(NewsHTTPStatusError) as context:
            await client.fetch_json(
                URL,
                bearer_token=None,
                timeout_seconds=2,
            )
        self.assertEqual(context.exception.status_code, 429)
        self.assertEqual(attempts, 2)

    async def test_content_length_limit(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"0123456789" * 3)

        client = await self._client(handler, max_response_bytes=10)
        with self.assertRaises(NewsTooLargeError):
            await client.fetch_json(
                URL,
                bearer_token=None,
                timeout_seconds=2,
            )

    async def test_streaming_size_limit_without_content_length(self) -> None:
        class ChunkStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"123456"
                yield b"78901"

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=ChunkStream())

        client = await self._client(handler, max_response_bytes=10)
        with self.assertRaises(NewsTooLargeError):
            await client.fetch_json(
                URL,
                bearer_token=None,
                timeout_seconds=2,
            )

    async def test_transport_error_is_retried_then_classified(self) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("connection failed", request=request)

        client = await self._client(handler, max_attempts=2)
        with self.assertRaises(NewsTransportError):
            await client.fetch_json(
                URL,
                bearer_token=None,
                timeout_seconds=2,
            )
        self.assertEqual(attempts, 2)

    async def test_timeout_is_classified(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.2)
            return httpx.Response(200, content=b"late")

        client = await self._client(handler)
        with self.assertRaises(NewsTimeoutError):
            await client.fetch_json(
                URL,
                bearer_token=None,
                timeout_seconds=0.05,
            )

    async def test_cancellation_propagates(self) -> None:
        started = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.sleep(5)
            return httpx.Response(200, content=b"late")

        client = await self._client(handler)
        task = asyncio.create_task(
            client.fetch_json(
                URL,
                bearer_token=None,
                timeout_seconds=5,
            ),
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_closed_client_rejects_fetch(self) -> None:
        client = NewsHttpClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"ok"),
            ),
        )
        await client.open()
        await client.close()
        with self.assertRaises(NewsClientClosedError):
            await client.fetch_json(
                URL,
                bearer_token=None,
                timeout_seconds=1,
            )

    async def test_concurrency_is_bounded(self) -> None:
        active = 0
        peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return httpx.Response(200, content=b"ok")

        client = await self._client(
            handler,
            max_concurrent_requests=1,
        )
        await asyncio.gather(
            *(
                client.fetch_json(
                    URL,
                    bearer_token=None,
                    timeout_seconds=2,
                )
                for _ in range(3)
            ),
        )
        self.assertEqual(peak, 1)


if __name__ == "__main__":
    unittest.main()


class NewsHttpClientLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def _wait_until(self, predicate, label: str) -> None:
        for _ in range(1000):
            if predicate():
                return
            await asyncio.sleep(0)
        raise AssertionError(f"condition not reached: {label}")

    async def _client(self, handler, **kwargs):
        client = NewsHttpClient(
            transport=httpx.MockTransport(handler),
            retry_delay_seconds=0,
            **kwargs,
        )
        await client.open()
        self.addAsyncCleanup(client.close)
        return client

    async def test_close_cancels_inflight_and_waits_without_touching_unrelated_tasks(
        self,
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        unrelated = asyncio.create_task(asyncio.Event().wait())

        async def handler(request: httpx.Request) -> httpx.Response:
            started.set()
            await release.wait()
            return httpx.Response(200, content=b"late")

        client = await self._client(handler)
        request = asyncio.create_task(
            client.fetch_json(
                URL,
                bearer_token=None,
                timeout_seconds=30,
            ),
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        await client.close()
        with self.assertRaises(NewsClientClosedError):
            await request
        self.assertFalse(unrelated.done())
        self.assertEqual(client.active_request_count, 0)
        self.assertFalse(client.is_open)
        unrelated.cancel()
        await asyncio.gather(unrelated, return_exceptions=True)

    async def test_close_cancels_queued_request_and_classifies_it(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            started.set()
            await release.wait()
            return httpx.Response(200, content=b"late")

        client = await self._client(
            handler,
            max_concurrent_requests=1,
        )
        first = asyncio.create_task(
            client.fetch_json(URL, bearer_token=None, timeout_seconds=30),
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        second = asyncio.create_task(
            client.fetch_json(URL, bearer_token=None, timeout_seconds=30),
        )
        await self._wait_until(
            lambda: client.active_request_count == 2,
            "two owned requests",
        )
        await client.close()
        results = await asyncio.gather(
            first,
            second,
            return_exceptions=True,
        )
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertIsInstance(result, NewsClientClosedError)
        self.assertEqual(requests, 1)

    async def test_close_cancels_retry_wait(self) -> None:
        attempts = 0
        retry_wait_started = asyncio.Event()

        class RetryWaitClient(NewsHttpClient):
            async def _sleep(self, seconds: float) -> None:
                retry_wait_started.set()
                await asyncio.Event().wait()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503, content=b"temporary")

        client = RetryWaitClient(
            transport=httpx.MockTransport(handler),
            retry_delay_seconds=1,
        )
        await client.open()
        self.addAsyncCleanup(client.close)
        request = asyncio.create_task(
            client.fetch_json(URL, bearer_token=None, timeout_seconds=30),
        )
        await asyncio.wait_for(retry_wait_started.wait(), timeout=1)
        await client.close()
        with self.assertRaises(NewsClientClosedError):
            await request
        self.assertEqual(attempts, 1)

    async def test_close_is_idempotent_and_reopen_isolated(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=b"ok")

        client = await self._client(handler)
        await client.close()
        await client.close()
        self.assertFalse(client.is_open)
        await client.open()
        self.assertTrue(client.is_open)
        body = await client.fetch_json(
            URL,
            bearer_token=None,
            timeout_seconds=2,
        )
        self.assertEqual(body, b"ok")
        self.assertEqual(calls, 1)
        self.assertEqual(client.active_request_count, 0)
