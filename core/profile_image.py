"""内容组合预览的固定样式中文图片渲染。

渲染器只接收已经截取、已经验证的组合预览数据；不访问 HTTP、数据库、宿主
或浏览器。使用 Pillow 在内存中生成分页 PNG，不写临时图片文件。
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - 由渲染入口给出可理解错误
    Image = None
    ImageDraw = None
    ImageFont = None


class ProfileImageError(Exception):
    """图片渲染错误基类。"""


class FontUnavailableError(ProfileImageError):
    """找不到可用的中文字体。"""


class ImageLimitError(ProfileImageError):
    """页数、像素或输出字节超过限制。"""


class ImageRenderCancelled(ProfileImageError):
    """渲染被取消或超时。"""


@dataclass(frozen=True, slots=True)
class ImageRenderLimits:
    page_width: int = 900
    page_height: int = 1200
    max_pages: int = 6
    max_total_pixels: int = 8_000_000
    max_page_bytes: int = 4 * 1024 * 1024
    max_total_bytes: int = 12 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImagePage:
    index: int
    width: int
    height: int
    png: bytes


_FONT_CANDIDATES = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)

_BACKGROUND = (247, 244, 238)
_PAPER = (255, 253, 249)
_INK = (33, 36, 34)
_MUTED = (99, 105, 100)
_ACCENT = (32, 105, 86)
_ERROR = (160, 56, 50)
_RULE = (215, 209, 198)
_CLOSING_PUNCT = set("，。、；：！？）》】”’…")


class ProfileImageRenderer:
    """把一个组合预览快照排成固定尺寸的中文分页图片。"""

    def __init__(
        self,
        *,
        font_path: str | Path | None = None,
        limits: ImageRenderLimits | None = None,
    ) -> None:
        self._font_path = Path(font_path) if font_path else None
        self._limits = limits or ImageRenderLimits()
        self._font_cache: dict[int, Any] = {}

    @property
    def limits(self) -> ImageRenderLimits:
        return self._limits

    def render(
        self,
        data: Any,
        *,
        created_at: str = "",
        cancel_event: Any | None = None,
    ) -> list[ImagePage]:
        if Image is None or ImageDraw is None or ImageFont is None:
            raise FontUnavailableError("图片渲染依赖 Pillow 未安装。")
        if not isinstance(data, dict):
            raise ProfileImageError("组合预览数据格式异常。")
        font_path = self._font_path or find_cjk_font()
        if not font_path.exists():
            raise FontUnavailableError("找不到可用的中文字体文件。")
        self._font_cache.clear()
        self._cancel_event = cancel_event

        first_header = self._first_header(data, created_at)
        cont_header = self._cont_header(data)
        content = self._content_lines(data)
        first_start = self._header_end(first_header)
        cont_start = self._cont_header_end(cont_header)
        pages = self._paginate(content, first_start, cont_start)
        if len(pages) > self._limits.max_pages:
            raise ImageLimitError(
                f"图片页数超过上限（{self._limits.max_pages} 页）。",
            )
        pixels = (
            self._limits.page_width
            * self._limits.page_height
            * len(pages)
        )
        if pixels > self._limits.max_total_pixels:
            raise ImageLimitError("图片总像素超过上限。")

        rendered: list[ImagePage] = []
        total_bytes = 0
        for index, page_lines in enumerate(pages, start=1):
            self._check_cancelled()
            image = self._draw_page(
                data,
                created_at,
                page_lines,
                index,
                len(pages),
                first_header,
                cont_header,
                first_start,
                cont_start,
            )
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            png = buffer.getvalue()
            if len(png) > self._limits.max_page_bytes:
                raise ImageLimitError("单页图片超过字节上限。")
            total_bytes += len(png)
            if total_bytes > self._limits.max_total_bytes:
                raise ImageLimitError("图片总字节超过上限。")
            rendered.append(
                ImagePage(
                    index=index,
                    width=self._limits.page_width,
                    height=self._limits.page_height,
                    png=png,
                ),
            )
        return rendered

    def _font(self, size: int) -> Any:
        if ImageFont is None:
            raise FontUnavailableError("图片渲染依赖 Pillow 未安装。")
        if size not in self._font_cache:
            path = self._font_path or find_cjk_font()
            try:
                self._font_cache[size] = ImageFont.truetype(
                    str(path),
                    size,
                )
            except Exception as exc:
                raise FontUnavailableError(
                    "无法加载中文字体，请检查宿主字体环境。",
                ) from exc
        return self._font_cache[size]

    def _check_cancelled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise ImageRenderCancelled("图片渲染已取消。")

    def _first_header(self, data: dict[str, Any], created_at: str) -> list[tuple[str, int, tuple[int, int, int], int]]:
        name = _clean(data.get("profile_name")) or "未命名组合"
        site = _clean(data.get("site_name")) or "未知站点"
        status = _status_text(data)
        lines: list[tuple[str, int, tuple[int, int, int], int]] = []
        lines.append(("60sAPI Hub", 18, _ACCENT, 0))
        for part in self._wrap(name, 34, self._content_width()):
            lines.append((part, 34, _INK, 8 if part != name else 10))
        for part in self._wrap(f"来源站点：{site}", 20, self._content_width()):
            lines.append((part, 20, _MUTED, 8))
        for part in self._wrap(f"状态：{status}", 20, self._content_width()):
            lines.append((part, 20, _MUTED, 4))
        if created_at:
            for part in self._wrap(
                f"预览生成时间：{_clean(created_at)}",
                18,
                self._content_width(),
            ):
                lines.append((part, 18, _MUTED, 4))
        return lines

    def _cont_header(self, data: dict[str, Any]) -> list[tuple[str, int, tuple[int, int, int], int]]:
        name = _clean(data.get("profile_name")) or "未命名组合"
        lines: list[tuple[str, int, tuple[int, int, int], int]] = [
            ("60sAPI Hub · 组合图片", 18, _ACCENT, 0),
        ]
        for part in self._wrap(name, 17, self._content_width()):
            lines.append((part, 17, _MUTED, 2))
        return lines

    def _content_lines(self, data: dict[str, Any]) -> list[tuple[str, int, tuple[int, int, int], int]]:
        lines: list[tuple[str, int, tuple[int, int, int], int]] = []
        sections = data.get("sections")
        if not isinstance(sections, list):
            sections = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            self._check_cancelled()
            label = _clean(section.get("label")) or _clean(section.get("content_type")) or "栏目"
            lines.append(("", 10, _MUTED, 0))
            lines.append((label, 30, _ACCENT, 0))
            if section.get("status") == "error":
                message = _clean(section.get("message")) or "栏目获取失败。"
                code = _clean(section.get("code")) or "error"
                lines.extend(self._wrapped_lines(
                    f"获取失败：{message}（{code}）",
                    22,
                    _ERROR,
                ))
                continue
            fetched = _clean(section.get("fetched_at")) or "未知"
            shown = _display_count(section)
            lines.extend(self._wrapped_lines(
                f"获取时间：{fetched} ｜ 展示条数：{shown}",
                18,
                _MUTED,
            ))
            content_date = _clean(section.get("content_date"))
            if content_date:
                lines.extend(self._wrapped_lines(
                    f"内容日期：{content_date}",
                    18,
                    _MUTED,
                ))
            for warning in _warnings(section):
                lines.extend(self._wrapped_lines(
                    f"提示：{warning}",
                    18,
                    _ERROR,
                ))
            items = _items(section)
            if not items:
                lines.extend(self._wrapped_lines(
                    "（空列表，0 条）",
                    20,
                    _MUTED,
                ))
                continue
            for index, item in enumerate(items, start=1):
                self._check_cancelled()
                title = _clean(item.get("title"))
                if not title:
                    continue
                lines.extend(self._wrapped_lines(
                    f"{index}. {title}",
                    24,
                    _INK,
                ))
                detail = _clean(item.get("detail"))
                if detail:
                    for line in self._wrapped_lines(
                        f"摘要：{detail}",
                        20,
                        _MUTED,
                        18,
                    ):
                        lines.append(line)
                hot_desc = _clean(item.get("hot_value_desc"))
                if hot_desc:
                    lines.extend(self._wrapped_lines(
                        f"热度：{hot_desc}",
                        20,
                        _ACCENT,
                        18,
                    ))
                hot = item.get("hot_value")
                if isinstance(hot, int) and not isinstance(hot, bool) and hot >= 0:
                    lines.extend(self._wrapped_lines(
                        f"热度：{hot}",
                        20,
                        _ACCENT,
                        18,
                    ))
                link = _clean(item.get("link"))
                if link:
                    lines.extend(self._wrapped_lines(
                        f"链接：{link}",
                        17,
                        _MUTED,
                        18,
                    ))
        return lines

    def _wrap(
        self,
        text: str,
        size: int,
        width: int,
    ) -> list[str]:
        font = self._font(size)
        clean = _clean(text)
        if not clean:
            return [""]
        lines: list[str] = []
        current = ""
        for char in clean:
            candidate = current + char
            if current and font.getlength(candidate) > width:
                if char in _CLOSING_PUNCT and len(current) > 1:
                    lines.append(current[:-1])
                    current = current[-1] + char
                else:
                    lines.append(current)
                    current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def _wrapped_lines(
        self,
        text: str,
        size: int,
        color: tuple[int, int, int],
        indent: int = 0,
    ) -> list[tuple[str, int, tuple[int, int, int], int]]:
        width = self._content_width() - indent
        return [
            (line, size, color, indent)
            for line in self._wrap(text, size, width)
        ]

    def _paginate(
        self,
        lines: list[tuple[str, int, tuple[int, int, int], int]],
        first_start: int,
        cont_start: int,
    ) -> list[list[tuple[str, int, tuple[int, int, int], int]]]:
        pages: list[list[tuple[str, int, tuple[int, int, int], int]]] = [[]]
        y = first_start
        bottom = self._limits.page_height - self._margin() - 70
        for line in lines:
            self._check_cancelled()
            spacing = 6
            height = line[1] + spacing
            if pages[-1] and y + height > bottom:
                pages.append([])
                y = cont_start
            pages[-1].append(line)
            y += height
        return pages

    def _draw_page(
        self,
        data: dict[str, Any],
        created_at: str,
        lines: list[tuple[str, int, tuple[int, int, int], int]],
        index: int,
        total: int,
        first_header: list[tuple[str, int, tuple[int, int, int], int]],
        cont_header: list[tuple[str, int, tuple[int, int, int], int]],
        first_start: int,
        cont_start: int,
    ) -> Any:
        assert Image is not None and ImageDraw is not None
        image = Image.new(
            "RGB",
            (self._limits.page_width, self._limits.page_height),
            _BACKGROUND,
        )
        draw = ImageDraw.Draw(image)
        margin = self._margin()
        draw.rectangle(
            (
                margin,
                margin,
                self._limits.page_width - margin,
                self._limits.page_height - margin,
            ),
            fill=_PAPER,
        )
        header = first_header if index == 1 else cont_header
        y = self._draw_header(draw, header, margin)
        draw.line(
            (margin, y + 6, self._limits.page_width - margin, y + 6),
            fill=_RULE,
            width=2,
        )
        y += 22
        for text, size, color, indent in lines:
            self._check_cancelled()
            draw.text(
                (margin + indent, y),
                text,
                font=self._font(size),
                fill=color,
            )
            y += size + 6
        page_text = f"第 {index} / {total} 页"
        footer_width = self._font(17).getlength(page_text)
        draw.text(
            (
                (self._limits.page_width - footer_width) // 2,
                self._limits.page_height - margin - 28,
            ),
            page_text,
            font=self._font(17),
            fill=_MUTED,
        )
        return image

    def _draw_header(
        self,
        draw: Any,
        header: list[tuple[str, int, tuple[int, int, int], int]],
        margin: int,
    ) -> int:
        y = margin
        for text, size, color, spacing in header:
            y += spacing
            draw.text(
                (margin, y),
                text,
                font=self._font(size),
                fill=color,
            )
            y += size + 6
        return y

    def _header_end(self, header: list[tuple[str, int, tuple[int, int, int], int]]) -> int:
        y = self._margin()
        for _text, size, _color, spacing in header:
            y += spacing + size + 6
        return y + 22

    def _cont_header_end(self, header: list[tuple[str, int, tuple[int, int, int], int]]) -> int:
        return self._header_end(header)

    def _content_width(self) -> int:
        return self._limits.page_width - 2 * self._margin()

    def _margin(self) -> int:
        return 54


def find_cjk_font() -> Path:
    env_path = os.environ.get("ASTRBOT_60SAPI_FONT")
    candidates = ((env_path,) if env_path else ()) + _FONT_CANDIDATES
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            continue
        if ImageFont is None:
            break
        try:
            ImageFont.truetype(str(path), 20)
        except Exception:
            continue
        return path
    raise FontUnavailableError(
        "未找到可用的中文字体；请安装 Noto Sans CJK/WenQuanYi，"
        "或通过 ASTRBOT_60SAPI_FONT 指定字体文件。",
    )


def _status_text(data: dict[str, Any]) -> str:
    status = data.get("status")
    if status == "partial":
        return (
            "部分成功（"
            f"{data.get('success_count', 0)} 成功 / "
            f"{data.get('failure_count', 0)} 失败）"
        )
    return "完整成功" if status == "complete" else "状态未知"


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


def _items(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("items")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


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


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in value
    )
    return " ".join(text.split()).strip()


__all__ = [
    "FontUnavailableError",
    "ImageLimitError",
    "ImagePage",
    "ImageRenderCancelled",
    "ImageRenderLimits",
    "ProfileImageError",
    "ProfileImageRenderer",
    "find_cjk_font",
]
