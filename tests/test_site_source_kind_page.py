from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "browser" / "site_source_kind_ui.js"

NODE_PROBE = "process.stdout.write(require.resolve('playwright-core'))"
CHROME_PROBE = (
    "process.stdout.write("
    "require('playwright-core').chromium.executablePath())"
)

MAC_APP = Path("Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")

def _cache_root(env: dict) -> Path:
    """Playwright 的浏览器缓存根目录；允许用环境变量覆盖。"""
    configured = env.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"

def _find_installed_chromium(root: Path) -> Path | None:
    """在缓存根目录里找任意已安装的 Chromium，不写死版本号。"""
    if not root.is_dir():
        return None
    found: list[tuple[float, Path]] = []
    for entry in root.glob("chromium-*"):
        for flavour in (
            "chrome-mac-arm64", "chrome-mac", "chrome-linux", "chrome-win",
        ):
            base = entry / flavour
            if not base.is_dir():
                continue
            for candidate in (
                base / MAC_APP, base / "chrome", base / "chrome.exe",
            ):
                if candidate.exists():
                    found.append((entry.stat().st_mtime, candidate))
    if not found:
        return None
    return max(found)[1]

def _run_node(node: str, code: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [node, "-e", code],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

def _resolve_chrome(node: str, env: dict) -> tuple[Path | None, str]:
    """返回 (浏览器路径, 失败原因)。显式变量优先，其次实际存在与缓存扫描。"""
    for name in ("ASTRBOT_TEST_CHROME_PATH", "CHROME_PATH"):
        configured = env.get(name)
        if configured:
            path = Path(configured)
            if path.exists():
                return path, ""
            return None, f"{name} 指向的文件不存在：{configured}"
    probed = _run_node(node, CHROME_PROBE, env)
    if probed.returncode == 0 and probed.stdout.strip():
        declared = Path(probed.stdout.strip())
        if declared.exists():
            return declared, ""
        discovered = _find_installed_chromium(_cache_root(env))
        if discovered is not None:
            return discovered, ""
        return None, (
            "Chromium 未安装：playwright-core 期望 "
            f"{declared}，可设置 ASTRBOT_TEST_CHROME_PATH",
        )
    discovered = _find_installed_chromium(_cache_root(env))
    if discovered is not None:
        return discovered, ""
    return None, "无法确定 Chromium 可执行文件，请设置 ASTRBOT_TEST_CHROME_PATH"

def probe_environment(env: dict | None = None) -> tuple[bool, str, dict]:
    """检查 node、playwright-core 与 Chromium 是否真的可用。

    返回 (ready, reason, prepared_env)。不可用时 reason 说明具体缺什么；
    可用时 prepared_env 已带好 CHROME_PATH。这里不写死任何开发者本机路径：
    浏览器用 ASTRBOT_TEST_CHROME_PATH / CHROME_PATH 指定，或由 playwright-core
    解析、再回退到其缓存目录里实际安装的 Chromium；模块解析可用 NODE_PATH。
    """
    prepared = dict(os.environ if env is None else env)
    node = shutil.which("node")
    if not node:
        return False, "缺少 node 可执行文件", prepared
    if not SCRIPT.exists():
        return False, f"缺少页面测试脚本 {SCRIPT.name}", prepared

    resolved = _run_node(node, NODE_PROBE, prepared)
    if resolved.returncode != 0:
        return (
            False,
            "无法解析 playwright-core（可设置 NODE_PATH 指向其 node_modules）",
            prepared,
        )

    chrome, reason = _resolve_chrome(node, prepared)
    if chrome is None:
        return False, reason, prepared
    prepared["CHROME_PATH"] = str(chrome)
    return True, "", prepared

class ProbeEnvironmentTest(unittest.TestCase):
    """依赖探测本身：缺什么就报什么，不把真实错误吞成跳过。"""

    def test_missing_node_is_reported(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            ready, reason, _ = probe_environment()
        self.assertFalse(ready)
        self.assertIn("node", reason)

    def test_node_path_without_module_is_reported(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("本机没有 node，无法验证模块解析分支")
        baseline_env = dict(os.environ)
        baseline_env.pop("NODE_PATH", None)
        if probe_environment(baseline_env)[0]:
            self.skipTest("本机 playwright-core 可从默认路径解析")
        with tempfile.TemporaryDirectory() as empty:
            env = dict(os.environ)
            env["NODE_PATH"] = empty
            ready, reason, _ = probe_environment(env)
        self.assertFalse(ready)
        self.assertIn("playwright-core", reason)

    def test_explicit_chrome_path_is_honoured(self) -> None:
        with tempfile.NamedTemporaryFile() as handle:
            fake = Path(handle.name)
            with mock.patch.dict(os.environ, {"ASTRBOT_TEST_CHROME_PATH": str(fake)}):
                resolved, reason = _resolve_chrome("node", dict(os.environ))
            self.assertEqual(resolved, fake)
            self.assertEqual(reason, "")

    def test_missing_explicit_chrome_path_is_reported(self) -> None:
        with mock.patch.dict(
            os.environ, {"ASTRBOT_TEST_CHROME_PATH": "/nonexistent/chrome"},
        ):
            resolved, reason = _resolve_chrome("node", dict(os.environ))
        self.assertIsNone(resolved)
        self.assertIn("ASTRBOT_TEST_CHROME_PATH", reason)

    def test_cache_scan_finds_installed_build_without_version_pin(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            build = root / "chromium-9999" / "chrome-mac-arm64"
            exe = build / MAC_APP
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
            self.assertEqual(_find_installed_chromium(root), exe)
            self.assertIsNone(_find_installed_chromium(root / "missing"))

class SiteSourceKindPageTest(unittest.TestCase):
    """在真实 Chromium 中执行站点页面的来源类型逻辑。

    这是桩 bridge 的页面逻辑验证：宿主 bridge 由测试脚本替换，因此它验证的是
    页面表单、展示与提交载荷，不验证真实 AstrBot 后台或数据库往返（例如未知
    来源类型能否真的被后端保存）。缺少 node / playwright-core / Chromium 时按
    探测到的具体原因跳过；依赖齐全后脚本失败必须判定为失败。
    """

    def _assert_page_passes(self, env: dict) -> None:
        completed = subprocess.run(
            [shutil.which("node") or "node", str(SCRIPT)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("SITE SOURCE KIND UI PASS", completed.stdout, output)

    def test_page_logic_covers_source_kind_states(self) -> None:
        ready, reason, env = probe_environment()
        if not ready:
            self.skipTest(reason)
        self._assert_page_passes(env)

    def test_script_failure_is_not_downgraded_to_skip(self) -> None:
        failed = subprocess.CompletedProcess([], 1, "", "页面断言失败")
        with mock.patch.object(subprocess, "run", return_value=failed):
            with self.assertRaises(AssertionError):
                self._assert_page_passes({})

if __name__ == "__main__":
    unittest.main()
