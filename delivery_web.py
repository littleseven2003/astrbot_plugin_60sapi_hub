"""手动测试投递和执行记录查询的宿主 Web 适配层。"""

from __future__ import annotations

from collections.abc import Callable

from astrbot.api.star import Context
from astrbot.api.web import error_response, json_response, request

from .core.delivery_api import DeliveryApi, DeliveryApiResult


class DeliveryWeb:
    def __init__(self, api: DeliveryApi, *, plugin_name: str, is_active: Callable[[], bool]) -> None:
        self._api = api
        self._plugin_name = plugin_name
        self._is_active = is_active

    def register(self, context: Context) -> None:
        for path, handler, methods, desc in (
            ("tasks/<task_id>/test-preview", self.preview, ["POST"], "读取手动测试确认信息"),
            ("tasks/<task_id>/test", self.execute, ["POST"], "显式手动测试投递"),
            ("executions", self.list_executions, ["GET"], "查询投递执行列表"),
            ("executions/<execution_id>", self.get_execution, ["GET"], "查询投递执行详情"),
        ):
            context.register_web_api(
                f"/{self._plugin_name}/{path}", handler, methods, desc,
            )

    async def preview(self, task_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.preview(task_id))

    async def execute(self, task_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.execute(task_id, await request.json(default=None)))

    async def list_executions(self):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.list_executions(
            limit=request.query.get("limit", 20, type=int),
            offset=request.query.get("offset", 0, type=int),
            task_id=request.query.get("task_id"),
        ))

    async def get_execution(self, execution_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.get_execution(execution_id))

    def _guard(self):
        if not request.username:
            return error_response("需要登录后才能手动测试投递。", status_code=401)
        if not self._is_active():
            return error_response("插件当前未启用。", status_code=503)
        return None

    @staticmethod
    def _respond(result: DeliveryApiResult):
        return json_response(result.body, status_code=result.status_code)
