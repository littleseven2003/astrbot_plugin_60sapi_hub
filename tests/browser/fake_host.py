"""Start an isolated AstrBot with a fake aiocqhttp platform for browser tests.

Usage:
  ASTRBOT_TEST_ROOT=/path/to/isolated/root \
  ASTRBOT_TEST_PORT=16194 \
  python tests/browser/fake_host.py

The root must already contain this plugin in data/plugins and a dashboard
password. The fake bot never connects to a real platform. No credentials are
stored here.
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.environ.get("ASTRBOT_TEST_ROOT")
PORT = os.environ.get("ASTRBOT_TEST_PORT", "16194")
if not ROOT:
    raise SystemExit("ASTRBOT_TEST_ROOT is required")
os.environ["ASTRBOT_ROOT"] = ROOT
os.environ["DASHBOARD_PORT"] = PORT
sys.path.insert(0, ROOT)


class FakeBot:
    async def send_group_msg(self, **kwargs):
        return {"status": "ok", "data": {"message_id": 1}}

    async def send_private_msg(self, **kwargs):
        return {"status": "ok", "data": {"message_id": 1}}


async def main() -> None:
    from astrbot.core import LogBroker, db_helper
    from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
        AiocqhttpAdapter,
    )
    from astrbot.dashboard.server import AstrBotDashboard

    core = AstrBotCoreLifecycle(LogBroker(), db_helper)
    await core.initialize()
    adapter = AiocqhttpAdapter(
        {
            "id": "fake_qq_01",
            "ws_reverse_host": "127.0.0.1",
            "ws_reverse_port": 0,
            "ws_reverse_token": "",
        },
        {},
        asyncio.Queue(),
    )
    adapter.bot = FakeBot()
    core.platform_manager.platform_insts.append(adapter)
    dashboard = AstrBotDashboard(
        core, db_helper, core.dashboard_shutdown_event, None,
    )
    await asyncio.gather(core.start(), dashboard.run())


if __name__ == "__main__":
    asyncio.run(main())
