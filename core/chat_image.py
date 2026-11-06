"""聊天图片查询：复用文字授权、组合快照与按栏目生成的长图渲染链路。"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass

from .chat_content import (
    AUTH_TEXT,
    CHANGED_TEXT,
    CONFIG_TEXT,
    USAGE_TEXT,
    _Unavailable,
    _Unauthorized,
    authorize_task,
    config_changed,
)
from .profile_api import ProfileApi
from .tasks import TaskValidationError, normalize_task_id

IMAGE_FAILED_TEXT = (
    "图片查询暂时不可用，请使用 /60s get <任务ID> 查看文字版。"
)
IMAGE_CHANGED_TEXT = CHANGED_TEXT
# 一个栏目一张长图；栏目数受现有五种内容接口限制。
MAX_SECTION_IMAGES = 5
MAX_SECTION_IMAGE_BYTES = 2 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ALLOWED_IMAGE_STATUSES = frozenset({"success", "empty", "failed"})


@dataclass(frozen=True, slots=True)
class ChatImageReply:
    """一次聊天图片回复：按栏目顺序排列的长图与可选失败说明。"""

    images: tuple[bytes, ...]
    text: str

    @property
    def image_count(self) -> int:
        return len(self.images)


class ChatImageQuery:
    def __init__(self, tasks, previews: ProfileApi) -> None:
        self._tasks = tasks
        self._previews = previews

    async def query(self, umo: str, task_id: object) -> str | ChatImageReply:
        try:
            normalized_id = normalize_task_id(task_id)
        except TaskValidationError:
            return USAGE_TEXT
        try:
            authorized = await authorize_task(self._tasks, umo, normalized_id)
        except _Unauthorized:
            return AUTH_TEXT
        except _Unavailable:
            return CONFIG_TEXT
        owner = _owner()
        try:
            preview = await self._previews.preview_profile(
                {
                    "profile_id": authorized.profile_id,
                    "force_refresh": False,
                },
                owner=owner,
            )
        except Exception:
            return IMAGE_FAILED_TEXT
        if preview.status_code != 200:
            return IMAGE_FAILED_TEXT
        data = preview.body.get("data") if isinstance(preview.body, dict) else None
        snapshot = data.get("image_snapshot") if isinstance(data, dict) else None
        snapshot_id = snapshot.get("id") if isinstance(snapshot, dict) else None
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return IMAGE_FAILED_TEXT
        try:
            rendered = await self._previews.generate_image(
                authorized.profile_id,
                {"snapshot_id": snapshot_id},
                owner=owner,
            )
        except Exception:
            return IMAGE_FAILED_TEXT
        if rendered.status_code != 200:
            return IMAGE_FAILED_TEXT
        rendered_data = (
            rendered.body.get("data") if isinstance(rendered.body, dict) else None
        )
        decoded = _decode_section_images(rendered_data)
        if decoded is None:
            return IMAGE_FAILED_TEXT
        images, failed_labels = decoded
        if not images:
            # 全部栏目都没生成出图：不伪造图片，也不拆分重试。
            return IMAGE_FAILED_TEXT
        if await config_changed(self._tasks, umo, authorized):
            return IMAGE_CHANGED_TEXT
        return ChatImageReply(
            images=images,
            text=_reply_text(len(images), failed_labels, normalized_id),
        )


def _reply_text(
    count: int, failed_labels: tuple[str, ...], task_id: str,
) -> str:
    lines = [f"共 {count} 张栏目长图。"]
    if failed_labels:
        lines.append("以下栏目未生成图片：" + "、".join(failed_labels) + "。")
    lines.append(f"可发送 /60s get {task_id} 获取文字版。")
    return "\n".join(lines)


def _owner() -> str:
    # Fresh internal identity per query; never user-supplied, never an empty
    # Dashboard owner. Snapshot IDs remain unguessable and are not exposed.
    return f"chat:{secrets.token_urlsafe(24)}"


def _decode_section_images(
    data: object,
) -> tuple[tuple[bytes, ...], tuple[str, ...]] | None:
    """按栏目顺序取出可发送的 PNG；失败栏目只收集标签，不伪造图片。"""
    if not isinstance(data, dict):
        return None
    items = data.get("images")
    if not isinstance(items, list) or not items:
        return None
    if len(items) > MAX_SECTION_IMAGES:
        return None
    parsed: list[tuple[int, bytes | None, str | None]] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        index = item.get("index")
        status = item.get("status")
        label = item.get("label")
        if type(index) is not int or index < 1:
            return None
        if not isinstance(status, str) or status not in _ALLOWED_IMAGE_STATUSES:
            return None
        if not isinstance(label, str) or not label.strip():
            return None
        if status == "failed":
            parsed.append((index, None, label.strip()))
            continue
        encoded = item.get("png_base64")
        if not isinstance(encoded, str) or not encoded:
            return None
        try:
            png = base64.b64decode(encoded, validate=True)
        except Exception:
            return None
        if not png.startswith(_PNG_SIGNATURE):
            return None
        if len(png) > MAX_SECTION_IMAGE_BYTES:
            return None
        parsed.append((index, png, None))
    parsed.sort(key=lambda entry: entry[0])
    if [entry[0] for entry in parsed] != list(range(1, len(parsed) + 1)):
        return None
    images = tuple(png for _, png, _ in parsed if png is not None)
    failed_labels = tuple(label for _, png, label in parsed if png is None and label)
    return images, failed_labels


__all__ = ["ChatImageQuery", "ChatImageReply", "IMAGE_FAILED_TEXT"]
