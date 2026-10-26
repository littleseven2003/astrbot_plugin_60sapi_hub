"""内容组合预览的纯文字与 Markdown 渲染。

本模块只接受组合预览的普通 Python 数据，不依赖 AstrBot、HTTP、数据库或
浏览器，也不修改传入结构。调用方必须基于同一份已截取的栏目结果生成两种
输出。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

_STATUS_TEXT = {
    "complete": "完整成功",
    "partial": "部分成功",
    "failed": "全部失败",
}


def render_profile_outputs(data: Any) -> dict[str, str]:
    """返回共享同一预览数据的 text 和 markdown 字符串。"""
    return {
        "text": render_profile_text(data),
        "markdown": render_profile_markdown(data),
    }


def render_profile_text(data: Any) -> str:
    if not isinstance(data, dict):
        raise ValueError("组合预览数据格式异常")
    lines: list[str] = []
    name = _plain_inline(data.get("profile_name")) or "未知组合"
    site = _plain_inline(data.get("site_name")) or "未知站点"
    lines.append(f"组合：{name}")
    lines.append(f"来源站点：{site}")
    lines.append(f"状态：{_status_text(data)}")
    lines.append("")

    sections = data.get("sections")
    if not isinstance(sections, list):
        raise ValueError("组合预览栏目格式异常")
    for section in sections:
        if not isinstance(section, dict):
            continue
        lines.extend(_text_section(section))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_profile_markdown(data: Any) -> str:
    if not isinstance(data, dict):
        raise ValueError("组合预览数据格式异常")
    lines: list[str] = []
    name = _md_inline(data.get("profile_name")) or "未知组合"
    site = _md_inline(data.get("site_name")) or "未知站点"
    lines.append(f"# {name}")
    lines.append("")
    lines.append(f"来源站点：{site}")
    lines.append("")
    lines.append(f"状态：{_status_text(data)}")
    lines.append("")

    sections = data.get("sections")
    if not isinstance(sections, list):
        raise ValueError("组合预览栏目格式异常")
    for section in sections:
        if not isinstance(section, dict):
            continue
        lines.extend(_markdown_section(section))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _text_section(section: dict[str, Any]) -> list[str]:
    label = _plain_inline(section.get("label")) or _plain_inline(section.get("content_type"))
    lines = [f"【{label or '栏目'}】"]
    if section.get("status") == "error":
        lines.append(
            "获取失败："
            f"{_plain_inline(section.get('message')) or '栏目获取失败。'}"
            f"（{_plain_inline(section.get('code')) or 'error'}）",
        )
        return lines

    lines.append(f"获取时间：{_clean(section.get('fetched_at')) or '未知'}")
    lines.append(f"展示条数：{_display_count(section)}")
    content_date = _plain_inline(section.get("content_date"))
    if content_date:
        lines.append(f"内容日期：{content_date}")
    for warning in _warnings(section):
        lines.append(f"提示：{_plain_inline(warning)}")

    items = _items(section)
    if not items:
        lines.append("（空列表，0 条）")
        return lines
    for index, item in enumerate(items, start=1):
        title = _plain_inline(item.get("title"))
        if not title:
            continue
        lines.append(f"{index}. {title}")
        detail = _plain_inline(item.get("detail"))
        if detail:
            lines.append(f"   摘要：{detail}")
        hot_desc = _plain_inline(item.get("hot_value_desc"))
        if hot_desc:
            lines.append(f"   热度：{hot_desc}")
        if _has_int_hot(item):
            lines.append(f"   热度：{item['hot_value']}")
        link = _plain_inline(item.get("link"))
        if link:
            lines.append(f"   链接：{link}")
    return lines


def _markdown_section(section: dict[str, Any]) -> list[str]:
    label = _md_inline(section.get("label")) or _md_inline(section.get("content_type"))
    lines = [f"## {label or '栏目'}", ""]
    if section.get("status") == "error":
        message = _clean(section.get("message")) or "栏目获取失败。"
        code = _clean(section.get("code")) or "error"
        lines.append(f"- 获取失败：{_md_inline(message)}（{_md_inline(code)}）")
        return lines

    lines.append(f"- 获取时间：{_clean(section.get('fetched_at')) or '未知'}")
    lines.append(f"- 展示条数：{_display_count(section)}")
    content_date = _md_inline(section.get("content_date"))
    if content_date:
        lines.append(f"- 内容日期：{content_date}")
    for warning in _warnings(section):
        lines.append(f"- 提示：{_md_inline(warning)}")

    items = _items(section)
    if not items:
        lines.extend(["", "（空列表，0 条）"])
        return lines
    lines.append("")
    for index, item in enumerate(items, start=1):
        title = _md_inline(item.get("title"))
        if not title:
            continue
        lines.append(f"{index}. {title}")
        detail = _md_inline(item.get("detail"))
        if detail:
            lines.append(f"   - 摘要：{detail}")
        hot_desc = _md_inline(item.get("hot_value_desc"))
        if hot_desc:
            lines.append(f"   - 热度：{hot_desc}")
        if _has_int_hot(item):
            lines.append(f"   - 热度：{item['hot_value']}")
        link = _clean(item.get("link"))
        if link:
            safe_link = _markdown_link(link)
            if safe_link is not None:
                lines.append(f"   - {safe_link}")
            else:
                lines.append(f"   - 链接：{_md_inline(link)}")
    return lines


def _items(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("items")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _has_int_hot(item: dict[str, Any]) -> bool:
    value = item.get("hot_value")
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _display_count(section: dict[str, Any]) -> str:
    items = _items(section)
    shown = section.get("displayed_count")
    total = section.get("total_item_count")
    if not isinstance(shown, int) or isinstance(shown, bool):
        shown = len(items)
    if not isinstance(total, int) or isinstance(total, bool):
        total = shown
    limit = section.get("limit")
    if limit is None:
        return f"{shown} / {total}（全部）"
    if isinstance(limit, int) and not isinstance(limit, bool):
        return f"{shown} / {total}（最多 {limit} 条）"
    return f"{shown} / {total}"


def _warnings(section: dict[str, Any]) -> list[str]:
    raw = section.get("warnings")
    warnings = [
        _clean(item)
        for item in raw
        if isinstance(item, str) and _clean(item)
    ] if isinstance(raw, list) else []
    if not warnings and section.get("date_is_unknown"):
        warnings.append("上游未提供可识别的内容日期，日期保持未知。")
    elif not warnings and section.get("is_stale"):
        warnings.append("上游内容日期早于当前日期；未用今天覆盖。")
    return warnings


def _status_text(data: dict[str, Any]) -> str:
    status = data.get("status")
    if status == "partial":
        success = data.get("success_count", 0)
        failure = data.get("failure_count", 0)
        return f"部分成功（{success} 成功 / {failure} 失败）"
    return _STATUS_TEXT.get(status, "状态未知")


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    # 不可信文本不能引入新的行结构或 HTML/Markdown 结构。
    text = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in value
    )
    return " ".join(text.split()).strip()


def _plain_inline(value: Any) -> str:
    text = _clean(value)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _md_inline(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    text = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    for char in "\\`*_{}[]()#+-.!|~":
        text = text.replace(char, f"\\{char}")
    return text


def _markdown_link(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except (TypeError, ValueError, UnicodeError):
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    try:
        safe = quote(
            url,
            safe="/:?&=#%+@!$~-._",
        )
    except (TypeError, ValueError):
        return None
    if not safe:
        return None
    return f"[链接](<{safe}>)"


__all__ = [
    "render_profile_markdown",
    "render_profile_outputs",
    "render_profile_text",
]
