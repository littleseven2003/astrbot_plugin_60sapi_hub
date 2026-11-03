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
        lifecycle: Callable[[], int] | None = None,
    ) -> None:
        self._api = api
        self._plugin_name = plugin_name
        self._is_active = is_active
        self._lifecycle = lifecycle or (lambda: 0)

    def register(self, context: Context) -> None:
        context.register_web_api(
            f"/{self._plugin_name}/content/preview",
            self.preview,
            ["POST"],
            "获取每日新闻或 Bilibili 热搜预览",
        )
        context.register_web_api(
            f"/{self._plugin_name}/sites/<site_id>/test",
            self.test_connection,
            ["POST"],
            "测试已保存站点连接",
        )

    async def test_connection(self, site_id: str):
        epoch = self._lifecycle()
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        if epoch != self._lifecycle() or not self._is_active():
            return error_response(
                "插件当前不可用，请重新打开页面后重试。",
                status_code=503,
            )
        result = await self._api.test_connection(site_id, payload)
        if result.status_code >= 400:
            code = result.body.get("data", {}).get("code", "test_failed")
            result = ContentApiResult(
                result.status_code,
                {"status": "error",
                 "message": f"{result.body['message']}（错误码 {code}）",
                 "data": result.body.get("data", {})},
            )
        return self._respond(result)

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
