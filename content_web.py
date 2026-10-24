"""每日新闻 / Bilibili 热搜统一预览的 AstrBot 宿主适配层。"""

from __future__ import annotations

from collections.abc import Callable

from astrbot.api.star import Context
from astrbot.api.web import error_response, json_response, request

from .core.content_api import ContentApi, ContentApiResult


class ContentWeb:
    """注册统一内容预览接口；只接收站点 ID 和固定内容类型。"""

    def __init__(
        self,
        api: ContentApi,
        *,
        plugin_name: str,
        is_active: Callable[[], bool],
    ) -> None:
        self._api = api
        self._plugin_name = plugin_name
        self._is_active = is_active

    def register(self, context: Context) -> None:
        context.register_web_api(
            f"/{self._plugin_name}/content/preview",
            self.preview,
            ["POST"],
            "获取每日新闻或 Bilibili 热搜预览",
        )

    async def preview(self):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.preview(payload))

    def _guard(self):
        if not request.username:
            return error_response(
                "需要登录后才能获取内容预览。",
                status_code=401,
            )
        if not self._is_active():
            return error_response(
                "插件当前未启用。",
                status_code=503,
            )
        return None

    @staticmethod
    def _respond(result: ContentApiResult):
        return json_response(result.body, status_code=result.status_code)
