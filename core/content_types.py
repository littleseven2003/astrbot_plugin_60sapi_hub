"""内容类型及其展示名称的唯一来源。"""

from __future__ import annotations

DAILY_NEWS = "daily_news"
BILIBILI_HOT = "bilibili_hot"
ZHIHU_HOT = "zhihu_hot"
DOUYIN_HOT = "douyin_hot"
TOUTIAO_HOT = "toutiao_hot"

SUPPORTED_CONTENT_TYPES = (
    DAILY_NEWS,
    BILIBILI_HOT,
    ZHIHU_HOT,
    DOUYIN_HOT,
    TOUTIAO_HOT,
)
KNOWN_CONTENT_TYPES = frozenset(SUPPORTED_CONTENT_TYPES)
CONTENT_LABELS = {
    DAILY_NEWS: "每日新闻",
    BILIBILI_HOT: "Bilibili 热搜",
    ZHIHU_HOT: "知乎热榜",
    DOUYIN_HOT: "抖音热搜",
    TOUTIAO_HOT: "今日头条热榜",
}

__all__ = [
    "BILIBILI_HOT",
    "CONTENT_LABELS",
    "DAILY_NEWS",
    "DOUYIN_HOT",
    "KNOWN_CONTENT_TYPES",
    "SUPPORTED_CONTENT_TYPES",
    "TOUTIAO_HOT",
    "ZHIHU_HOT",
]
