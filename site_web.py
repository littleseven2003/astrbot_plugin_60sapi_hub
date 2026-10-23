"""AstrBot 插件后台站点 API 的宿主适配层。"""

from __future__ import annotations

from collections.abc import Callable

from astrbot.api.star import Context
from astrbot.api.web import error_response, json_response, request

from .core.site_api import SiteApi, SiteApiResult


class SiteWeb:
    """注册并处理站点管理 Web API，不自行管理登录或 token。"""

    def __init__(
        self,
        api: SiteApi,
        *,
        plugin_name: str,
        is_active: Callable[[], bool],
    ) -> None:
        self._api = api
        self._plugin_name = plugin_name
        self._is_active = is_active

    def register(self, context: Context) -> None:
        routes = (
            ("sites", self.list_sites, ["GET"], "列出站点"),
            ("sites", self.create_site, ["POST"], "新增站点"),
            ("sites/<site_id>", self.get_site, ["GET"], "读取站点"),
            (
                "sites/<site_id>/update",
                self.update_site,
                ["POST"],
                "编辑站点",
            ),
            (
                "sites/<site_id>/delete",
                self.delete_site,
                ["POST"],
                "删除站点",
            ),
        )
        for path, handler, methods, desc in routes:
            context.register_web_api(
                f"/{self._plugin_name}/{path}",
                handler,
                methods,
                desc,
            )

    async def list_sites(self):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.list_sites())

    async def create_site(self):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.create_site(payload))

    async def get_site(self, site_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.get_site(site_id))

    async def update_site(self, site_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.update_site(site_id, payload))

    async def delete_site(self, site_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.delete_site(site_id, payload))

    def _guard(self):
        if not request.username:
            return error_response(
                "需要登录后才能管理站点。",
                status_code=401,
            )
        if not self._is_active():
            return error_response(
                "插件当前未启用。",
                status_code=503,
            )
        return None

    @staticmethod
    def _respond(result: SiteApiResult):
        return json_response(result.body, status_code=result.status_code)
