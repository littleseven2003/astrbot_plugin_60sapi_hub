# 参考资料

核验日期：2026-10-26。AstrBot 源码基线为 v4.29.1；60s API 源码核验 commit 为 `d582aa65d44f76e91c434174c9cf44684d30755f`（package 2.54.0）。已核对文档不等于已完成运行兼容验证，实现时优先检查所选版本源码。

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
| B站热搜 | https://docs.60s-api.viki.moe/254039323e0 | `/v2/bili`；GET；成功 `{code,message,data}`，`data` 为 `{title,link}` 数组；无排名/热度/更新时间字段 |
| 知乎话题榜 | https://docs.60s-api.viki.moe/254026504e0 | `/v2/zhihu`；GET；成功 `{code,message,data}`，`data` 为对象数组；含 `title/detail/hot_value_desc/link` 等，无排名/更新时间字段 |
| 抖音热搜 | https://docs.60s-api.viki.moe/254028799e0 | `/v2/douyin`；GET；固定默认热榜；`data` 为对象数组，含 `title/hot_value/link` 等；无排名字段 |
| 头条热搜 | https://docs.60s-api.viki.moe/254038833e0 | `/v2/toutiao`；GET；固定默认热榜；`data` 为对象数组，含 `title/hot_value/link` 等；无排名字段 |

每日新闻核验补充：

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/60s.module.ts` 处理 `/60s`；实际请求路径 `/v2/60s`。
- 文档显示 `encoding` 支持 text/json/markdown/image/image-proxy，默认 json；`date` 可选，缺省时实例会尝试当天、昨天和前天。插件只使用默认 JSON，不传参数。
- 公开实例 `https://60s.viki.moe/v2/60s` 于 2026-10-23 低频请求一次：HTTP 200、`code=200`、`data.date=2026-10-23`、`data.news` 为 15 条字符串。该结果仅用于核验结构与解析边界，不代表其他部署实例；未设置为默认站点。
- 上游文档/源码示例中的字段不保证所有实例一致；插件对缺失日期、可选字段和两种新闻条目形态做容错，对必要结构变化明确报错。

Bilibili 热搜核验补充：

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/bili.module.ts` 注册 `/bili`；实际请求路径 `/v2/bili`。
- 源码请求 Bilibili 内部接口后将列表映射为 `{title, link}`：标题取 `keyword` 或 `show_name`，链接为 Bilibili 搜索链接。内部 `position`、`hot_id` 等字段没有进入 60s API 响应。
- 文档页面最后修改时间 2025-11-11，示例只展示标题和链接。没有排名、热度或更新时间字段；数组顺序是接口返回顺序，不是经插件重排的排名。
- 公开实例 `https://60s.viki.moe/v2/bili` 于 2026-10-24 低频请求一次：HTTP 500、`code=500`，message 表明 Bilibili 上游返回 HTML 导致解析失败，未得到可用列表。因此真实 Bilibili 列表没有成功核验；插件行为由官方源码/文档和本地模拟验证，不能据此宣称公开实例可用。
- 不把 B站热搜当热门视频榜，不把列表序号当上游排名，不生成热度或更新时间。

知乎热榜核验补充：

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/zhihu.module.ts` 注册 `/zhihu`；实际请求路径 `/v2/zhihu`。
- 源码映射字段：`title`、`detail`（`target.excerpt`）、`cover`（首个子项缩略图）、`hot_value_desc`（`detail_text`）、`answer_cnt`、`follower_cnt`、`comment_cnt`、`created_at`（`target.created * 1000`）、`created`（格式化字符串）、`link`（`target.url` 转为 `www.zhihu.com/question/...`）。
- 文档页面最后修改时间 2025-11-11；公开实例 2026-10-24 低频请求成功：HTTP 200、`code=200`、30 条数组，字段与源码映射一致。`detail` 可为空，`hot_value_desc` 是“1670 万热度”这类文本。
- `created`/`created_at` 是话题创建时间，不是更新时间；没有排名字段。插件不把数组序号当排名，不把抓取时间当更新时间，不把热度文本换算成数值；`cover` 不渲染，避免自动请求远程图片。
- 公开实例只用于一次字段/解析核验，不写入默认配置；未用真实实例完成浏览器全链路。

抖音热搜核验补充：

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/douyin.module.ts` 注册 `/douyin`；实际请求路径 `/v2/douyin`，源码请求固定抖音内部接口，没有文档化查询参数或榜单变体。
- 文档页面最后修改时间 2025-11-11。源码映射：`title=word`、`hot_value`（原始整数）、`cover`、`link=https://www.douyin.com/search/...`、`event_time/event_time_at`、`active_time/active_time_at`；内部 `position` 不暴露。
- 公开实例 2026-10-26 低频请求成功：HTTP 200、`code=200`、50 条数组；`title` 字符串、`hot_value` 整数、`link` 搜索链接，另有封面和时间字段。
- 没有排名字段；数组顺序保留。`event_time`/`active_time` 等时间字段的单位/语义未作为更新时间使用，插件不展示；抓取时间不冒充更新时间。
- `hot_value` 是原始整数，插件不做“万”等单位换算；`cover` 不渲染，避免自动请求远程图片。
- 公开实例只用于一次字段/解析核验，不写入默认配置；未用真实实例完成浏览器全链路。

今日头条热榜核验补充：

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/toutiao.module.ts` 注册 `/toutiao`；实际请求路径 `/v2/toutiao`，源码请求固定头条内部接口，没有文档化查询参数或榜单变体。
- 文档页面最后修改时间 2025-11-11。源码映射：`title=Title`、`hot_value=+HotValue`（原始整数）、`cover=Image.url`、`link=Url` 去查询串和末尾斜杠；接口没有排名字段，也没有摘要、标签或独立时间字段。
- 公开实例 2026-10-26 低频请求成功：HTTP 200、`code=200`、50 条数组；`title` 字符串、`hot_value` 整数、`link` 头条链接，另有 `cover`。
- 数组顺序保留，序号不冒充排名；抓取时间不冒充更新时间；`hot_value` 不换算单位；`cover` 不渲染。
- 公开实例只用于一次字段/解析核验，不写入默认配置；未用真实实例完成浏览器全链路。

不从上游文档中的示例推断所有实例版本一致。引入源码、图标或模板时分别核对许可证与署名要求。
