# 开发手册

本项目按个人开源项目维护。当前已建立最小插件入口、程序版本源和站点存储服务，其余业务功能尚未实现；代码、依赖和测试目录随后续功能按需创建。

## 获取与本地设置

```bash
git clone https://github.com/littleseven2003/astrbot_plugin_60sapi_hub.git
cd astrbot_plugin_60sapi_hub
git config core.hooksPath .githooks
```

提交者自行设置真实 Git 姓名/邮箱，推荐使用 GitHub 提供的隐私邮箱；不要提交个人全局 Git 配置。hooks 使用 Python 3，缺少运行环境应安装后再提交，不能关闭校验绕过。

## 开发环境

当前验证基线为 AstrBot v4.29.1 和 Python 3.12，依据与核验范围见下文“AstrBot 兼容性基线”。使用独立虚拟环境和独立 `ASTRBOT_ROOT`，不要改动正在使用的 AstrBot 实例。

运行依赖随实现添加，不提前安装整个依赖集合，不把 AstrBot 本体加入插件 requirements。当前运行依赖为 `aiosqlite`（异步 SQLite），约束见 [requirements.txt](../requirements.txt)；AstrBot 本体由宿主提供，不作为插件依赖安装。

测试宿主、凭据、运行数据库、图片缓存和未脱敏日志不提交到仓库。需要真实平台测试时使用专用测试账号/群。

## AstrBot 兼容性基线

### 验证基线

| 项目 | 值 |
|---|---|
| AstrBot | v4.29.1 |
| 源码 commit | `dffe922773725e9c178392c423676ad65044748c`（2026-10-17） |
| 源码 | https://github.com/AstrBotDevs/AstrBot/tree/v4.29.1 |
| Python | 3.12.13；v4.29.1 的 `pyproject.toml` 声明 `requires-python = ">=3.12"` |
| 官方文档 | https://docs.astrbot.app/dev/star/plugin-new.html 及同目录的配置、视图、存储、发送章节 |

选择依据：v4.29.1 是核验时仓库最新的稳定 tag，插件 API 以该 tag 源码为准。运行验证在隔离源码环境和独立 `ASTRBOT_ROOT` 中完成，没有连接真实消息平台。

不从旧版文档推断最低兼容版本。`metadata.yaml` 暂不声明 `astrbot_version`，因为尚未验证 4.29.1 以外的版本；扩大范围前须补充源码核对和运行验证。

### 已核验的宿主行为

**源码已核验，且运行已验证：**

- 插件目录为 `<ASTRBOT_ROOT>/data/plugins/<目录名>/`，入口是 `main.py`（也接受 `<目录名>.py`）；元数据文件为 `metadata.yaml` 或 `metadata.yml`。
- `metadata.yaml` 必填 `name`、`desc`、`version`、`author`，`repo` 可选；本仓库把其中的 `version` 作为唯一程序版本源。
- 插件主类继承 `astrbot.api.star.Star` 即可被自动发现，不需要 `register_star` 装饰器；类名不要求与目录名一致。
- 加载时实例化插件类，调用可选 `initialize()`；停用或重载时调用插件类自身定义的 `terminate()`，随后解绑处理器并重新导入模块。没有资源需要释放时不必写空生命周期方法。
- 在 WebUI 插件列表中可读到名称、作者、版本和仓库；停用、启用、重载均返回成功，重载后插件仍只有一份注册记录，未出现本插件引起的 Traceback。
- 插件数据目录通过 `StarTools.get_data_dir(plugin_name)` 创建；站点数据库位于 `<ASTRBOT_ROOT>/data/plugin_data/<插件名>/sites.sqlite3`。停用后数据库句柄关闭，启用/重载后数据保留。
- 初始加载、停用、启用和重载过程没有触发 60s API 请求，也没有发送消息。

**源码已核验，尚未在本轮运行验证：**

- 配置：`_conf_schema.json` 会被解析，配置以 `AstrBotConfig` 注入 `__init__`，持久化到 `<ASTRBOT_ROOT>/data/config/<插件目录名>_config.json`；`secret: true` 只遮罩 WebUI 显示，不加密配置文件。
- 插件视图：`views/<视图名>/index.html` 由 Dashboard 扫描并以受限 iframe 加载，前端通过 `window.AstrBotPluginView` bridge 调用后端；视图目录变更后需要重载插件。
- Web API：`context.register_web_api(route, handler, methods, desc)` 注册，路由需以插件名开头，由 Dashboard 负责鉴权；同路由同方法会替换旧注册，后端应使用 `astrbot.api.web` 的请求/响应封装。
- v4.29.1 的 `main.py` 仍保留 `>=3.10` 的历史运行时检查，但包元数据和依赖要求以 Python 3.12 为准，不能据此宣称 Python 3.10 可用。
- 停用/重载优先调用插件类自身定义的 `__del__`，没有才调用 `terminate`；插件不应使用 `__del__` 管理资源。重载不会整体清空已注册 Web API，但相同路由和方法会替换，新增路由须自行避免残留。

**未验证：**

- 4.29.1 以外的 AstrBot 版本、其他 Python 版本和其他操作系统。
- 配置表单在真实 WebUI 中的保存/重载、插件视图 bridge 调用、Web API 鉴权与上传。
- 消息平台、群成员隔离、主动投递、渲染、调度与重启恢复。
- SQLite 已做服务级和宿主路径/关闭验证，但未做多进程并发、崩溃恢复或大数据量测试。
- 60s API 实例的真实返回结构；这些属于后续 M2-M5。

### 可复现的本地验证

以下命令用于隔离环境，不要指向正在使用的 AstrBot 实例。两个代码块分别在两个独立终端执行；环境变量不会跨终端共享，因此终端二需要独立输入与终端一相同的临时密码。

**终端一：准备并启动宿主。** 在本插件仓库根目录执行：

```bash
git clone --depth 1 --branch v4.29.1 https://github.com/AstrBotDevs/AstrBot.git /tmp/astrbot-src
python3.12 -m venv /tmp/astrbot-venv
/tmp/astrbot-venv/bin/pip install -r /tmp/astrbot-src/requirements.txt

export ASTRBOT_ROOT=/tmp/astrbot-smoke
read -rsp '设置本地测试密码: ' DASHBOARD_PASSWORD; echo
export DASHBOARD_PASSWORD
mkdir -p "$ASTRBOT_ROOT/data/plugins"
ln -s "$PWD" "$ASTRBOT_ROOT/data/plugins/astrbot_plugin_60sapi_hub"

cd /tmp/astrbot-src
ASTRBOT_ROOT="$ASTRBOT_ROOT" DASHBOARD_HOST=127.0.0.1 DASHBOARD_PORT=16185 \
  ASTRBOT_DASHBOARD_INITIAL_PASSWORD="$DASHBOARD_PASSWORD" \
  /tmp/astrbot-venv/bin/python main.py
```

启动日志应出现 `Loading plugin astrbot_plugin_60sapi_hub` 和对应版本。日志中的初始密码仅用于本地验证，不要复制到仓库或公开渠道。

**终端二：登录并验证生命周期。** 另开终端，独立输入终端一设置的同一测试密码：

```bash
BASE=http://127.0.0.1:16185
read -rsp '输入终端一设置的测试密码: ' DASHBOARD_PASSWORD; echo
export DASHBOARD_PASSWORD

LOGIN=$(curl -sS -X POST "$BASE/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json,os; print(json.dumps({"username":"astrbot","password":os.environ["DASHBOARD_PASSWORD"]}))')")
TOKEN=$(printf '%s' "$LOGIN" | python3 -c 'import json,sys; d=json.load(sys.stdin); token=(d.get("data") or {}).get("token"); sys.exit(1) if not token else print(token)') \
  || { echo '登录失败，停止后续验证。' >&2; exit 1; }
unset DASHBOARD_PASSWORD LOGIN
AUTH="Authorization: Bearer $TOKEN"

curl -sS "$BASE/api/v1/plugins" -H "$AUTH"
curl -sS -X PATCH "$BASE/api/v1/plugins/enabled" -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"plugin_id":"astrbot_plugin_60sapi_hub","enabled":false}'
curl -sS -X PATCH "$BASE/api/v1/plugins/enabled" -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"plugin_id":"astrbot_plugin_60sapi_hub","enabled":true}'
curl -sS -X POST "$BASE/api/v1/plugins/reload" -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"plugin_id":"astrbot_plugin_60sapi_hub"}'
unset TOKEN AUTH
```

密码和 token 不应出现在命令回显、提交内容或日志附件中。非交互环境可在两个 shell 中分别显式 export 同一个临时密码，但不要把它写入仓库。

预期：插件列表该项的 `activated` 在停用/启用间变化；重载后同一名称仍只有一项；日志没有本插件引起的 Traceback；没有 60s API 请求或消息发送日志。登录失败时终端二会直接退出，不执行后续请求。验证后按 `Ctrl+C` 停止宿主，并清理 `/tmp/astrbot-src`、`/tmp/astrbot-venv`、`/tmp/astrbot-smoke`。

WebUI 初始密码只写入隔离环境日志；不要提交日志、token、数据库或真实站点凭据。

## 站点存储服务

- 领域模型、校验和服务：`core/sites.py`。公开模型为 `SiteConfig`、`SiteCreate`、`SiteUpdate`；错误分为校验、未找到、revision 冲突和存储不可用。
- SQLite 存储：`core/site_store.py`，使用 `aiosqlite` 异步访问，不阻塞事件循环。
- 数据库位置：`main.py` 通过 `StarTools.get_data_dir(plugin_name)` 获取插件数据目录，再使用其中的 `sites.sqlite3`。
- 生命周期：`initialize()` 打开数据库并完成首次建库，`terminate()` 关闭连接；打开失败会关闭已创建的连接，`close()` 可重复调用。
- schema：首次打开创建 `sites` 表，并把 `PRAGMA user_version` 设为 1；遇到更高版本拒绝打开，不自动删除或覆盖数据库。
- revision：新增为 1；编辑和删除必须传入期望 revision，冲突时抛出明确错误；成功编辑后加 1。
- 凭据：编辑使用 `keep`、`set`、`clear`；默认 `keep`，空白值不会清除原凭据。公开站点结构只有 `has_bearer_token`，输入对象和存储对象的调试输出也隐藏原文。
- 测试：在仓库根目录安装 `requirements.txt` 后运行 `python -m unittest discover -s tests -v`。测试使用临时数据库和虚构数据，不联网。
- 宿主检查：启动后确认数据库路径和 `PRAGMA user_version`；停用后确认数据库句柄已关闭；启用或重载后确认数据仍在且插件只有一份注册。不要对正在使用的插件数据目录做破坏性测试。
- 当前未注册后台视图或 Web API；站点操作只能通过服务层调用，后台页面属于后续工作。

## Git 提交规则

- 默认使用 main；需要隔离实验时使用短期 feat/、fix/ 或 docs/ 分支，完成后合回并清理。
- 不使用 develop、GitFlow 或强制 PR。一个人的小改动可以直接提交 main。
- 每个独立小改动完成且通过相关检查后立即 commit；一个提交只承担一个目的。
- 不把多个功能、无关重构和修复积攒为一个阶段性大提交；功能与验证它的测试可以同一提交。
- 提交前检查 git status、git diff、git diff --cached，确保只有本次修改。
- 标题格式：`类别: 中文描述`，使用英文半角冒号及一个空格。允许必要技术名词。

| 类别 | 用途 | 示例 |
|---|---|---|
| feat | 新功能 | feat: 支持知乎热榜内容解析 |
| fix | 修复 | fix: 避免任务重载后重复推送 |
| docs | 文档 | docs: 补充群聊白名单填写说明 |
| refactor | 重构 | refactor: 分离内容解析与消息投递 |
| perf | 性能 | perf: 合并相同接口的并发请求 |
| test | 测试 | test: 覆盖空白名单与投递超时 |
| build / ci | 构建或自动检查 | build: 声明图片渲染运行依赖 |
| chore / style | 维护或纯格式 | chore: 更新开发工具配置 |
| revert | 撤销 | revert: 撤销不兼容的任务恢复修改 |

commit-msg hook 检查类别、冒号空格及中文描述。它不能判断改动是否足够小，提交者仍须遵循原子提交规则。不得使用 --no-verify 或临时修改 hooksPath 绕过校验。

不提交真实 UMO、密钥、消息、数据库或本机绝对路径。已公开历史不 amend/rebase，不强推 main；撤销采用新的 revert 提交，并将自动生成标题改成规定格式。

## 版本来源

当前已在 `metadata.yaml` 的 `version` 字段建立唯一程序版本源，初始开发版本为 `v0.1.0-dev.1`；**尚无发布标签或可分发版本**。不额外维护容易不同步的 VERSION 或 `__version__`。

使用 SemVer：

- PATCH：向后兼容的修复，例如 0.1.0 → 0.1.1。
- MINOR：向后兼容的新功能，例如 0.1.1 → 0.2.0。
- MAJOR：1.0 之后的不兼容变更；0.x 阶段的不兼容变更提升 minor 并明确迁移说明。
- 预发布：开发快照 v0.1.0-dev.1、候选版 v0.1.0-rc.1，后续增加编号。
- 第一个正式版本 v0.1.0 在 MVP 完整验收后发布；不把空插件标为可用版本。

每个小提交不必增加版本号；用户可见变更及时累计到 docs/changelog.md 的 Unreleased。生成新的可分发快照/预发布/正式发布时必须使用未发布过的新版本，并将相应条目归入该版本标题。已发布版本不可复用。

## 发布步骤

1. 确认工作区干净，运行该版本需要的检查；记录真实宿主与平台矩阵。
2. 更新 metadata.yaml 的 version、作者、仓库及真实兼容范围。
3. 更新 docs/changelog.md，增加 `## [0.1.0] - YYYY-MM-DD` 等与版本对应的条目，并保留 Unreleased。
4. 提交：例如 `chore: 准备发布v0.1.0`。
5. 创建带注释的标签：`git tag -a v0.1.0 -m "发布v0.1.0"`。
6. 按维护者意图推送 main 和该标签，创建对应 GitHub Release；发布包不含本地数据。

pre-push hook 会检查：推送提交中的版本格式；标签必须为带注释 SemVer 标签、指向的版本与 metadata 一致、该版本在变更记录中有带日期条目；拒绝覆盖/删除发布标签和非快进更新 main。校验读取被推送的 commit，而非当前工作区。

本地 hooks 不会随 clone 自动启用，因此 clone 后必须设置 core.hooksPath。hooks 是本地防误操作措施，不代替审阅和服务端权限控制。

## 许可与贡献

采用 MIT，与上游 60s API 相同许可类型。引用上游源码时保留原版权/许可说明，不能仅更换作者名；调用 API 不意味着上游代码属于本项目。新增第三方依赖或资源前核对兼容许可。

提交影响行为的修改时同步需求、配置、使用手册或迁移说明；实现细节不必全部暴露给终端用户。
