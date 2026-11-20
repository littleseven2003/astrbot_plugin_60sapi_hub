"""单站优先、缺栏时按栏目拼合多站点的纯逻辑测试。"""

from __future__ import annotations

import unittest

from core.site_assembly import SiteProbe, assemble
from core.site_requirements import Requirement


def section(content_type: str, count: int, *, status: str = "ok"):
    return {
        "content_type": content_type,
        "status": status,
        "items": [{"title": f"{content_type}-{index}"} for index in range(count)],
    }


class SiteAssemblyTest(unittest.TestCase):
    def test_single_site_wins_over_later_candidates(self) -> None:
        probes = [
            SiteProbe("a", "A", 1, (section("news", 1),), False),
            SiteProbe("c", "C", 3, (
                section("news", 2), section("hot", 2),
            ), True),
            SiteProbe("b", "B", 2, (section("news", 2), section("hot", 2)), False),
        ]
        assembly = assemble(
            probes,
            (Requirement("news", 2, "每日新闻"), Requirement("hot", 2, "知乎热榜")),
        )
        self.assertEqual(assembly.mode, "single")
        self.assertEqual(assembly.site_id, "c")
        self.assertEqual(assembly.sites, (("c", 3),))
        self.assertTrue(assembly.satisfied)

    def test_multi_uses_earliest_candidate_per_column(self) -> None:
        probes = [
            SiteProbe("a", "A", 1, (section("news", 2), section("hot", 0)), False),
            SiteProbe("b", "B", 2, (section("news", 1), section("hot", 3)), False),
            SiteProbe("c", "C", 3, (section("hot", 5),), False),
        ]
        assembly = assemble(
            probes,
            (Requirement("news", 2, "每日新闻"), Requirement("hot", 2, "知乎热榜")),
        )
        self.assertEqual(assembly.mode, "multi")
        self.assertTrue(assembly.satisfied)
        self.assertEqual(
            [(item["content_type"], item["site_id"]) for item in assembly.assignments],
            [("news", "a"), ("hot", "b")],
        )
        # 同一栏目整栏使用一个站点的完整列表，不跨站拼条数。
        hot = [item for item in assembly.sections if item["content_type"] == "hot"][0]
        self.assertEqual(len(hot["items"]), 3)
        self.assertEqual(assembly.sites, (("a", 1), ("b", 2)))

    def test_missing_column_reports_shortfall_without_dropping_others(self) -> None:
        probes = [
            SiteProbe("a", "A", 1, (section("news", 2), section("hot", 1)), False),
            SiteProbe("b", "B", 2, (section("news", 1),), False),
        ]
        assembly = assemble(
            probes,
            (Requirement("news", 2, "每日新闻"), Requirement("hot", 3, "知乎热榜")),
        )
        self.assertEqual(assembly.mode, "missing")
        self.assertFalse(assembly.satisfied)
        self.assertEqual(
            [item["content_type"] for item in assembly.sections], ["news"],
        )
        self.assertEqual(len(assembly.missing), 1)
        self.assertEqual(assembly.missing[0]["content_type"], "hot")
        self.assertEqual(assembly.missing[0]["available"], 1)

    def test_insufficient_items_do_not_satisfy_single_site(self) -> None:
        probes = [SiteProbe(
            "a", "A", 1,
            (section("news", 2), section("hot", 2)),
            False,
        )]
        assembly = assemble(
            probes,
            (Requirement("news", 3, "每日新闻"), Requirement("hot", 2, "知乎热榜")),
        )
        self.assertEqual(assembly.mode, "missing")
        self.assertFalse(assembly.satisfied)
        self.assertEqual(assembly.missing[0]["content_type"], "news")
        self.assertEqual(assembly.missing[0]["available"], 2)


if __name__ == "__main__":
    unittest.main()
