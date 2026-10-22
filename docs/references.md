# 参考资料

核验日期：2026-10-22。已核对文档不等于已完成运行兼容验证；实现时优先检查所选版本源码。

| 主题 | 官方来源 |
|---|---|
| AstrBot 插件开发 | https://docs.astrbot.app/dev/star/plugin-new.html |
| 配置表单 | https://docs.astrbot.app/dev/star/guides/plugin-config.html |
| 插件后台视图 | https://docs.astrbot.app/dev/star/guides/plugin-pages.html |
| 消息发送 | https://docs.astrbot.app/dev/star/guides/send-message.html |
| 文转图 | https://docs.astrbot.app/dev/star/guides/html-to-pic.html |
| 插件存储 | https://docs.astrbot.app/dev/star/guides/storage.html |
| /sid 与 UMO | https://docs.astrbot.app/use/command.html |
| 60s API 源码 | https://github.com/vikiboss/60s |
| 60s API 文档 | https://docs.60s-api.viki.moe/ |
| 上游 MIT 许可证 | https://github.com/vikiboss/60s/blob/main/license |

## 首批接口核验入口

| 内容 | 文档 | 当前已核对信息 |
|---|---|---|
| 每日新闻 | https://docs.60s-api.viki.moe/254026209e0 | /v2/60s；存在内容日期和新闻列表 |
| B站热搜 | https://docs.60s-api.viki.moe/254039323e0 | /v2/bili；热搜词与链接 |
| 知乎话题榜 | https://docs.60s-api.viki.moe/254026504e0 | /v2/zhihu；完整结构待实例核验 |
| 抖音热搜 | https://docs.60s-api.viki.moe/254028799e0 | 栏目已确认，路径和变体实施时核验 |
| 头条热搜 | https://docs.60s-api.viki.moe/254038833e0 | 栏目已确认，路径和变体实施时核验 |

不从上游文档中的示例推断所有实例版本一致，也不把 B站热搜当热门视频榜。引入源码、图标或模板时分别核对许可证与署名要求。
