from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.delivery import DeliveryService
from core.content_plan import PlanItem
from core.plan_preview import PlanPreviewResult


def _data(marker: str, items: int) -> dict:
    return {
        "status": "complete",
        "outputs": {"text": f"{marker} 文本", "markdown": f"{marker} markdown"},
        "failed_sections": [],
        "sections": [
            {
                "content_type": "daily_news",
                "status": "ok",
                "items": [{"title": f"{marker}{i}"} for i in range(items)],
            },
        ],
    }


def _ok(data: dict) -> PlanPreviewResult:
    return PlanPreviewResult(status_code=200, body={"status": "ok", "data": data})


class _FakeApi:
    def __init__(self, by_site: dict[str, dict]) -> None:
        self.by_site = by_site
        self.calls: list[str] = []

    async def preview_site(self, payload, *, owner=None, site_id=None):
        self.calls.append(site_id)
        data = self.by_site.get(site_id)
        if data is None:
            return PlanPreviewResult(
                status_code=502, body={"status": "error", "data": {"code": "x"}},
            )
        return _ok(data)


class _FakeSites:
    def __init__(self, sites: dict[str, object]) -> None:
        self.sites = sites

    async def get_site(self, site_id: str):
        try:
            return self.sites[site_id]
        except KeyError:
            from core.sites import SiteNotFoundError

            raise SiteNotFoundError(site_id) from None


class DeliveryFailoverTest(unittest.IsolatedAsyncioTestCase):
    """执行期按顺序寻找满足全部要求的单个站点。"""

    def _service(self, api, sites) -> DeliveryService:
        service = object.__new__(DeliveryService)
        service._api = api
        service._sites = sites
        return service

    def _task(self, sites: tuple[str, ...], limit: int):
        # 栏目方案是任务自身的一部分：条数直接取自任务 items。
        return SimpleNamespace(
            id="p1", output_format="text", sites=sites,
            items=(PlanItem("daily_news", limit),),
        )

    async def test_uses_first_site_that_satisfies_everything(self) -> None:
        api = _FakeApi({
            "site-a": _data("A", 1),
            "site-b": _data("B", 5),
        })
        sites = _FakeSites({
            "site-a": SimpleNamespace(id="site-a", enabled=True),
            "site-b": SimpleNamespace(id="site-b", enabled=True),
        })
        service = self._service(api, sites)
        prepared = await service._prepare_with_failover(
            "exec-1", self._task(("site-a", "site-b"), 2),
            [sites.sites["site-a"], sites.sites["site-b"]],
        )
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.slots[0][1], "B 文本")
        # 与组合绑定站点相同的候选不传覆盖参数（等价调用）。
        self.assertEqual(api.calls, ["site-a", "site-b"])

    async def test_no_content_from_other_sites_is_mixed(self) -> None:
        api = _FakeApi({
            "site-a": _data("A", 1),
            "site-b": _data("B", 5),
        })
        sites = _FakeSites({
            "site-a": SimpleNamespace(id="site-a", enabled=True),
            "site-b": SimpleNamespace(id="site-b", enabled=True),
        })
        service = self._service(api, sites)
        prepared = await service._prepare_with_failover(
            "exec-2", self._task(("site-a", "site-b"), 2),
            [sites.sites["site-a"], sites.sites["site-b"]],
        )
        body = prepared.slots[0][1]
        self.assertIn("B", body)
        self.assertNotIn("A", body)

    async def test_all_sites_failing_yields_safe_alert(self) -> None:
        api = _FakeApi({"site-a": _data("A", 1)})
        sites = _FakeSites({
            "site-a": SimpleNamespace(id="site-a", enabled=True),
            "site-b": SimpleNamespace(id="site-b", enabled=True),
        })
        service = self._service(api, sites)
        prepared = await service._prepare(
            "exec-3", self._task(("site-a", "site-b"), 3),
            [sites.sites["site-a"], sites.sites["site-b"]],
        )
        text = prepared.slots[0][1]
        self.assertTrue(text.startswith("60sAPI推送服务异常："), text)
        self.assertEqual(prepared.content_status, "failed")
        # 提示不泄露内部细节。
        for secret in ("site-a", "site-b", "http", "traceback"):
            self.assertNotIn(secret, text)

    async def test_candidates_keep_order_and_skip_disabled(self) -> None:
        api = _FakeApi({})
        sites = _FakeSites({
            "site-a": SimpleNamespace(id="site-a", enabled=False),
            "site-b": SimpleNamespace(id="site-b", enabled=True),
            "site-c": SimpleNamespace(id="site-c", enabled=True),
        })
        service = self._service(api, sites)
        resolved = await service._candidate_sites(
            self._task(("site-a", "site-b", "missing", "site-c"), 1),
        )
        # 完整候选按顺序返回（含停用），由调用方在“选择内容”时跳过停用项。
        self.assertEqual(
            [item.id for item in resolved], ["site-a", "site-b", "site-c"],
        )


if __name__ == "__main__":
    unittest.main()
