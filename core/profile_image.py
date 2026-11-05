"""内容组合预览的逐栏目中文长图渲染。

一个栏目生成一张高度随内容增长的长图；不固定分页、不把一个栏目拆成多张。
渲染器只接收已经截取、已经验证的组合预览数据，不访问 HTTP、数据库、宿主
或浏览器，也不在图片中渲染链接、二维码或任何外部资源。
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
    """像素、字节或栏目数量超过限制。"""


class ImageRenderCancelled(ProfileImageError):
    """渲染被取消或超时。"""


@dataclass(frozen=True, slots=True)
class ImageRenderLimits:
    """逐栏目长图的资源上限；宽度固定，高度与字节都设有上限。"""

    width: int = 900
    max_height: int = 8000
    max_image_bytes: int = 2 * 1024 * 1024
    max_total_bytes: int = 8 * 1024 * 1024
    max_sections: int = 5


@dataclass(frozen=True, slots=True)
class SectionImage:
    """一个栏目对应的长图结果；失败栏目没有图片字节。"""

    index: int
    content_type: str
    label: str
    status: str
    width: int
    height: int
    png: bytes | None
    error: str | None


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

# 视觉规范配色：主蓝、标题区浅蓝、正文深蓝、次要文字、分隔线、浅蓝底。
_ACCENT = (49, 95, 200)          # #315fc8
_HEADER_BG = (234, 241, 255)     # 标题区浅蓝
_BG = (241, 245, 250)            # #f1f5fa
_INK = (36, 50, 74)              # #24324a
_MUTED = (108, 125, 148)         # #6c7d94
_DIVIDER = (220, 229, 240)       # #dce5f0
_BORDER = (220, 229, 240)
_WHITE = (255, 255, 255)

_WIDTH = 900
_CARD_MARGIN = 24
_CARD_PAD_X = 40
_CARD_RADIUS = 24
_HEADER_TOP_PAD = 32
_HEADER_BOTTOM_PAD = 28
_BODY_TOP_GAP = 26
_BODY_BOTTOM_GAP = 24
_FOOTER_BOTTOM_PAD = 28
_ITEM_TEXT_X = 52
_DECOR_BAR_W = 6
_DECOR_BAR_H = 64

_MAX_CONTENT_CHARS = 200_000
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|.", re.UNICODE)
_CLOSING_PUNCT = set("，。、；：！？）》】”’…")
_URL_RE = re.compile(r'''https?://[^\s<>"']+''', re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?")
_WEEKDAY_RE = re.compile(r"(星期[一二三四五六日天]|周[一二三四五六日天])")
_CHINA_TZ = timezone(timedelta(hours=8))
_FOOTER_LEFT_WIDTH = 300
_FOOTER_COLUMN_GAP = 40
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SAFE_ERROR_RE = re.compile(r"[a-z0-9_]{1,32}")

_STATUS_SUCCESS = "success"
_STATUS_EMPTY = "empty"
_STATUS_FAILED = "failed"

_ERROR_SECTION_FAILED = "section_failed"
_ERROR_TOO_TALL = "section_too_tall"
_ERROR_TOO_LARGE = "section_too_large"
_ERROR_TOO_LONG = "section_too_long"
_ERROR_TOTAL_BYTES = "total_bytes_exceeded"


@dataclass(frozen=True, slots=True)
class _Part:
    text: str
    size: int
    color: tuple[int, int, int]
    bold: bool = False
    x: int = 0
    right: int | None = None


@dataclass(frozen=True, slots=True)
class _Line:
    parts: tuple[_Part, ...] = ()
    rule: bool = False
    gap_before: int = 0
    gap_after: int = 0


class ProfileImageRenderer:
    """把组合预览数据渲染为每个栏目一张的中文长图。"""

    def __init__(
        self,
        font_path: str | os.PathLike[str] | None = None,
        limits: ImageRenderLimits | None = None,
    ) -> None:
        self._font_path = Path(font_path) if font_path else None
        self._limits = limits or ImageRenderLimits()
        self._font_cache: dict[int, Any] = {}
        self._validated_font_path: Path | None = None
        self._cancel_event: Any | None = None

    @property
    def limits(self) -> ImageRenderLimits:
        return self._limits

    def render(
        self,
        data: Any,
        *,
        limits: ImageRenderLimits | None = None,
        created_at: str = "",
        cancel_event: Any | None = None,
    ) -> tuple[SectionImage, ...]:
        """按组合配置顺序返回每个栏目对应的长图结果。"""
        if Image is None or ImageDraw is None or ImageFont is None:
            raise FontUnavailableError("图片渲染依赖 Pillow 未安装。")
        active = limits if limits is not None else self._limits
        self._validate_limits(active)
        if not isinstance(data, dict):
            raise ProfileImageError("组合预览数据格式异常。")
        sections = data.get("sections")
        if not isinstance(sections, list):
            raise ProfileImageError("组合预览栏目格式异常。")
        for section in sections:
            if not isinstance(section, dict):
                raise ProfileImageError("组合预览栏目格式异常。")
        if len(sections) > active.max_sections:
            raise ImageLimitError(
                f"栏目数量超过上限（{active.max_sections} 个）。",
            )
        font_path = self._font_path or find_cjk_font()
        if not font_path.exists():
            raise FontUnavailableError("找不到可用的中文字体文件。")
        self._font_cache.clear()
        self._validated_font_path = None
        self._cancel_event = cancel_event
        self._check_cancelled()

        site_name = _clean(data.get("site_name")) or "60sAPI Hub"
        results: list[SectionImage] = []
        total_bytes = 0
        bytes_exhausted = False
        for index, section in enumerate(sections, start=1):
            self._check_cancelled()
            label = _section_label(section)
            content_type = _clean(section.get("content_type"))
            if bytes_exhausted:
                results.append(
                    self._failed(
                        active, index, label, content_type,
                        _ERROR_TOTAL_BYTES,
                    ),
                )
                continue
            try:
                result = self._render_section(
                    section, index, label, content_type,
                    active, created_at, site_name,
                )
            except ImageRenderCancelled:
                raise
            except ImageLimitError:
                raise
            except ProfileImageError:
                raise
            except Exception as exc:
                raise ProfileImageError("图片渲染失败。") from exc
            if result.png is not None:
                if total_bytes + len(result.png) > active.max_total_bytes:
                    result = self._failed(
                        active, index, label, content_type,
                        _ERROR_TOTAL_BYTES,
                    )
                    bytes_exhausted = True
                else:
                    total_bytes += len(result.png)
            results.append(result)
        return tuple(results)

    def _render_section(
        self,
        section: dict[str, Any],
        index: int,
        label: str,
        content_type: str,
        limits: ImageRenderLimits,
        created_at: str,
        site_name: str,
    ) -> SectionImage:
        if _section_failed(section):
            return self._failed(
                limits, index, label, content_type,
                _safe_error_code(section),
            )
        items = _items(section)
        if _content_char_count(section, items) > _MAX_CONTENT_CHARS:
            return self._failed(
                limits, index, label, content_type, _ERROR_TOO_LONG,
            )
        header = self._header_lines(
            section, label, content_type, limits.width,
        )
        body = self._body_lines(items, limits.width)
        footer = self._footer_lines(
            section, site_name, created_at, limits.width,
        )
        header_h = (
            _HEADER_TOP_PAD
            + self._lines_height(header)
            + _HEADER_BOTTOM_PAD
        )
        body_h = self._lines_height(body)
        footer_h = self._lines_height(footer)
        height = (
            _CARD_MARGIN
            + header_h
            + _BODY_TOP_GAP
            + body_h
            + _BODY_BOTTOM_GAP
            + footer_h
            + _FOOTER_BOTTOM_PAD
            + _CARD_MARGIN
        )
        if height > limits.max_height:
            return self._failed(
                limits, index, label, content_type, _ERROR_TOO_TALL,
            )
        png = self._draw(header, body, footer, height, limits.width, header_h)
        if len(png) > limits.max_image_bytes:
            return self._failed(
                limits, index, label, content_type, _ERROR_TOO_LARGE,
            )
        status = _STATUS_EMPTY if not items else _STATUS_SUCCESS
        return SectionImage(
            index=index,
            content_type=content_type,
            label=label,
            status=status,
            width=limits.width,
            height=height,
            png=png,
            error=None,
        )

    @staticmethod
    def _failed(
        limits: ImageRenderLimits,
        index: int,
        label: str,
        content_type: str,
        error: str,
    ) -> SectionImage:
        return SectionImage(
            index=index,
            content_type=content_type,
            label=label,
            status=_STATUS_FAILED,
            width=limits.width,
            height=0,
            png=None,
            error=error,
        )

    def _header_lines(
        self,
        section: dict[str, Any],
        label: str,
        content_type: str,
        width: int,
    ) -> list[_Line]:
        lines: list[_Line] = [
            _Line(
                parts=(_Part("60sAPI Hub", 18, _ACCENT, True, 0),),
                gap_after=12,
            ),
        ]
        label_width = self._content_width(width) - 90
        for part in self._wrap(label, 38, label_width, bold=True):
            lines.append(
                _Line(
                    parts=(_Part(part, 38, _INK, True, 0),),
                    gap_after=6,
                ),
            )
        meta = self._meta_text(section, content_type)
        if meta:
            for part in self._wrap(meta, 17, label_width):
                lines.append(
                    _Line(
                        parts=(_Part(part, 17, _MUTED, False, 0),),
                        gap_after=2,
                    ),
                )
        return lines

    def _body_lines(
        self,
        items: list[dict[str, Any]],
        width: int,
    ) -> list[_Line]:
        lines: list[_Line] = []
        if not items:
            lines.append(
                _Line(
                    parts=(
                        _Part("（上游本次没有返回内容）", 22, _MUTED, True, 0),
                    ),
                    gap_before=6,
                    gap_after=8,
                ),
            )
            lines.append(
                _Line(
                    parts=(
                        _Part(
                            "合法空栏目：获取成功，但本次没有可展示条目。",
                            18, _MUTED, False, 0,
                        ),
                    ),
                    gap_after=10,
                ),
            )
            return lines
        text_width = self._content_width(width) - _ITEM_TEXT_X
        for position, item in enumerate(items, start=1):
            title = _display_text(item.get("title"))
            title_lines = self._wrap(title, 24, text_width, bold=True)
            for offset, part in enumerate(title_lines):
                parts: list[_Part] = []
                if offset == 0:
                    parts.append(
                        _Part(f"{position:02d}", 22, _ACCENT, True, 0),
                    )
                parts.append(_Part(part, 24, _INK, True, _ITEM_TEXT_X))
                lines.append(
                    _Line(
                        parts=tuple(parts),
                        gap_before=8 if position > 1 and offset == 0 else 0,
                        gap_after=2,
                    ),
                )
            detail = _display_text(item.get("detail"))
            if detail:
                for offset, part in enumerate(
                    self._wrap(detail, 19, text_width),
                ):
                    lines.append(
                        _Line(
                            parts=(
                                _Part(part, 19, _MUTED, False, _ITEM_TEXT_X),
                            ),
                            gap_before=4 if offset == 0 else 0,
                            gap_after=2,
                        ),
                    )
            hot = _hot_text(item)
            if hot:
                for offset, part in enumerate(
                    self._wrap(hot, 18, text_width),
                ):
                    lines.append(
                        _Line(
                            parts=(
                                _Part(part, 18, _MUTED, False, _ITEM_TEXT_X),
                            ),
                            gap_before=4 if offset == 0 else 0,
                            gap_after=2,
                        ),
                    )
            lines.append(_Line(rule=True, gap_before=12, gap_after=12))
        return lines

    def _footer_lines(
        self,
        section: dict[str, Any],
        site_name: str,
        created_at: str,
        width: int,
    ) -> list[_Line]:
        source = (
            _display_text(section.get("site_name"))
            or _display_text(site_name)
            or "60sAPI Hub"
        )
        fetched = (
            _display_text(section.get("fetched_at"))
            or _display_text(created_at)
            or "未知"
        )
        fetched_text = _format_fetched_time(fetched)
        content_width = self._content_width(width)
        left_width = min(_FOOTER_LEFT_WIDTH, content_width // 2)
        right_width = content_width - left_width - _FOOTER_COLUMN_GAP
        left_lines: list[_Part] = [
            _Part("60sAPI Hub", 16, _ACCENT, True, 0),
            _Part("由插件根据接口数据生成", 15, _MUTED, False, 0),
        ]
        right_lines: list[_Part] = []
        for part in self._wrap(
            f"数据来源：{source}", 15, right_width,
        ):
            right_lines.append(
                _Part(part, 15, _MUTED, False, 0, content_width),
            )
        for part in self._wrap(
            f"获取时间：{fetched_text}", 15, right_width,
        ):
            right_lines.append(
                _Part(part, 15, _MUTED, False, 0, content_width),
            )
        lines: list[_Line] = [_Line(rule=True, gap_after=12)]
        for index in range(max(len(left_lines), len(right_lines))):
            parts: list[_Part] = []
            if index < len(left_lines):
                parts.append(left_lines[index])
            if index < len(right_lines):
                parts.append(right_lines[index])
            lines.append(_Line(parts=tuple(parts), gap_after=4))
        return lines

    def _meta_text(
        self,
        section: dict[str, Any],
        content_type: str,
    ) -> str:
        parts: list[str] = []
        content_date = _format_content_date(
            section.get("content_date"), section,
        )
        if content_date:
            parts.append(f"内容日期：{content_date}")
        elif content_type == "daily_news" and section.get("date_is_unknown"):
            parts.append("内容日期：未知")
        shown = _int_or_none(section.get("displayed_count"))
        if shown is None:
            shown = _int_or_none(section.get("item_count"))
        if shown is None:
            shown = len(_items(section))
        total = _int_or_none(section.get("total_item_count"))
        count_text = f"显示 {shown} 条"
        if total is not None and total > shown:
            count_text += f" ｜ 共 {total} 条"
        parts.append(count_text)
        return " ｜ ".join(parts)

    def _draw(
        self,
        header: list[_Line],
        body: list[_Line],
        footer: list[_Line],
        height: int,
        width: int,
        header_h: int,
    ) -> bytes:
        assert Image is not None and ImageDraw is not None
        image = Image.new("RGB", (width, height), _BG)
        try:
            draw = ImageDraw.Draw(image)
            card = (
                _CARD_MARGIN,
                _CARD_MARGIN,
                width - _CARD_MARGIN,
                height - _CARD_MARGIN,
            )
            draw.rounded_rectangle(
                card, radius=_CARD_RADIUS, fill=_WHITE,
            )
            band = (
                _CARD_MARGIN,
                _CARD_MARGIN,
                width - _CARD_MARGIN,
                _CARD_MARGIN + header_h + _CARD_RADIUS,
            )
            draw.rounded_rectangle(
                band, radius=_CARD_RADIUS, fill=_HEADER_BG,
            )
            draw.rectangle(
                (
                    _CARD_MARGIN + 2,
                    _CARD_MARGIN + header_h,
                    width - _CARD_MARGIN - 2,
                    _CARD_MARGIN + header_h + _CARD_RADIUS,
                ),
                fill=_HEADER_BG,
            )
            bar_x = width - _CARD_MARGIN - _CARD_PAD_X - _DECOR_BAR_W
            draw.rounded_rectangle(
                (
                    bar_x,
                    _CARD_MARGIN + _HEADER_TOP_PAD + 2,
                    bar_x + _DECOR_BAR_W,
                    _CARD_MARGIN + _HEADER_TOP_PAD + 2 + _DECOR_BAR_H,
                ),
                radius=3,
                fill=_ACCENT,
            )
            y = self._draw_lines(
                draw, header, _CARD_MARGIN + _HEADER_TOP_PAD, width,
            )
            y = self._draw_lines(
                draw,
                body,
                _CARD_MARGIN + header_h + _BODY_TOP_GAP,
                width,
            )
            y += _BODY_BOTTOM_GAP
            self._draw_lines(draw, footer, y, width)
            draw.rounded_rectangle(
                card, radius=_CARD_RADIUS, outline=_BORDER, width=2,
            )
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
        finally:
            image.close()

    def _draw_lines(
        self,
        draw: Any,
        lines: list[_Line],
        start_y: int,
        width: int,
    ) -> int:
        y = start_y
        left = _CARD_MARGIN + _CARD_PAD_X
        right = width - _CARD_MARGIN - _CARD_PAD_X
        for line in lines:
            self._check_cancelled()
            y += line.gap_before
            if line.rule:
                draw.line((left, y, right, y), fill=_DIVIDER, width=1)
                y += 1 + line.gap_after
                continue
            line_h = self._line_text_height(line)
            for part in line.parts:
                part_h = self._text_height(part.size)
                part_y = y + (line_h - part_h) // 2
                self._draw_text(draw, part, left + part.x, part_y)
            y += line_h + line.gap_after
        return y

    def _draw_text(
        self,
        draw: Any,
        part: _Part,
        x: int,
        y: int,
    ) -> None:
        if part.right is not None:
            width = self._font(part.size).getlength(part.text)
            if part.bold:
                width += 2
            x = x + part.right - width
        draw.text(
            (x, y),
            part.text,
            font=self._font(part.size),
            fill=part.color,
            stroke_width=1 if part.bold else 0,
            stroke_fill=part.color,
        )

    def _lines_height(self, lines: list[_Line]) -> int:
        return sum(self._line_height(line) for line in lines)

    def _line_height(self, line: _Line) -> int:
        if line.rule:
            return line.gap_before + 1 + line.gap_after
        return (
            line.gap_before
            + self._line_text_height(line)
            + line.gap_after
        )

    def _line_text_height(self, line: _Line) -> int:
        if not line.parts:
            return 0
        return max(self._text_height(part.size) for part in line.parts)

    def _text_height(self, size: int) -> int:
        ascent, descent = self._font(size).getmetrics()
        return int(ascent) + int(descent)

    @staticmethod
    def _content_width(width: int) -> int:
        return width - 2 * (_CARD_MARGIN + _CARD_PAD_X)

    def _wrap(
        self,
        text: str,
        size: int,
        width: int,
        *,
        bold: bool = False,
    ) -> list[str]:
        clean = _clean(text)
        if not clean or width <= 0:
            return []
        font = self._font(size)
        padding = 2 if bold else 0
        usable = width - padding

        def measure(value: str) -> float:
            return font.getlength(value) + padding

        lines: list[str] = []
        current = ""
        for token in _TOKEN_RE.findall(clean):
            self._check_cancelled()
            if not token:
                continue
            if not current and token.isspace():
                continue
            if not current and token in _CLOSING_PUNCT and lines:
                # 行首不出现收尾标点：余标点跟随上一行，最多超出一个标点宽。
                lines[-1] = lines[-1] + token
                continue
            if measure(token) > usable:
                if current:
                    lines.append(current.rstrip())
                    current = ""
                chunks = self._split_token(token, measure, usable)
                lines.extend(chunks[:-1])
                current = chunks[-1] if chunks else ""
                continue
            candidate = current + token
            if current and measure(candidate) > usable:
                if token.isspace():
                    lines.append(current.rstrip())
                    current = ""
                    continue
                if token in _CLOSING_PUNCT:
                    # 收尾标点不另起一行，跟随上一行并允许最多超出一个标点。
                    lines.append((current + token).rstrip())
                    current = ""
                    continue
                lines.append(current.rstrip())
                current = token
            else:
                current = candidate
        if current.strip():
            lines.append(current.rstrip())
        return lines or [clean]

    def _split_token(
        self,
        token: str,
        measure: Any,
        usable: int,
    ) -> list[str]:
        chunks: list[str] = []
        start = 0
        length = len(token)
        while start < length:
            self._check_cancelled()
            low = start + 1
            high = length
            best = start + 1
            while low <= high:
                middle = (low + high) // 2
                if measure(token[start:middle]) <= usable:
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            chunks.append(token[start:best])
            start = best
        return chunks

    def _font(self, size: int) -> Any:
        if ImageFont is None:
            raise FontUnavailableError("图片渲染依赖 Pillow 未安装。")
        if size not in self._font_cache:
            path = self._font_path or find_cjk_font()
            if path != self._validated_font_path:
                try:
                    font = ImageFont.truetype(str(path), 32)
                except Exception as exc:
                    raise FontUnavailableError(
                        "无法加载中文字体，请检查宿主字体环境。",
                    ) from exc
                if not _font_supports_chinese(font):
                    raise FontUnavailableError(
                        "指定字体不包含有效中文覆盖，请更换字体文件。",
                    )
                self._validated_font_path = path
            try:
                self._font_cache[size] = ImageFont.truetype(
                    str(path), size,
                )
            except Exception as exc:
                raise FontUnavailableError(
                    "无法加载中文字体，请检查宿主字体环境。",
                ) from exc
        return self._font_cache[size]

    def _check_cancelled(self) -> None:
        event = self._cancel_event
        checker = getattr(event, "is_set", None)
        if checker is not None and checker():
            raise ImageRenderCancelled("图片渲染已取消。")

    @staticmethod
    def _validate_limits(limits: Any) -> None:
        if not isinstance(limits, ImageRenderLimits):
            raise ProfileImageError("图片渲染上限配置无效。")
        for name in (
            "width",
            "max_height",
            "max_image_bytes",
            "max_total_bytes",
            "max_sections",
        ):
            value = getattr(limits, name)
            if type(value) is not int or value <= 0:
                raise ProfileImageError("图片渲染上限配置无效。")
        if limits.width < 320:
            raise ProfileImageError("图片宽度过小，无法排版。")


def find_cjk_font() -> Path:
    env_path = os.environ.get("ASTRBOT_60SAPI_FONT")
    candidates = ((env_path,) if env_path else ()) + _FONT_CANDIDATES
    seen: set[str] = set()
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        if ImageFont is None:
            break
        try:
            font = ImageFont.truetype(str(path), 32)
        except Exception:
            continue
        if _font_supports_chinese(font):
            return path
    raise FontUnavailableError(
        "未找到包含有效中文覆盖的字体；请安装 Noto Sans CJK/WenQuanYi，"
        "或通过 ASTRBOT_60SAPI_FONT 指定字体文件。",
    )


def _font_supports_chinese(font: Any) -> bool:
    """用实际字形覆盖检查字体是否支持常见中文，而不只验证可加载。"""
    try:
        missing = bytes(font.getmask("U0010ffff"))
    except Exception:
        missing = b""
    seen: set[bytes] = set()
    for char in "中文测试新闻":
        try:
            glyph = bytes(font.getmask(char))
        except Exception:
            return False
        if not glyph or glyph == missing:
            return False
        seen.add(glyph)
    return len(seen) >= 2


def _format_fetched_time(value: Any) -> str:
    raw = _clean(value)
    if not raw:
        return "未知"
    text = raw.replace("T", " ")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_CHINA_TZ)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _format_content_date(value: Any, section: dict[str, Any]) -> str:
    raw = _clean(value)
    weekday = _weekday_text(section, raw)
    if not raw:
        return ""
    match = _DATE_RE.search(raw)
    if match is None:
        suffix = f"（{weekday}）" if weekday and weekday not in raw else ""
        return raw + suffix
    try:
        parsed = date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
    except ValueError:
        suffix = f"（{weekday}）" if weekday and weekday not in raw else ""
        return raw + suffix
    text = f"{parsed.year} 年 {parsed.month} 月 {parsed.day} 日"
    if weekday:
        text += f"（{weekday}）"
    return text


def _weekday_text(section: dict[str, Any], raw: str) -> str:
    labels = "一二三四五六日"
    for key in ("weekday", "content_weekday", "weekday_text"):
        value = section.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            if 1 <= value <= 7:
                return "星期" + labels[value - 1]
            continue
        text = _display_text(value)
        if text:
            match = _WEEKDAY_RE.search(text)
            return match.group(1) if match else text
    match = _WEEKDAY_RE.search(raw)
    return match.group(1) if match else ""


def _display_text(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    return _clean(_URL_RE.sub(" ", text))


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in value
    )
    return " ".join(text.split()).strip()


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _items(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("items")
    if not isinstance(raw, list):
        return []
    return [
        item for item in raw
        if isinstance(item, dict) and _display_text(item.get("title"))
    ]


def _section_label(section: dict[str, Any]) -> str:
    return (
        _display_text(section.get("label"))
        or _display_text(section.get("content_type"))
        or "栏目"
    )


def _section_failed(section: dict[str, Any]) -> bool:
    return _clean(section.get("status")).lower() in {"error", "failed"}


def _safe_error_code(section: dict[str, Any]) -> str:
    code = _clean(section.get("code")).lower()
    return code if _SAFE_ERROR_RE.fullmatch(code) else _ERROR_SECTION_FAILED


def _hot_text(item: dict[str, Any]) -> str:
    desc = _display_text(item.get("hot_value_desc"))
    if desc:
        return desc if "热度" in desc else f"热度：{desc}"
    value = item.get("hot_value")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return ""
    return f"热度：{value}"


def _content_char_count(
    section: dict[str, Any],
    items: list[dict[str, Any]],
) -> int:
    total = len(_display_text(section.get("label")))
    for item in items:
        total += len(_display_text(item.get("title")))
        total += len(_display_text(item.get("detail")))
        total += len(_display_text(item.get("hot_value_desc")))
    return total


__all__ = [
    "FontUnavailableError",
    "ImageLimitError",
    "ImageRenderCancelled",
    "ImageRenderLimits",
    "ProfileImageError",
    "ProfileImageRenderer",
    "SectionImage",
    "find_cjk_font",
]
