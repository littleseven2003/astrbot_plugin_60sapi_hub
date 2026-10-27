"""推送任务后台 API 的 AstrBot 宿主适配层。"""

from __future__ import annotations

from collections.abc import Callable

from astrbot.api.star import Context
from astrbot.api.web import error_response, json_response, request

from .core.task_api import TaskApi, TaskApiResult


class TaskWeb:
    """注册推送任务 CRUD 接口；不发送消息、不调度、不请求上游。"""

    def __init__(
        self,
        api: TaskApi,
        *,
        plugin_name: str,
        is_active: Callable[[], bool],
    ) -> None:
        self._api = api
        self._plugin_name = plugin_name
        self._is_active = is_active

    def register(self, context: Context) -> None:
        routes = (
            ("tasks", self.list_tasks, ["GET"], "列出推送任务"),
            ("tasks", self.create_task, ["POST"], "新增推送任务"),
            ("tasks/<task_id>", self.get_task, ["GET"], "读取推送任务"),
            (
                "tasks/<task_id>/update",
                self.update_task,
                ["POST"],
                "编辑推送任务",
            ),
            (
                "tasks/<task_id>/delete",
                self.delete_task,
                ["POST"],
                "删除推送任务",
            ),
        )
        for path, handler, methods, desc in routes:
            context.register_web_api(
                f"/{self._plugin_name}/{path}",
                handler,
                methods,
                desc,
            )

    async def list_tasks(self):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.list_tasks())

    async def create_task(self):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.create_task(payload))

    async def get_task(self, task_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        return self._respond(await self._api.get_task(task_id))

    async def update_task(self, task_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.update_task(task_id, payload))

    async def delete_task(self, task_id: str):
        guard = self._guard()
        if guard is not None:
            return guard
        payload = await request.json(default=None)
        return self._respond(await self._api.delete_task(task_id, payload))

    def _guard(self):
        if not request.username:
            return error_response(
                "需要登录后才能管理推送任务。",
                status_code=401,
            )
        if not self._is_active():
            return error_response("插件当前未启用。", status_code=503)
        return None

    @staticmethod
    def _respond(result: TaskApiResult):
        return json_response(result.body, status_code=result.status_code)
