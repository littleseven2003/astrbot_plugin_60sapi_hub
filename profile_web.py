"""内容组合后台 API 的 AstrBot 宿主适配层。"""

from __future__ import annotations

from collections.abc import Callable

from astrbot.api.star import Context
from astrbot.api.web import error_response, json_response, request

from .core.profile_api import ProfileApi, ProfileApiResult


class ProfileWeb:
    """注册内容组合 CRUD 和预览接口。"""

    def __init__(
        self,
        api: ProfileApi,
        *,
        plugin_name: str,
        is_active: Callable[[], bool],
    ) -> None:
        self._api = api
        self._plugin_name = plugin_name
        self._is_active = is_active

    def register(self, context: Context) -> None:
        routes = (
            ("profiles", self.list_profiles, ["GET"], "列出内容组合"),
            ("profiles", self.create_profile, ["POST"], "新增内容组合"),
            ("profiles/<profile_id>", self.get_profile, ["GET"], "读取内容组合"),
            (
                "profiles/<profile_id>/update",
                self.update_profile,
                ["POST"],
                "编辑内容组合",
            ),
            (
                "profiles/<profile_id>/delete",
                self.delete_profile,
                ["POST"],
                "删除内容组合",
            ),
            (
                "profiles/<profile_id>/preview",
                self.preview_profile,
                ["POST"],
                "获取内容组合预览",
            ),
        )
        for path, handler, methods, desc in routes:
            context.register_web_api(
                f"/{self._plugin_name}/{path}",
                handler,
                methods,
                desc,
            )

    async def list_profiles(self):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.list_profiles())

    async def create_profile(self):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.create_profile(payload))

    async def get_profile(self, profile_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.get_profile(profile_id))

    async def update_profile(self, profile_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.update_profile(profile_id, payload))

    async def delete_profile(self, profile_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.delete_profile(profile_id, payload))

    async def preview_profile(self, profile_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        if isinstance(payload, dict):
            payload = {**payload, "profile_id": profile_id}
        return self._respond(await self._api.preview_profile(payload))

    def _guard(self):
        if not request.username:
            return error_response(
                "需要登录后才能管理内容组合。",
                status_code=401,
            )
        if not self._is_active():
            return error_response(
                "插件当前未启用。",
                status_code=503,
            )
        return None

    @staticmethod
    def _respond(result: ProfileApiResult):
        return json_response(result.body, status_code=result.status_code)
