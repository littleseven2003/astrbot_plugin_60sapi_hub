"""60s API Hub 的 AstrBot 插件入口。

当前仅提供可被宿主发现和加载的最小插件类，站点配置、内容获取与推送功能尚未实现。
"""

from astrbot.api.star import Star


class SixtySAPIHub(Star):
    """接入 60s API 的资讯聚合与订阅插件。"""
