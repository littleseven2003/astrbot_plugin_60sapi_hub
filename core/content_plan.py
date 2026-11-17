"""任务内容方案（栏目、顺序与每栏条数）的领域模型与校验。

内容是任务自己的一部分：每个任务保存一份独立的栏目配置，编辑一个任务
不会改变其他任务。本模块只做校验与转换，不访问存储、网络或渲染。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .content_types import CONTENT_LABELS, KNOWN_CONTENT_TYPES
from .site_store import StoredProfileItem

MAX_PLAN_ITEMS = 5
MAX_ITEM_LIMIT = 1000


class PlanValidationError(Exception):
    """任务内容方案输入不合法。"""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field} 无效：{reason}")


@dataclass(frozen=True, slots=True)
class PlanItem:
    """任务中的一个固定内容栏目；顺序即输出顺序。"""

    content_type: str
    limit: int | None = None

    @property
    def label(self) -> str:
        return CONTENT_LABELS[self.content_type]


def normalize_plan_items(value: object) -> tuple[PlanItem, ...]:
    """校验任务提交的栏目数组：1～5 个、类型不重复、条数为空或 1～1000。"""
    if not isinstance(value, list):
        raise PlanValidationError("items", "必须是数组")
    if not 1 <= len(value) <= MAX_PLAN_ITEMS:
        raise PlanValidationError(
            "items",
            f"必须包含 1～{MAX_PLAN_ITEMS} 个栏目",
        )
    items: list[PlanItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        field = f"items[{index}]"
        if not isinstance(raw, dict):
            raise PlanValidationError(field, "必须是对象")
        content_type = raw.get("content_type")
        if (
            not isinstance(content_type, str)
            or content_type not in KNOWN_CONTENT_TYPES
        ):
            raise PlanValidationError(
                f"{field}.content_type",
                "不是支持的内容类型",
            )
        if content_type in seen:
            raise PlanValidationError(
                f"{field}.content_type",
                "同一任务不能重复选择内容类型",
            )
        seen.add(content_type)
        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise PlanValidationError(f"{field}.params", "必须是空对象")
        if params:
            raise PlanValidationError(
                f"{field}.params",
                "当前接口不支持非空参数",
            )
        limit = raw.get("limit")
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise PlanValidationError(
                    f"{field}.limit",
                    "必须是 null 或整数",
                )
            if not 1 <= limit <= MAX_ITEM_LIMIT:
                raise PlanValidationError(
                    f"{field}.limit",
                    f"必须在 1～{MAX_ITEM_LIMIT} 之间",
                )
        items.append(PlanItem(content_type=content_type, limit=limit))
    return tuple(items)


def plan_payload(items: tuple[PlanItem, ...]) -> list[dict[str, object]]:
    """对外输出的栏目数组：顺序、类型、条数与可读名称。"""
    return [
        {
            "content_type": item.content_type,
            "label": item.label,
            "limit": item.limit,
        }
        for item in items
    ]


def plan_digest(items: tuple[PlanItem, ...]) -> str:
    """栏目配置的稳定摘要，用于判断一次查询期间的配置是否变过。"""
    material = "|".join(
        f"{item.content_type}:{item.limit if item.limit is not None else '*'}"
        for item in items
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def plan_items_from_stored(
    stored: tuple[StoredProfileItem, ...],
) -> tuple[PlanItem, ...]:
    return tuple(
        PlanItem(content_type=item.content_type, limit=item.limit)
        for item in stored
    )


def plan_items_to_stored(
    items: tuple[PlanItem, ...],
) -> tuple[StoredProfileItem, ...]:
    return tuple(
        StoredProfileItem(
            content_type=item.content_type,
            params_json="{}",
            limit=item.limit,
        )
        for item in items
    )


__all__ = [
    "MAX_ITEM_LIMIT",
    "MAX_PLAN_ITEMS",
    "PlanItem",
    "PlanValidationError",
    "normalize_plan_items",
    "plan_digest",
    "plan_items_from_stored",
    "plan_items_to_stored",
    "plan_payload",
]
