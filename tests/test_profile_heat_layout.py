"""热榜热度排版：整栏统一布局，避免位数差一位导致行高跳变。"""

from __future__ import annotations

import unittest

from core.profile_image import FontUnavailableError, ProfileImageRenderer, find_cjk_font


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


@unittest.skipUnless(has_font(), "no CJK font available")
class HeatLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = ProfileImageRenderer()

    def test_heat_layout_is_uniform_for_mixed_digit_counts(self) -> None:
        # 7 位与 8 位热度混排：整栏统一用“热度单独一行”，不出现行高跳变。
        items = [
            {"title": f"标题{index}", "hot_value": 9_999_999 if index <= 10 else 999_999}
            for index in range(1, 21)
        ]
        lines = self.renderer._body_lines(items, self.renderer.limits.width)
        title_lines = [line for line in lines if any(part.number for part in line.parts)]
        heat_lines = [
            line for line in lines
            if line.parts and all(part.text.startswith("热度：") for part in line.parts)
        ]
        self.assertEqual(len(title_lines), 20)
        self.assertEqual(len(heat_lines), 20)
        # 每一条标题行都不能再挤进热度：否则位数不同会改变行高。
        for line in title_lines:
            self.assertFalse(any(part.text.startswith("热度：") for part in line.parts))

    def test_short_heat_keeps_inline_column(self) -> None:
        items = [
            {"title": f"标题{index}", "hot_value": 10 + index}
            for index in range(1, 6)
        ]
        lines = self.renderer._body_lines(items, self.renderer.limits.width)
        title_lines = [line for line in lines if any(part.number for part in line.parts)]
        self.assertEqual(len(title_lines), 5)
        for line in title_lines:
            self.assertTrue(any(part.right is not None for part in line.parts))


if __name__ == "__main__":
    unittest.main()
