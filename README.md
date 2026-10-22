# 60sAPI Hub

基于 [60s API](https://github.com/vikiboss/60s) 的 AstrBot 资讯聚合与订阅插件，计划支持多站点配置、多接口组合、文字与图片生成，以及群聊和私聊白名单定时推送。

**项目处于设计阶段，目前仅提供文档，尚无可安装的插件或程序发布版本。**

## 功能规划

- 添加自建或其他可用的 60s API 站点。
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

代码、依赖、后台页面和测试目录将在实现相关功能时按需创建。当前仓库不包含空业务包、占位插件入口或尚未使用的依赖文件。

项目标识：`astrbot_plugin_60sapi_hub`。开发协作约定见根目录 [AGENTS.md](AGENTS.md)。

仓库：https://github.com/littleseven2003/astrbot_plugin_60sapi_hub

## 开源许可与致谢

本项目采用 [MIT License](LICENSE)，与 60s API 使用相同许可类型。感谢 60s API 提供数据服务与开源实现。本项目独立开发，不是 60s API 官方插件。

当前没有复制 60s API 源码。未来如引入其代码或其他第三方资源，将保留对应版权声明及许可要求。
