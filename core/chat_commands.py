"""聊天发现命令：帮助和当前会话可见的已保存任务列表。

只读取任务、组合和站点元信息；不获取上游内容、不发送消息、不写业务配置。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .profiles import ContentProfileService
from .sites import SiteService
from .tasks import OUTPUT_FORMAT_LABELS, PushTaskService

PAGE_SIZE = 10
MAX_PAGE_DIGITS = 6
HELP_TEXT = (
    "60sAPI Hub 命令：\n"
    "/60s 或 /60s help：显示本帮助\n"
    "/60s list [页码]：列出当前会话可见的已保存任务（每页最多 10 项）"
)
EMPTY_TEXT = "当前会话没有可见任务。"
PAGE_NOTICE = "页码必须是正整数。"
PAGE_OUT_OF_RANGE = "页码超出范围。"
LIST_UNAVAILABLE = "暂时无法读取任务列表。"


@dataclass(frozen=True, slots=True)
class VisibleTask:
    id: str
    name: str
    output_label: str
    created_at: str


class ChatCommandService:
    """只读命令处理；不依赖投递、内容获取或调度。"""

    def __init__(
        self,
        tasks: PushTaskService,
        profiles: ContentProfileService,
        sites: SiteService,
    ) -> None:
        self._tasks = tasks
        self._profiles = profiles
        self._sites = sites

    async def handle(self, umo: str, message: str) -> str:
        parsed = parse_command(message)
        if parsed is None:
            return ""
        subcommand, args = parsed
        if subcommand == "help":
            return HELP_TEXT
        if subcommand == "list":
            return await self._list(umo, args)
        return f"未知命令。\n\n{HELP_TEXT}"

    async def _list(self, umo: str, args: list[str]) -> str:
        if len(args) > 1:
            return f"命令参数无效。\n\n{HELP_TEXT}"
        page = 1
        if args:
            raw = args[0]
            if not re.fullmatch(r"[0-9]{1,6}", raw):
                return PAGE_NOTICE
            page = int(raw)
            if page < 1:
                return PAGE_NOTICE
        try:
            visible = await self._visible_tasks(umo)
        except Exception:
            return LIST_UNAVAILABLE
        if not visible:
            return EMPTY_TEXT
        total_pages = (len(visible) + PAGE_SIZE - 1) // PAGE_SIZE
        if page > total_pages:
            return (
                f"{PAGE_OUT_OF_RANGE} 当前会话共有 {len(visible)} 项任务，"
                f"共 {total_pages} 页。"
            )
        start = (page - 1) * PAGE_SIZE
        chunk = visible[start:start + PAGE_SIZE]
        lines = [
            f"当前会话可见任务（第 {page}/{total_pages} 页，共 {len(visible)} 项）：",
        ]
        for index, task in enumerate(chunk, start=start + 1):
            lines.append(
                f"{index}. {task.name}\n"
                f"   ID：{task.id}\n"
                f"   输出：{task.output_label}",
            )
        if total_pages > 1:
            lines.append("使用 /60s list 页码 查看更多。")
        return "\n".join(lines)

    async def _visible_tasks(self, umo: str) -> list[VisibleTask]:
        tasks = await self._tasks.list_tasks()
        profiles = await self._profiles.list_profiles()
        sites = await self._sites.list_sites()
        profile_by_id = {profile.id: profile for profile in profiles}
        site_by_id = {site.id: site for site in sites}
        visible: list[VisibleTask] = []
        for task in tasks:
            # Exact UMO membership only. No prefix, bare ID, platform or
            # member-isolation fallback; the current UMO is never widened.
            if umo not in task.umos:
                continue
            profile = profile_by_id.get(task.profile_id)
            if profile is None:
                continue
            site = site_by_id.get(profile.site_id)
            if site is None or not site.enabled:
                continue
            visible.append(VisibleTask(
                id=task.id,
                name=task.name,
                output_label=OUTPUT_FORMAT_LABELS[task.output_format],
                created_at=task.created_at,
            ))
        visible.sort(key=lambda item: (item.created_at, item.id))
        return visible


def parse_command(message: str) -> tuple[str, list[str]] | None:
    """解析 /60s 或 60s 后的子命令；非本命令返回 None。"""
    parts = message.strip().split()
    if not parts:
        return None
    first = parts[0]
    if first.startswith("/"):
        first = first[1:]
    if first != "60s":
        return None
    rest = parts[1:]
    if not rest:
        return ("help", [])
    subcommand = rest[0]
    if subcommand == "help":
        return ("help", rest[1:])
    if subcommand == "list":
        return ("list", rest[1:])
    return ("unknown", rest[1:])
