"""60s API Hub 的 AstrBot 插件入口。

负责宿主装配、站点存储和后台站点 API 的生命周期；内容获取与推送尚未实现。
"""

from astrbot.api.star import Context, Star, StarTools

from .core.site_api import SiteApi
from .core.sites import SiteService
from .site_web import SiteWeb

PLUGIN_NAME = "astrbot_plugin_60sapi_hub"
DATABASE_FILENAME = "sites.sqlite3"


class SixtySAPIHub(Star):
    """接入 60s API 的资讯聚合与订阅插件。"""

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        plugin_name = getattr(self, "name", "") or PLUGIN_NAME
        data_dir = StarTools.get_data_dir(plugin_name)
        self.sites = SiteService(data_dir / DATABASE_FILENAME)
        self._active = False
        self._site_web = SiteWeb(
            SiteApi(self.sites),
            plugin_name=plugin_name,
            is_active=lambda: self._active,
        )
        self._site_web.register(context)

    async def initialize(self) -> None:
        await self.sites.open()
        self._active = True

    async def terminate(self) -> None:
        self._active = False
        await self.sites.close()
