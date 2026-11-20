"""单站优先、缺栏时按栏目拼合多站点内容。

判定只使用已经取到的各站点预览结果，不重新请求上游：

1. 按候选顺序找第一个能同时满足全部栏目与条数的站点，整次使用它的内容；
2. 没有任何单站满足时，按任务栏目顺序逐栏选择候选里最靠前的合格站点，
   同一栏目只取一个站点的完整列表，不跨站拼条数；
3. 仍缺栏目时保留已选栏目并列出缺项，由调用方决定提示或告警。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .site_requirements import Requirement, Shortfall

MODE_SINGLE = "single"
MODE_MULTI = "multi"
MODE_MISSING = "missing"


@dataclass(frozen=True, slots=True)
class SiteProbe:
    """一个候选站点本次取到的结果；sections 可能为空或不完整。"""

    site_id: str
    site_name: str | None
    revision: int
    sections: tuple[dict[str, Any], ...]
    satisfied: bool


@dataclass(frozen=True, slots=True)
class Assembly:
    """整次内容的来源决定：单站、拼合或仍缺栏目。"""

    mode: str
    site_id: str | None
    site_name: str | None
    sites: tuple[tuple[str, int], ...]
    sections: tuple[dict[str, Any], ...]
    assignments: tuple[dict[str, Any], ...]
    missing: tuple[dict[str, Any], ...]

    @property
    def satisfied(self) -> bool:
        return self.mode != MODE_MISSING

    @property
    def multi_site(self) -> bool:
        return self.mode == MODE_MULTI


def section_available(section: dict[str, Any] | None, required: int | None) -> bool:
    if not isinstance(section, dict) or section.get("status") != "ok":
        return False
    if required is None or required <= 0:
        return True
    items = section.get("items")
    count = len(items) if isinstance(items, list) else 0
    if count == 0:
        raw = section.get("item_count")
        count = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
    return count >= required


def _index_sections(sections: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for section in sections:
        content_type = section.get("content_type")
        if isinstance(content_type, str) and content_type not in indexed:
            indexed[content_type] = section
    return indexed


def assemble(
    probes: Iterable[SiteProbe],
    requirements: Iterable[Requirement],
) -> Assembly:
    ordered = tuple(probes)
    required = tuple(requirements)

    # 1. 单站优先：整次使用第一个满足全部要求的站点。
    for probe in ordered:
        if probe.satisfied:
            return Assembly(
                mode=MODE_SINGLE,
                site_id=probe.site_id,
                site_name=probe.site_name,
                sites=((probe.site_id, probe.revision),),
                sections=probe.sections,
                assignments=(
                    {
                        "content_type": item.content_type,
                        "label": item.label,
                        "site_id": probe.site_id,
                        "site_name": probe.site_name,
                        "revision": probe.revision,
                    }
                    for item in required
                ),
                missing=(),
            )

    # 2. 按栏目拼合：每栏取候选顺序里第一个合格的站点，整栏使用它的列表。
    indexed = [
        (probe, _index_sections(probe.sections)) for probe in ordered
    ]
    selected: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    used_sites: dict[str, int] = {}
    for item in required:
        chosen: tuple[SiteProbe, dict[str, Any]] | None = None
        for probe, sections in indexed:
            section = sections.get(item.content_type)
            if section_available(section, item.required):
                chosen = (probe, section)  # type: ignore[arg-type]
                break
        if chosen is None:
            best = None
            for probe, sections in indexed:
                section = sections.get(item.content_type)
                if not isinstance(section, dict):
                    continue
                raw = section.get("items")
                count = len(raw) if isinstance(raw, list) else 0
                if best is None or count > best[1]:
                    best = (probe, count, section.get("status"))
            missing.append({
                "content_type": item.content_type,
                "label": item.label,
                "required": item.required,
                "available": best[1] if best else 0,
                "reason": (best[2] if best and best[1] else "missing"),
            })
            continue
        probe, section = chosen
        selected.append(section)
        assignments.append({
            "content_type": item.content_type,
            "label": item.label,
            "site_id": probe.site_id,
            "site_name": probe.site_name,
            "revision": probe.revision,
        })
        used_sites[probe.site_id] = probe.revision

    mode = MODE_MISSING if missing else MODE_MULTI
    first_site = assignments[0]["site_id"] if assignments else None
    first_name = assignments[0]["site_name"] if assignments else None
    return Assembly(
        mode=mode,
        site_id=first_site,
        site_name=first_name if not missing else None,
        sites=tuple(used_sites.items()),
        sections=tuple(selected),
        assignments=tuple(assignments),
        missing=tuple(missing),
    )


def shortfalls(missing: Iterable[dict[str, Any]]) -> tuple[Shortfall, ...]:
    return tuple(
        Shortfall(
            str(item.get("content_type")),
            item.get("required") if isinstance(item.get("required"), int) else None,
            int(item.get("available") or 0),
            str(item.get("reason") or "missing"),
        )
        for item in missing
    )


__all__ = [
    "Assembly",
    "MODE_MISSING",
    "MODE_MULTI",
    "MODE_SINGLE",
    "SiteProbe",
    "assemble",
    "section_available",
    "shortfalls",
]
