from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "browser" / "site_source_kind_ui.js"

DEFAULT_CHROME = (
    Path.home()
    / "Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64"
    / "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)
DEFAULT_NODE_PATH = Path("/Users/littleseven/.npm/_npx/0b9ff77863cb6e9f/node_modules")


def _chrome() -> str | None:
    configured = os.environ.get("CHROME_PATH")
    if configured and Path(configured).exists():
        return configured
    if DEFAULT_CHROME.exists():
        return str(DEFAULT_CHROME)
    return None


def _node_modules() -> str | None:
    configured = os.environ.get("NODE_PATH")
    if configured and Path(configured).is_dir():
        return configured
    if DEFAULT_NODE_PATH.is_dir():
        return str(DEFAULT_NODE_PATH)
    return None


CHROME = _chrome()
NODE = shutil.which("node")
NODE_PATH = _node_modules()
READY = bool(CHROME and NODE and NODE_PATH and SCRIPT.exists())


class SiteSourceKindPageTest(unittest.TestCase):
    """在真实 Chromium 中执行页面逻辑验证来源类型默认值与未知处理。

    这是浏览器验收脚本的无头包装；缺少 node、playwright 或 Chromium 时跳过，
    并把原因写清楚，不用源码字符串匹配冒充页面验证。
    """

    @unittest.skipUnless(
        READY, "缺少 node / playwright-core / Chromium，未执行页面逻辑验收",
    )
    def test_page_logic_covers_source_kind_states(self) -> None:
        environment = dict(os.environ)
        environment["CHROME_PATH"] = CHROME or ""
        environment["NODE_PATH"] = NODE_PATH or ""
        completed = subprocess.run(
            [NODE, str(SCRIPT)],
            cwd=str(ROOT),
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("SITE SOURCE KIND UI PASS", completed.stdout, output)


if __name__ == "__main__":
    unittest.main()
