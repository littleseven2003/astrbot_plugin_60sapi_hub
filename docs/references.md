# 参考资料

核验日期：2026-10-23。AstrBot 源码基线为 v4.29.1；60s API 源码核验 commit 为 `d582aa65d44f76e91c434174c9cf44684d30755f`（package 2.54.0）。已核对文档不等于已完成运行兼容验证，实现时优先检查所选版本源码。

| 主题 | 官方来源 |
|---|---|
| AstrBot 源码基线 | https://github.com/AstrBotDevs/AstrBot/tree/v4.29.1 |
| AstrBot 插件开发 | https://docs.astrbot.app/dev/star/plugin-new.html |
| 配置表单 | https://docs.astrbot.app/dev/star/guides/plugin-config.html |
| 插件后台视图 | https://docs.astrbot.app/dev/star/guides/plugin-pages.html |
| 消息发送 | https://docs.astrbot.app/dev/star/guides/send-message.html |
| 文转图 | https://docs.astrbot.app/dev/star/guides/html-to-pic.html |
| 插件存储 | https://docs.astrbot.app/dev/star/guides/storage.html |
| /sid 与 UMO | https://docs.astrbot.app/use/command.html |
| 60s API 源码 | https://github.com/vikiboss/60s |
| 60s API 文档 | https://docs.60s-api.viki.moe/ |
| 60s API 每日新闻文档 | https://docs.60s-api.viki.moe/254026209e0 |
| 60s API 官方公开实例 | https://60s.viki.moe |
| 上游 MIT 许可证 | https://github.com/vikiboss/60s/blob/main/license |

## 首批接口核验入口

| 内容 | 文档 | 当前已核对信息 |
|---|---|---|
| 每日新闻 | https://docs.60s-api.viki.moe/254026209e0 | `/v2/60s`；GET；`encoding` 默认 json，可选 date；成功为 `{code,message,data}`，必要 `data.news`，`date` 可为空；条目为字符串或 `{title,link}` |
| B站热搜 | https://docs.60s-api.viki.moe/254039323e0 | /v2/bili；热搜词与链接 |
| 知乎话题榜 | https://docs.60s-api.viki.moe/254026504e0 | /v2/zhihu；完整结构待实例核验 |
| 抖音热搜 | https://docs.60s-api.viki.moe/254028799e0 | 栏目已确认，路径和变体实施时核验 |
| 头条热搜 | https://docs.60s-api.viki.moe/254038833e0 | 栏目已确认，路径和变体实施时核验 |

每日新闻核验补充：

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/60s.module.ts` 处理 `/60s`；实际请求路径 `/v2/60s`。
- 文档显示 `encoding` 支持 text/json/markdown/image/image-proxy，默认 json；`date` 可选，缺省时实例会尝试当天、昨天和前天。插件只使用默认 JSON，不传参数。
- 公开实例 `https://60s.viki.moe/v2/60s` 于 2026-10-23 低频请求一次：HTTP 200、`code=200`、`data.date=2026-10-23`、`data.news` 为 15 条字符串。该结果仅用于核验结构与解析边界，不代表其他部署实例；未设置为默认站点。
- 上游文档/源码示例中的字段不保证所有实例一致；插件对缺失日期、可选字段和两种新闻条目形态做容错，对必要结构变化明确报错。

不从上游文档中的示例推断所有实例版本一致，也不把 B站热搜当热门视频榜。引入源码、图标或模板时分别核对许可证与署名要求。
