"""60s API Hub 的 AstrBot 插件入口。

负责宿主装配、站点存储、每日新闻 / Bilibili 热搜获取和后台接口的生命周期；
渲染、发送与调度尚未实现。
"""

from astrbot.api.star import Context, Star, StarTools

from .content_web import ContentWeb
from .delivery_web import DeliveryWeb
from .core.content_api import ContentApi
from .core.content_service import ContentService
from .core.news_api import NewsApi
from .core.site_api import SiteApi
from .core.sites import SiteService
from .news_web import NewsWeb
from .profile_web import ProfileWeb
from .site_web import SiteWeb
from .task_web import TaskWeb
from .core.image_service import ProfileImageService
from .core.image_snapshots import ImageSnapshotStore
from .core.profile_api import ProfileApi
from .core.profiles import ContentProfileService
from .core.task_api import TaskApi
from .core.tasks import PushTaskService
from .core.astrbot_sender import AstrBotAiocqhttpSender
from .core.delivery import DeliveryLimits, DeliveryService
from .core.delivery_api import DeliveryApi

PLUGIN_NAME = "astrbot_plugin_60sapi_hub"
DATABASE_FILENAME = "sites.sqlite3"


class SixtySAPIHub(Star):
    """接入 60s API 的资讯聚合与订阅插件。"""

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        plugin_name = getattr(self, "name", "") or PLUGIN_NAME
        data_dir = StarTools.get_data_dir(plugin_name)
        self.sites = SiteService(data_dir / DATABASE_FILENAME)
        self.content = ContentService(self.sites)
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.image_snapshots = ImageSnapshotStore()
        self.image_service = ProfileImageService()
        self.profile_api = ProfileApi(
            self.profiles,
            self.content,
            self.sites,
            image_snapshots=self.image_snapshots,
            image_service=self.image_service,
        )
        self.delivery = DeliveryService(
            tasks=self.tasks,
            profiles=self.profiles,
            sites=self.sites,
            profile_api=self.profile_api,
            store=self.sites.store,
            sender=AstrBotAiocqhttpSender(context),
            limits=DeliveryLimits(),
        )
        self._active = False
        self._site_web = SiteWeb(
            SiteApi(self.sites),
            plugin_name=plugin_name,
            is_active=lambda: self._active,
        )
        self._news_web = NewsWeb(
            NewsApi(self.content.news),
            plugin_name=plugin_name,
            is_active=lambda: self._active,
        )
        self._content_web = ContentWeb(
            ContentApi(self.content),
            plugin_name=plugin_name,
            is_active=lambda: self._active,
        )
        self._task_web = TaskWeb(
            TaskApi(self.tasks, self.profiles, self.sites),
            plugin_name=plugin_name,
            is_active=lambda: self._active,
        )
        self._delivery_web = DeliveryWeb(
            DeliveryApi(
                delivery=self.delivery,
                tasks=self.tasks,
                profiles=self.profiles,
                sites=self.sites,
                store=self.sites.store,
                inspector=AstrBotAiocqhttpSender(context),
            ),
            plugin_name=plugin_name,
            is_active=lambda: self._active,
        )
        self._profile_web = ProfileWeb(
            self.profile_api,
            plugin_name=plugin_name,
            is_active=lambda: self._active,
        )
        self._site_web.register(context)
        self._news_web.register(context)
        self._content_web.register(context)
        self._task_web.register(context)
        self._delivery_web.register(context)
        self._profile_web.register(context)

    async def initialize(self) -> None:
        await self.sites.open()
        try:
            await self.content.open()
        except Exception:
            await self.delivery.close()
            await self.content.close()
            try:
                await self.image_service.close()
            finally:
                self.image_snapshots.close()
                await self.sites.close()
            raise
        self._active = True

    async def terminate(self) -> None:
        self._active = False
        try:
            await self.delivery.close()
        finally:
            try:
                await self.image_service.close()
            finally:
                self.image_snapshots.close()
                try:
                    await self.content.close()
                finally:
                    await self.sites.close()
