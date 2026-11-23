"""图片样式定义的数据模型。

一套样式只描述「看起来怎样」：配色、字号层级、边框与装饰参数。排版规则
（换行、基线、内容顺序、来源脱敏、资源限制）仍由渲染器统一实现，样式之间
不复制布局代码。
"""

from __future__ import annotations

from dataclasses import dataclass

RGB = tuple[int, int, int]

NUMBER_SHAPES = ("plain", "square")
FRAME_STYLES = ("card", "square")


@dataclass(frozen=True, slots=True)
class ImageStyle:
    """一套不可变的图片样式；所有色值与尺寸都在这里显式给出。"""

    id: str
    name: str
    description: str

    # 底色与文字
    page_bg: RGB
    card_bg: RGB
    header_bg: RGB | None
    ink: RGB
    title_ink: RGB
    muted: RGB
    num: RGB
    heat: RGB
    divider: RGB
    border: RGB
    accent: RGB
    brand_ink: RGB
    brand_secondary_ink: RGB
    footer_bg: RGB | None
    footer_ink: RGB
    footer_brand_ink: RGB
    footer_muted_ink: RGB

    # 字号与行高
    brand_size: int
    label_size: int
    label_leading: int
    title_size: int
    title_leading: int
    num_size: int
    num_col: int
    num_gap: int
    detail_size: int
    detail_leading: int
    heat_size: int
    footer_size: int
    footer_leading: int

    # 边框、留白与装饰
    card_radius: int
    card_border: int
    pad_x: int
    accent_bar_w: int
    accent_bar_h: int
    number_shape: str = "plain"
    number_bg: RGB | None = None
    frame_style: str = "card"

    def __post_init__(self) -> None:
        if self.number_shape not in NUMBER_SHAPES:
            raise ValueError(f"未知序号形状：{self.number_shape}")
        if self.frame_style not in FRAME_STYLES:
            raise ValueError(f"未知外框样式：{self.frame_style}")
