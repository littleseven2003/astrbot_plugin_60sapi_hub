from __future__ import annotations

import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from core.image_service import ImageTimeoutError, ProfileImageService
from core.profile_image import ImageRenderCancelled


class SlowRenderer:
    started = threading.Event()

    def __init__(self, **_kwargs) -> None:
        pass

    def render(self, _data, *, created_at="", cancel_event=None):
        type(self).started.set()
        time.sleep(0.15)
        return []


class BlockingRenderer:
    started = threading.Event()

    def __init__(self, **_kwargs) -> None:
        pass

    def render(self, _data, *, created_at="", cancel_event=None):
        type(self).started.set()
        if cancel_event is not None:
            cancel_event.wait(2)
        raise ImageRenderCancelled("cancelled")


class ImageServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_and_close(self) -> None:
        SlowRenderer.started.clear()
        service = ProfileImageService(timeout_seconds=0.01)
        with patch("core.image_service.ProfileImageRenderer", SlowRenderer):
            with self.assertRaises(ImageTimeoutError):
                await service.render({})
        await service.close()
        self.assertFalse(service.is_open)

    async def test_close_releases_blocked_render(self) -> None:
        BlockingRenderer.started.clear()
        service = ProfileImageService(timeout_seconds=5)
        with patch("core.image_service.ProfileImageRenderer", BlockingRenderer):
            task = asyncio.create_task(service.render({}))
            await asyncio.to_thread(BlockingRenderer.started.wait, 2)
            await asyncio.wait_for(service.close(), timeout=2)
            with self.assertRaises(Exception):
                await task
        self.assertFalse(service.is_open)
