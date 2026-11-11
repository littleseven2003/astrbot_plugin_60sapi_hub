from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "views" / "sites" / "index.html"
APP_JS = ROOT / "views" / "sites" / "app.js"


class SiteFormDefaultsTest(unittest.TestCase):
    """后台站点表单的默认来源类型为公开。

    这是静态检查：本仓库测试环境没有可用的 AstrBot WebUI 构建产物，
    真实浏览器验收仍需在具备环境的机器上执行。
    """

    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")
        self.js = APP_JS.read_text(encoding="utf-8")

    def _options(self) -> list[str]:
        match = re.search(
            r'<select id="source-kind".*?</select>', self.html, re.S,
        )
        self.assertIsNotNone(match, "站点表单缺少来源类型下拉框")
        return re.findall(r"<option[^>]*>", match.group(0))

    def test_public_option_is_first_and_selected(self) -> None:
        options = self._options()
        self.assertGreaterEqual(len(options), 2)
        self.assertIn('value="public"', options[0])
        self.assertIn("selected", options[0])
        self.assertIn('value="self_built"', options[1])
        self.assertNotIn("selected", options[1])

    def test_new_site_form_resets_to_public(self) -> None:
        self.assertIn('elements.sourceKind.value = "public";', self.js)
        self.assertNotIn('elements.sourceKind.value = "self_built";', self.js)

    def test_unknown_kind_falls_back_to_public(self) -> None:
        # 读到缺失或未知的来源类型时按公开处理，与新建默认值一致。
        self.assertIn(
            'raw.source_kind === "self_built" ? "self_built" : "public"',
            self.js,
        )


if __name__ == "__main__":
    unittest.main()
