# 60sAPI Hub

基于 [60s API](https://github.com/vikiboss/60s) 的 AstrBot 资讯聚合与订阅插件，计划支持多站点配置、多接口组合、文字与图片生成，以及群聊和私聊白名单定时推送。

**最小插件入口与站点存储服务已建立；站点配置可校验并持久化到 SQLite，但后台页面和业务功能尚未实现，当前不是可日常使用的插件，也没有发布版本。**

## 已实现

- 可被 AstrBot 发现、加载、停用、启用和重载的最小插件入口。
- 站点配置模型：名称、基础地址、启用状态、超时、可选 Bearer 凭据和 revision。
- HTTP(S)/内网/IPv6/反向代理前缀地址校验，以及误填 `/v2`、用户信息、查询串和 fragment 的明确拒绝。
- SQLite 站点增删改查、revision 冲突检测，以及凭据的保留、替换和清除。
- 插件停用/重载时关闭数据库连接，初始化失败时释放已创建资源。

## 功能规划（尚未实现）

- 在 AstrBot 后台添加、编辑、停用和删除站点。
- 选择每日新闻、B站热搜、知乎话题榜、抖音热搜、头条热搜等接口。
- 根据上游 JSON 自行组织文字、Markdown 和图片，支持多栏目与图片分页。
- 在 AstrBot 后台配置站点、内容组合、定时任务与预览。
- 每个任务独立填写完整 UMO 白名单，可混合群聊和私聊。
- 支持手动查询、定时推送、执行记录和失败处理。

## 文档

| 文档 | 内容 |
|---|---|
| [使用手册](docs/user-guide.md) | 产品使用方式、白名单与常见问题；当前为设计说明 |
| [需求说明](docs/requirements.md) | 功能范围与验收目标 |
| [配置说明](docs/configuration.md) | 站点、内容组合、任务与 UMO |
| [架构设计](docs/architecture.md) | 数据链路、模块边界与可靠性 |
| [开发手册](docs/development.md) | 环境、Git 提交、版本与发布流程 |
| [测试与验收](docs/testing.md) | 离线、宿主与平台验证 |
| [开发路线图](docs/roadmap.md) | 阶段目标与当前进度 |
| [变更记录](docs/changelog.md) | 已完成变更与发布历史 |
| [参考资料](docs/references.md) | 官方资料和核验边界 |

## 项目状态

最小插件入口和站点存储服务已建立。站点配置可新增、读取、编辑、删除并持久化到插件数据目录的 SQLite；后台页面、Web API、内容获取、解析、渲染、白名单和调度尚未实现。

已在 AstrBot v4.29.1 / Python 3.12.13 的隔离源码环境验证加载、停用、启用和重载：数据库位于 `data/plugin_data/astrbot_plugin_60sapi_hub/sites.sqlite3`，停用后连接关闭，启用/重载保留数据且无重复实例。站点校验与存储有服务级测试；后台页面操作、真实消息平台和 60s API 未验证。验证方法与兼容范围见[开发手册](docs/development.md#astrbot-兼容性基线)。

程序版本唯一来源为 [metadata.yaml](metadata.yaml)，当前为 `v0.1.0-dev.1`。运行依赖见 [requirements.txt](requirements.txt)，当前仅 `aiosqlite`（异步 SQLite）；AstrBot 本体由宿主提供，不列为插件依赖。

项目标识：`astrbot_plugin_60sapi_hub`。开发协作约定见根目录 [AGENTS.md](AGENTS.md)。

仓库：https://github.com/littleseven2003/astrbot_plugin_60sapi_hub

## 开源许可与致谢

本项目采用 [MIT License](LICENSE)，与 60s API 使用相同许可类型。感谢 60s API 提供数据服务与开源实现。本项目独立开发，不是 60s API 官方插件。

当前没有复制 60s API 源码。未来如引入其代码或其他第三方资源，将保留对应版权声明及许可要求。
