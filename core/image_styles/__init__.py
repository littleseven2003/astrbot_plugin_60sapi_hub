"""图片样式注册表：渲染器、接口校验与后台选项的唯一来源。"""

from __future__ import annotations

from .base import FRAME_STYLES, NUMBER_SHAPES, RGB, ImageStyle
from .presets import PRESETS, STYLE_ORDER, DEFAULT as DEFAULT_STYLE

DEFAULT_STYLE_ID = DEFAULT_STYLE.id


def is_known_style(style_id: object) -> bool:
    return isinstance(style_id, str) and style_id in PRESETS


def parse_style_id(value: object) -> str:
    """严格解析任务提交的样式 ID；未知值由调用方转成字段级错误。"""
    if not isinstance(value, str):
        raise ValueError("image_style")
    normalized = value.strip()
    if normalized not in PRESETS:
        raise ValueError("image_style")
    return normalized


def resolve_style_id(value: object) -> str:
    """宽松解析：读取历史数据或快照时未知值安全回退默认样式。"""
    if isinstance(value, str) and value in PRESETS:
        return value
    return DEFAULT_STYLE_ID


def get_style(value: object = None) -> ImageStyle:
    """按 ID / 样式对象 / 空值返回样式；未知值回退默认。"""
    if isinstance(value, ImageStyle):
        return value
    return PRESETS[resolve_style_id(value)]


def _hex(color: tuple[int, int, int] | None) -> str | None:
    if color is None:
        return None
    return "#{:02X}{:02X}{:02X}".format(*color)


def _preview_tokens(style: ImageStyle) -> dict[str, object]:
    """下拉旁示例卡的配色与形状；新增样式自动获得示例。"""
    return {
        "page_bg": _hex(style.page_bg),
        "card_bg": _hex(style.card_bg),
        "header_bg": _hex(style.header_bg or style.card_bg),
        "ink": _hex(style.ink),
        "title_ink": _hex(style.title_ink),
        "muted": _hex(style.muted),
        "num": _hex(style.num),
        "heat": _hex(style.heat),
        "divider": _hex(style.divider),
        "border": _hex(style.border),
        "accent": _hex(style.accent),
        "brand_ink": _hex(style.brand_ink),
        "number_shape": style.number_shape,
        "number_bg": _hex(style.number_bg),
        "frame_style": style.frame_style,
        "card_radius": style.card_radius,
    }


def style_options() -> tuple[dict[str, object], ...]:
    """后台下拉与任务列表使用的稳定选项；顺序固定，不随字典顺序变化。"""
    return tuple(
        {
            "id": PRESETS[style_id].id,
            "name": PRESETS[style_id].name,
            "description": PRESETS[style_id].description,
            "preview": _preview_tokens(PRESETS[style_id]),
        }
        for style_id in STYLE_ORDER
        if style_id in PRESETS
    )


__all__ = [
    "DEFAULT_STYLE",
    "DEFAULT_STYLE_ID",
    "FRAME_STYLES",
    "ImageStyle",
    "NUMBER_SHAPES",
    "PRESETS",
    "RGB",
    "STYLE_ORDER",
    "get_style",
    "is_known_style",
    "parse_style_id",
    "resolve_style_id",
    "style_options",
]
