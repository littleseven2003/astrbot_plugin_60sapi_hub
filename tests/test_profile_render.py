from __future__ import annotations

import copy
import unittest

from core.profile_render import (
    render_profile_markdown,
    render_profile_outputs,
    render_profile_text,
)


def data() -> dict:
    return {
        "profile_id": "profile-1",
        "profile_name": "组合 # [名称](javascript:alert(1)) `code`\n## 注入",
        "profile_revision": 3,
        "site_id": "site-1",
        "site_name": "站点 <b>名称</b>\n第二行",
        "status": "partial",
        "success_count": 2,
        "failure_count": 1,
        "sections": [
            {
                "content_type": "daily_news",
                "label": "每日新闻",
                "status": "ok",
                "endpoint": "/v2/60s",
                "fetched_at": "2026-10-26T02:00:00+00:00",
                "content_date": "2026-10-26",
                "displayed_count": 1,
                "total_item_count": 2,
                "limit": 1,
                "warnings": ["内容日期未知提示"],
                "items": [
                    {
                        "title": "# [标题](javascript:alert(1)) <img src=x> `x`\n## 伪造",
                        "detail": "摘要 *粗体* [链接](https://evil.test)",
                        "link": "https://example.test/a_(b) c",
                    },
                ],
            },
            {
                "content_type": "zhihu_hot",
                "label": "知乎热榜",
                "status": "ok",
                "fetched_at": "2026-10-26T02:01:00+00:00",
                "displayed_count": 2,
                "total_item_count": 2,
                "limit": None,
                "items": [
                    {
                        "title": "知乎零值",
                        "hot_value_desc": "1670 万热度",
                    },
                    {
                        "title": "知乎零值二",
                        "hot_value_desc": "0 热度",
                    },
                ],
            },
            {
                "content_type": "douyin_hot",
                "label": "抖音热搜",
                "status": "error",
                "message": "上游站点返回 HTTP 500。",
                "code": "upstream_http_error",
            },
            {
                "content_type": "bilibili_hot",
                "label": "Bilibili 热搜",
                "status": "ok",
                "fetched_at": "2026-10-26T02:02:00+00:00",
                "displayed_count": 0,
                "total_item_count": 0,
                "limit": None,
                "items": [],
            },
            {
                "content_type": "douyin_hot",
                "label": "抖音热搜",
                "status": "ok",
                "fetched_at": "2026-10-26T02:03:00+00:00",
                "displayed_count": 1,
                "total_item_count": 1,
                "limit": 10,
                "items": [
                    {"title": "零热度真实值", "hot_value": 0},
                    {"title": "正热度", "hot_value": 42},
                ],
            },
        ],
    }


class ProfileRenderTest(unittest.TestCase):
    def test_outputs_are_deterministic_and_do_not_mutate_input(self) -> None:
        payload = data()
        before = copy.deepcopy(payload)
        first = render_profile_outputs(payload)
        second = render_profile_outputs(payload)
        self.assertEqual(first, second)
        self.assertEqual(payload, before)
        self.assertEqual(set(first), {"text", "markdown"})
        self.assertEqual(first["text"], render_profile_text(payload))
        self.assertEqual(first["markdown"], render_profile_markdown(payload))

    def test_text_contains_order_limits_dates_warnings_and_heat(self) -> None:
        text = render_profile_text(data())
        self.assertIn("组合：组合 # [名称]", text)
        self.assertIn("来源站点：站点", text)
        self.assertIn("状态：部分成功（2 成功 / 1 失败）", text)
        self.assertLess(text.index("【每日新闻】"), text.index("【知乎热榜】"))
        self.assertIn("获取时间：2026-10-26T02:00:00+00:00", text)
        self.assertIn("展示条数：1 / 2（最多 1 条）", text)
        self.assertIn("内容日期：2026-10-26", text)
        self.assertIn("提示：内容日期未知提示", text)
        self.assertIn("热度：0 热度", text)
        self.assertIn("热度：1670 万热度", text)
        self.assertIn("热度：0", text)
        self.assertIn("热度：42", text)
        self.assertIn("获取失败：上游站点返回 HTTP 500。（upstream_http_error）", text)
        self.assertIn("（空列表，0 条）", text)
        self.assertNotIn("<img", text)
        self.assertNotIn("<b>", text)

    def test_markdown_escapes_untrusted_structure_and_safe_links(self) -> None:
        markdown = render_profile_markdown(data())
        self.assertTrue(markdown.startswith("# "))
        self.assertIn("\\#", markdown)
        self.assertIn("\\[名称\\]", markdown)
        self.assertIn("\\`code\\`", markdown)
        self.assertNotIn("\n## 注入", markdown)
        self.assertNotIn("<img", markdown)
        self.assertNotIn("<b>", markdown)
        self.assertNotIn("](javascript:", markdown)
        self.assertIn("https://example.test/a_%28b%29%20c", markdown)
        self.assertIn("[链接](<https://example.test/a_%28b%29%20c>)", markdown)
        self.assertIn("## 每日新闻", markdown)
        self.assertIn("## 知乎热榜", markdown)
        self.assertIn("## 抖音热搜", markdown)
        self.assertIn("## Bilibili 热搜", markdown)
        self.assertIn("（空列表，0 条）", markdown)
        self.assertIn("1. \\# \\[标题\\]", markdown)
        self.assertNotIn("1. # [标题]", markdown)
        self.assertIn("热度：0", markdown)
        self.assertIn("热度：42", markdown)

    def test_unknown_or_stale_daily_warning_is_rendered(self) -> None:
        payload = {
            "profile_name": "组合",
            "site_name": "站点",
            "status": "complete",
            "success_count": 1,
            "failure_count": 0,
            "sections": [
                {
                    "content_type": "daily_news",
                    "label": "每日新闻",
                    "status": "ok",
                    "fetched_at": "2026-10-26T02:00:00+00:00",
                    "date_is_unknown": True,
                    "displayed_count": 0,
                    "total_item_count": 0,
                    "items": [],
                },
            ],
        }
        text = render_profile_text(payload)
        markdown = render_profile_markdown(payload)
        self.assertIn("日期保持未知", text)
        self.assertIn("日期保持未知", markdown)
        payload["sections"][0].pop("date_is_unknown")
        payload["sections"][0]["is_stale"] = True
        self.assertIn("早于当前日期", render_profile_text(payload))
