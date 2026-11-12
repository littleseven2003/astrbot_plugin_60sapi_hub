"""任务“满足全部类别与条数”的统一判定。

后台测试与自动执行都必须走这里，避免测试通过而执行条件不同。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class Requirement:
    """一类内容的要求；required 为 None 表示不限条数。"""

    content_type: str
    required: int | None
    label: str = ""


@dataclass(frozen=True, slots=True)
class Shortfall:
    content_type: str
    required: int | None
    available: int
    reason: str


def _available_count(section: dict[str, Any]) -> int:
    items = section.get("items")
    if isinstance(items, list):
        return len(items)
    count = section.get("item_count")
    if isinstance(count, int) and not isinstance(count, bool):
        return count
    return 0


def evaluate_sections(
    sections: Iterable[dict[str, Any]],
    requirements: Iterable[Requirement],
) -> tuple[bool, tuple[Shortfall, ...]]:
    """同一站点必须同时满足全部类别；缺任何一类都算不满足。"""
    indexed: dict[str, dict[str, Any]] = {}
    for section in sections:
        content_type = section.get("content_type")
        if isinstance(content_type, str) and content_type not in indexed:
            indexed[content_type] = section
    shortfalls: list[Shortfall] = []
    for requirement in requirements:
        section = indexed.get(requirement.content_type)
        if section is None:
            shortfalls.append(Shortfall(
                requirement.content_type, requirement.required, 0, "missing",
            ))
            continue
        if section.get("status") != "ok":
            shortfalls.append(Shortfall(
                requirement.content_type, requirement.required, 0, "unavailable",
            ))
            continue
        available = _available_count(section)
        # 合法空列表是正常响应；只有要求正数条目时才算不足。
        if (
            requirement.required is not None
            and requirement.required > 0
            and available < requirement.required
        ):
            shortfalls.append(Shortfall(
                requirement.content_type, requirement.required,
                available, "insufficient",
            ))
    return (not shortfalls), tuple(shortfalls)


def requirements_from_items(items: Iterable[Any]) -> tuple[Requirement, ...]:
    """把组合栏目转换为要求；limit=None 沿用现有“不限条数”语义。"""
    return tuple(
        Requirement(
            content_type=item.content_type,
            required=item.limit,
            label=getattr(item, "label", "") or "",
        )
        for item in items
    )


__all__ = [
    "Requirement",
    "Shortfall",
    "evaluate_sections",
    "requirements_from_items",
]
