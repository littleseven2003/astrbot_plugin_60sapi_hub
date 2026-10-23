"""60s API Hub 的 AstrBot 插件入口。

当前负责宿主装配和站点存储的生命周期；后台页面、内容获取与推送尚未实现。
"""

from astrbot.api.star import Context, Star, StarTools

from .core.sites import SiteService

PLUGIN_NAME = "astrbot_plugin_60sapi_hub"
DATABASE_FILENAME = "sites.sqlite3"


class SixtySAPIHub(Star):
    """接入 60s API 的资讯聚合与订阅插件。"""

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        plugin_name = getattr(self, "name", "") or PLUGIN_NAME
        data_dir = StarTools.get_data_dir(plugin_name)
        self.sites = SiteService(data_dir / DATABASE_FILENAME)

    async def initialize(self) -> None:
        await self.sites.open()

    async def terminate(self) -> None:
        await self.sites.close()
