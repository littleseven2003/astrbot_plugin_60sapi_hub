"""聊天图片查询：复用文字授权、组合快照和现有图片渲染链路。"""

from __future__ import annotations

import base64
import hashlib
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
_MAX_IMAGE_PAGES = 6
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True, slots=True)
class ChatImageReply:
    pages: tuple[bytes, ...]
    text: str

    @property
    def page_count(self) -> int:
        return len(self.pages)


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
        pages = _decode_pages(rendered_data)
        if pages is None:
            return IMAGE_FAILED_TEXT
        if await config_changed(self._tasks, umo, authorized):
            return IMAGE_CHANGED_TEXT
        return ChatImageReply(
            pages=pages,
            text=(
                f"共 {len(pages)} 页图片。\n"
                f"可发送 /60s get {normalized_id} 获取文字版。"
            ),
        )


def _owner() -> str:
    # Fresh internal identity per query; never user-supplied, never an empty
    # Dashboard owner. Snapshot IDs remain unguessable and are not exposed.
    return f"chat:{secrets.token_urlsafe(24)}"


def _decode_pages(data: object) -> tuple[bytes, ...] | None:
    if not isinstance(data, dict):
        return None
    page_items = data.get("pages")
    if not isinstance(page_items, list) or not page_items:
        return None
    if len(page_items) > _MAX_IMAGE_PAGES:
        return None
    decoded: list[tuple[int, bytes]] = []
    for item in page_items:
        if not isinstance(item, dict):
            return None
        index = item.get("index")
        encoded = item.get("png_base64")
        if type(index) is not int or not isinstance(encoded, str):
            return None
        try:
            png = base64.b64decode(encoded, validate=True)
        except Exception:
            return None
        expected = item.get("sha256")
        if isinstance(expected, str) and hashlib.sha256(png).hexdigest() != expected:
            return None
        if not png.startswith(_PNG_SIGNATURE):
            return None
        decoded.append((index, png))
    decoded.sort(key=lambda item: item[0])
    if [item[0] for item in decoded] != list(range(1, len(decoded) + 1)):
        return None
    return tuple(item[1] for item in decoded)


__all__ = ["ChatImageQuery", "ChatImageReply", "IMAGE_FAILED_TEXT"]
