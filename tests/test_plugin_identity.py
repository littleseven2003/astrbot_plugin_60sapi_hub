"""插件身份与侧边栏入口元数据：显示名、本地化视图标题与页面入口。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISPLAY_NAME = "60sAPI Hub"
PAGE_NAME = "sites"


def read_metadata_keys(path: Path) -> dict[str, str]:
    """只读取 metadata.yaml 的顶层 ``key: value``，避免测试依赖 PyYAML。

    手册要求安装的运行时依赖里没有 PyYAML；身份测试必须能在该环境直接运行。
    """
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        values[key.strip()] = value.strip().strip("\"'")
    return values


class PluginIdentityTest(unittest.TestCase):
    def test_metadata_declares_display_name(self) -> None:
        metadata = read_metadata_keys(ROOT / "metadata.yaml")
        self.assertEqual(metadata["name"], "astrbot_plugin_60sapi_hub")
        self.assertEqual(metadata["version"], "v0.1.0-dev.1")
        self.assertEqual(metadata["display_name"], DISPLAY_NAME)

    def test_side_bar_page_entry_exists(self) -> None:
        # AstrBot 在 views/ 或 pages/ 下按目录发现插件页面，入口必须是 index.html。
        entry = ROOT / "views" / PAGE_NAME / "index.html"
        self.assertTrue(entry.is_file(), f"缺少插件页面入口：{entry}")

    def test_locales_use_the_same_sidebar_title(self) -> None:
        for locale in ("zh-CN", "en-US"):
            path = ROOT / ".astrbot-plugin" / "i18n" / f"{locale}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                data["metadata"]["display_name"], DISPLAY_NAME, locale,
            )
            self.assertEqual(
                data["views"][PAGE_NAME]["title"], DISPLAY_NAME, locale,
            )
            self.assertTrue(data["views"][PAGE_NAME]["description"].strip(), locale)


if __name__ == "__main__":
    unittest.main()
