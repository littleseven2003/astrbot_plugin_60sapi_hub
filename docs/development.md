# 开发手册

本项目按个人开源项目维护。M1、M2 已通过 AstrBot v4.29.1 / Python 3.12.13 基线验收，当前包括插件入口、站点与组合持久化、五类内容获取、共享缓存/手动刷新、后台预览、组合文字/Markdown 和分页图片。消息发送和调度尚未实现；代码、依赖和测试目录随后续功能按需创建。验收范围见 [测试手册](testing.md#m2-验收基线)。

## 获取与本地设置

```bash
git clone https://github.com/littleseven2003/astrbot_plugin_60sapi_hub.git
cd astrbot_plugin_60sapi_hub
git config core.hooksPath .githooks
```

提交者自行设置真实 Git 姓名/邮箱，推荐使用 GitHub 提供的隐私邮箱；不要提交个人全局 Git 配置。hooks 使用 Python 3，缺少运行环境应安装后再提交，不能关闭校验绕过。

## 开发环境

当前验证基线为 AstrBot v4.29.1 和 Python 3.12，依据与核验范围见下文“AstrBot 兼容性基线”。使用独立虚拟环境和独立 `ASTRBOT_ROOT`，不要改动正在使用的 AstrBot 实例。

运行依赖随实现添加，不提前安装整个依赖集合，不把 AstrBot 本体加入插件 requirements。当前运行依赖为 `aiosqlite`（异步 SQLite）、`httpx`（异步 HTTP）和 `Pillow`（本地中文图片渲染），约束见 [requirements.txt](../requirements.txt)；AstrBot 本体由宿主提供，不作为插件依赖安装。`httpx` 只由插件自身使用，不依赖宿主碰巧安装的版本。`Pillow` 只用于服务端绘制图片，按项目依赖安装；它不安装 AstrBot 本体。

图片中文字体不随仓库分发，按系统路径发现（macOS 实测 `Hiragino Sans GB`；Linux 可检测 Noto Sans CJK/WenQuanYi）；也可设置 `ASTRBOT_60SAPI_FONT` 指向管理员提供的字体文件。字体仍受其自身许可约束，插件不复制或再分发系统字体；缺少字体时返回 `font_unavailable`，不生成伪造或缺字图片。

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
- 插件视图：Dashboard 会扫描插件根目录的 `views/<视图名>/index.html`（其次 `pages/`），以受限 iframe 加载并自动注入 bridge。真实环境已验证 `views/sites/index.html` 被发现、页面标题通过 `.astrbot-plugin/i18n/zh-CN.json` 显示为“站点管理”，侧边栏入口可用。
- 视图 bridge：实际对象为 `window.AstrBotPluginView`（别名 `AstrBotPluginPage`），提供 `ready()`、`getContext()`、`getLocale()`、`getI18n()`、`t(...)`、`onContext(...)`、`apiGet(endpoint)`、`apiPost(endpoint, body)`、上传/下载和 SSE 方法。`endpoint` 是相对路径，由宿主拼成 `/api/v1/plugins/extensions/<插件名>/<endpoint>`；本页只使用 `apiGet`/`apiPost`，不创建独立登录或保存 Dashboard token。
- 视图 bridge 的响应约定：后端返回 `{status:"ok",data:...}` 时解析为 `data` 本身；返回 `{status:"error",message,...}` 或 HTTP 失败时抛出 `Error`。本页按成功、明确拒绝、revision 冲突和结果未确认分别处理。
- Web API：`context.register_web_api(route, handler, methods, desc)` 注册，路由须以 `/<插件名>/` 开头；Dashboard 负责扩展路由鉴权，处理器可通过 `astrbot.api.web.request.username` 检查当前用户。请求体用 `request.json(default=None)`，响应使用 `json_response`/`error_response`。同路由同方法会替换旧注册；重载不会整体清空注册表，因此插件用“实例是否已启用”标志拒绝停用后的旧处理器。
- 真实 Web API 已验证：未登录请求返回 401；登录后可完成站点列表、新增、读取、编辑、删除；校验错误返回 400、未找到返回 404、revision 冲突返回 409 且不覆盖、存储不可用返回 503；凭据只返回 `has_bearer_token`。停用后旧处理器返回 503，启用/重载后新实例恢复读写，数据仍保留。
- 站点管理页资源的 HTML/CSS/JS 由宿主页面服务按插件资产 token 提供；不启动第二个 HTTP 服务，不在页面读取或写入 Dashboard cookie/token。真实浏览器已验证新增、编辑、凭据保留/替换/清除、冲突、删除、刷新、停用/启用/重载和窄屏布局。
- 统一内容预览 API 已注册为 `POST /<插件名>/content/preview`，只接受站点 ID 和固定内容类型；每日新闻旧接口 `POST /<插件名>/daily-news/preview` 继续保留。两者沿用 Dashboard 鉴权和插件启用检查。真实宿主 API 已验证未登录 401、停用 503、五种内容正常/空内容/旧内容，以及未知类型 400、上游 HTTP/业务/非 JSON/重定向/大小/超时错误。
- 每日新闻页面已在真实 Dashboard 中验证选择已启用站点主动获取、正常/空内容/旧内容/错误/超时状态、HTML 文本安全渲染、打开页面和保存站点不自动请求、停用/启用/重载后恢复、未登录 401、窄屏无横向溢出。

**源码已核验，尚未在本轮运行验证：**

- 配置：`_conf_schema.json` 会被解析，配置以 `AstrBotConfig` 注入 `__init__`，持久化到 `<ASTRBOT_ROOT>/data/config/<插件目录名>_config.json`；`secret: true` 只遮罩 WebUI 显示，不加密配置文件。该能力当前未使用。
- v4.29.1 的 `main.py` 仍保留 `>=3.10` 的历史运行时检查，但包元数据和依赖要求以 Python 3.12 为准，不能据此宣称 Python 3.10 可用。
- 停用/重载优先调用插件类自身定义的 `__del__`，没有才调用 `terminate`；插件不应使用 `__del__` 管理资源。

**未验证：**

- 4.29.1 以外的 AstrBot 版本、其他 Python 版本和其他操作系统。
- 配置表单在真实 WebUI 中的保存/重载、上传功能、暗色主题以及除 Chromium 外的浏览器。
- 消息平台、群成员隔离、主动投递、渲染、调度与重启恢复。
- SQLite 已做服务级和宿主路径/关闭验证，但未做多进程并发、崩溃恢复或大数据量测试。
- 60s API 的官方文档/源码和一次公开实例响应已核验；尚未遍历社区实例、不同部署版本，也未用真实实例完成浏览器全链路验证。
- 其他 endpoint、消息发送和调度尚未实现；不要展示或宣称这些能力。组合持久化/预览、纯文字/Markdown 和固定样式分页图片已实现，但不能冒充消息发送或主题编辑。站点保存仍只做格式校验，不代替内容获取验证。

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
mkdir -p "$ASTRBOT_ROOT/data/plugins/astrbot_plugin_60sapi_hub"
PLUGIN_DIR="$ASTRBOT_ROOT/data/plugins/astrbot_plugin_60sapi_hub"
tar --exclude='.git' --exclude='__pycache__' -cf - . | tar -xf - -C "$PLUGIN_DIR"

cd /tmp/astrbot-src
ASTRBOT_ROOT="$ASTRBOT_ROOT" DASHBOARD_HOST=127.0.0.1 DASHBOARD_PORT=16185 \
  ASTRBOT_DASHBOARD_INITIAL_PASSWORD="$DASHBOARD_PASSWORD" \
  /tmp/astrbot-venv/bin/python main.py
```

启动日志应出现 `Loading plugin astrbot_plugin_60sapi_hub` 和对应版本。日志中的初始密码仅用于本地验证，不要复制到仓库或公开渠道。

**注意：** AstrBot v4.29.1 的页面发现会把插件根目录 `resolve()` 后要求其仍位于 `data/plugins` 下。若把仓库用符号链接放到插件目录，后台视图不会出现；这是宿主路径安全边界，不是插件视图错误。页面验证必须使用上面的实际目录副本，或正式安装产生的真实目录。

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
curl -sS "$BASE/api/v1/plugins/extensions/astrbot_plugin_60sapi_hub/sites" -H "$AUTH"
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

预期：插件列表该项的 `activated` 在停用/启用间变化；扩展站点列表在登录后返回 200；重载后同一名称仍只有一项；日志没有本插件引起的 Traceback；没有 60s API 请求或消息发送日志。登录失败时终端二会直接退出，不执行后续请求。页面可在登录后打开 `http://127.0.0.1:16185/#/plugin-view/astrbot_plugin_60sapi_hub/sites`；页面与 API 的完整验收步骤见[测试手册](testing.md)。

验证后按 `Ctrl+C` 停止宿主，并清理 `/tmp/astrbot-src`、`/tmp/astrbot-venv`、`/tmp/astrbot-smoke`。

WebUI 初始密码只写入隔离环境日志；不要提交日志、token、数据库或真实站点凭据。

## 站点存储服务

- 领域模型、校验和服务：`core/sites.py`。公开模型为 `SiteConfig`、`SiteCreate`、`SiteUpdate`；错误分为校验、未找到、revision 冲突和存储不可用。
- SQLite 存储：`core/site_store.py`，使用 `aiosqlite` 异步访问，不阻塞事件循环。
- 数据库位置：`main.py` 通过 `StarTools.get_data_dir(plugin_name)` 获取插件数据目录，再使用其中的 `sites.sqlite3`。
- 生命周期：`initialize()` 打开数据库并完成首次建库，`terminate()` 关闭连接；打开失败会关闭已创建的连接，`close()` 可重复调用。
- schema：当前版本为 2，包含 `sites`、`content_profiles`、`content_profile_items`。首次打开直接创建三张表；v1 数据库在同一事务中新增组合表并提升 `PRAGMA user_version=2`，失败回滚并保留站点、凭据和 revision；遇到更高版本拒绝打开，不自动删除或覆盖数据库。
- 组合与站点共用一个 `SiteStore` 连接、锁和事务；不另建 SQLite 连接。站点删除在事务中检查引用并由外键 `ON DELETE RESTRICT` 兜底，不级联删除组合。
- revision：新增为 1；编辑和删除必须传入期望 revision，冲突时抛出明确错误；成功编辑后加 1。
- 取消写入：新增、编辑、删除被取消时保留 `CancelledError`，在释放锁前完成回滚或关闭；提交前取消会回滚，COMMIT 已完成时不承诺撤销；清理无法确认时关闭连接并标记存储不可用，需要重建实例。
- 凭据：编辑使用 `keep`、`set`、`clear`；默认 `keep`，空白值不会清除原凭据。公开站点结构只有 `has_bearer_token`，输入对象和存储对象的调试输出也隐藏原文。
- 测试：在仓库根目录安装 `requirements.txt` 后运行 `python -m unittest discover -s tests -v`。测试使用临时数据库和虚构数据，不联网；`tests/test_site_api.py` 覆盖后台业务接口的成功与错误映射。
- 宿主检查：启动后确认数据库路径和 `PRAGMA user_version`；停用后确认数据库句柄已关闭；启用或重载后确认数据仍在且插件只有一份注册。不要对正在使用的插件数据目录做破坏性测试。

## 站点管理页面与 API

- 业务层：`core/site_api.py`；宿主适配：`site_web.py`；静态页面：`views/sites/index.html`、`style.css`、`app.js`；中文视图名：`.astrbot-plugin/i18n/zh-CN.json`。
- 路由由 `main.py` 在 `__init__` 注册。bridge 只有 `apiGet`/`apiPost`，所以读取用 GET，新增用 POST，编辑/删除用 POST 子路径；所有路由都在 `/<插件名>/` 下，由 Dashboard 扩展鉴权，处理器再检查用户名和插件启用状态。
- 校验与存储仍只有一套真源：页面不复制站点数据，不写本地存储，不缓存凭据；成功后重新读取列表。凭据编辑动作默认 `keep`，替换/清除需要显式选择；服务端只返回 `has_bearer_token`。
- 冲突不会自动覆盖：服务端返回 409，页面重新读取列表并提示重新编辑；普通校验失败保留表单输入，修正后可再次提交。保存期间禁用表单，避免重复提交。
- 写请求的响应丢失、超时或连接中断进入“结果未确认”：页面保留表单/目标信息，禁用保存或删除、取消和关闭，只提供“重新读取最新列表”。重新读取失败继续保持未确认；读取成功后显示最新列表状态，并要求人工确认后再关闭。新增记录不按同名/同地址认领，只根据服务器列表和稳定 ID 展示实际状态；编辑/删除不自动重发。
- 4.29.1 bridge 的失败对象只有 `Error.message`，不保留 HTTP 状态或响应 `data.code`。页面把已知的服务端拒绝消息视为明确失败，其余（含网络异常和未知 5xx）保守归为未确认。当前无需修改 API 契约；以后若增加机器可读错误码，应同步 bridge 能力和兼容性说明。
- 页面不使用 CDN、外部字体、统计脚本或独立服务器；列表用 `textContent` 等 DOM API 渲染，不使用用户数据拼 `innerHTML`。
- 当前包含五类内容单栏预览、缓存与预览、内容组合 CRUD/组合预览、文字/Markdown 和固定样式分页图片，不包含其他 endpoint、发送或调度。内容组合只引用已保存站点和五个现有类型；不能扩展成任意 URL、跨站点或参数面板。站点保存和组合保存本身不发起上游请求。组合预览成功后页面显示同源纯文字/Markdown，可复制或下载；格式切换、复制、下载不访问上游，不引入 HTML Markdown 渲染器。图片由服务端短期快照显式生成，逐页 base64 PNG 展示/下载；不重新请求上游，不使用 HTML 模板或远程资源。

## 内容获取与预览

### 每日新闻上游契约

- 官方源码路由：`src/router.ts` 的 `appRouter` 前缀为 `/v2`，`/60s` 路由由 `src/modules/60s.module.ts` 处理，因此实际路径为 `/v2/60s`。核验源码 commit `d582aa65d44f76e91c434174c9cf44684d30755f`（package 2.54.0），官方文档页面最后修改时间为 2025-11-19。
- 请求为 GET；`encoding` 参数支持 text/json/markdown/image/image-proxy，默认 JSON；`date` 可选，默认取当天并回退到前两天。本插件只请求默认 JSON 的最新内容，不传 `date`/`encoding`。
- 成功响应为 `{code:200, message, data}`；`data.news` 是必要数组。公开实例当前返回字符串数组，源码的 markdown 分支也兼容 `{title, link}` 对象，因此解析器同时支持两种条目。条数完全按上游返回，不写死 15 条，不补凑。
- `data.date` 通常为 `YYYY-MM-DD`，也容忍 `YYYY/MM/DD` 并归一化；缺失或无法识别时内容日期未知。`tip`、`data.link`、条目 `link` 为可选字段；只保留通过 HTTP(S) 校验的链接，不执行上游 HTML。
- 业务错误以 JSON `code != 200` 表示；HTTP 错误、非 JSON、结构变化、超时和大小超限分别归类。`news: []` 是合法空内容，不是解析失败。

### Bilibili 热搜上游契约

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/bili.module.ts` 注册 `/bili`，实际路径 `/v2/bili`；核验源码 commit `d582aa65d44f76e91c434174c9cf44684d30755f`（package 2.54.0），官方文档页面最后修改时间为 2025-11-11。
- 请求为 GET，无额外参数；成功响应同为 `{code, message, data}`，但 `data` 是数组，不再是每日新闻的对象。
- 官方源码把 Bilibili 内部列表映射为 `{title, link}`：标题取 `keyword` 或 `show_name`，链接为 `https://search.bilibili.com/all?keyword=...`。内部 `position`、`hot_id` 等字段没有进入 60s API 响应。
- 当前公开契约没有排名、热度或更新时间字段；本插件不生成这些值，不用数组序号充当排名，不把获取时间当上游更新时间。顺序按响应数组原样保留，实际条数按列表长度。
- `data: []` 是合法空列表；`data` 不是数组、条目不是对象或 `title` 不是非空字符串时是结构错误。`link` 可选，缺失、非字符串或不是安全 HTTP(S) 时忽略链接；不执行上游 HTML。
- 业务错误/HTTP 错误/非 JSON/超时/大小/重定向与每日新闻共用同一客户端和错误分类。

### 知乎热榜上游契约

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/zhihu.module.ts` 注册 `/zhihu`，实际路径 `/v2/zhihu`；核验源码 commit `d582aa65d44f76e91c434174c9cf44684d30755f`（package 2.54.0），官方文档页面最后修改时间为 2025-11-11。
- 请求为 GET，无额外参数；成功响应为 `{code, message, data}`，`data` 是数组。
- 源码映射：`title=target.title`、`detail=target.excerpt`、`cover=children[0].thumbnail`、`hot_value_desc=detail_text`、`answer_cnt/follower_cnt/comment_cnt`、`created_at=target.created*1000`、`created=localeTime`、`link=target.url` 转为 `www.zhihu.com/question/...`。
- 公开实例核验（2026-10-24）返回 HTTP 200、`code=200`、30 条；每条包含字符串 `title/detail/cover/hot_value_desc/created/link` 和整数计数/`created_at`。`detail` 可为空，`hot_value_desc` 是“1670 万热度”这类原始文本。
- `created`/`created_at` 是话题创建时间，不是更新时间；接口没有排名字段。插件不把数组序号当排名，不把抓取时间当更新时间，不把热度文本换算成数值。
- 本适配器只展示核验过的 `title`、可选 `detail`、可选 `hot_value_desc` 文本和可选安全 `link`；`cover` 不渲染，避免自动请求远程图片。必要字段 `title` 缺失或不是非空字符串是结构错误；其他可选字段异常保持未知。
- 公开实例只做一次低频请求，不写入默认配置；真实列表在仓库外保存用于解析边界核验，不作为本插件的默认站点。

### 抖音热搜上游契约

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/douyin.module.ts` 注册 `/douyin`，实际路径 `/v2/douyin`；核验源码 commit `d582aa65d44f76e91c434174c9cf44684d30755f`（package 2.54.0），官方文档页面最后修改时间为 2025-11-11。
- 当前只接入固定默认实时热搜榜；源码请求固定的抖音内部接口，没有文档化查询参数或榜单变体，本轮不扩展变体选择。
- 请求为 GET，无额外参数；成功响应为 `{code, message, data}`，`data` 是数组。
- 源码映射字段：`title=word`、`hot_value`（原始整数）、`cover`（`word_cover.url_list[0]`）、`link=https://www.douyin.com/search/...`、`event_time/event_time_at`、`active_time/active_time_at`。没有排名字段；数组顺序为上游顺序。
- 公开实例核验（2026-10-26）返回 HTTP 200、`code=200`、50 条；`title` 为字符串、`hot_value` 为整数、`link` 为抖音搜索链接，另有 `cover` 和时间字段。时间字段的实际单位/语义没有作为更新时间使用；本适配器不展示它们。
- 本适配器只展示核验过的 `title`、可选原始整数 `hot_value` 和可选安全 `link`；`hot_value` 不做单位换算，`cover` 不渲染，页面不自动请求远程图片。标题缺失或不是非空字符串是结构错误；其他可选字段异常保持未知。
- 不把数组序号当排名，不把获取时间当更新时间；公开实例只做一次低频请求，不写入默认配置。

### 今日头条热榜上游契约

- 官方源码 `src/router.ts` 使用 `/v2` 前缀，`src/modules/toutiao.module.ts` 注册 `/toutiao`，实际路径 `/v2/toutiao`；核验源码 commit `d582aa65d44f76e91c434174c9cf44684d30755f`（package 2.54.0），官方文档页面最后修改时间为 2025-11-11。
- 当前只接入固定默认热榜；源码请求固定的头条内部接口，没有文档化查询参数或榜单变体，本轮不扩展变体选择。
- 请求为 GET，无额外参数；成功响应为 `{code, message, data}`，`data` 是数组。
- 源码映射字段：`title=Title`、`hot_value=+HotValue`（原始整数）、`cover=Image.url`、`link=Url` 去除查询串和末尾斜杠。接口没有排名字段；数组顺序为上游顺序；没有摘要、标签或独立时间字段。
- 公开实例核验（2026-10-26）返回 HTTP 200、`code=200`、50 条；`title` 为字符串、`hot_value` 为整数、`link` 为头条链接，另有 `cover`。
- 本适配器只展示 `title`、可选原始整数 `hot_value` 和可选安全 `link`；`hot_value` 不做单位换算，`cover` 不渲染。标题缺失或不是非空字符串是结构错误；其他可选字段异常保持未知。
- 不把数组序号当排名，不把获取时间当更新时间；公开实例只做一次低频请求，不写入默认配置。

### 实现边界

- `core/content_client.py`：共享 `httpx.AsyncClient`；`core/content_service.py`：一个客户端组合五种适配器；`core/content_cache.py`：统一预览的 TTL/LRU 缓存和同键合并；`core/profiles.py`：组合模型/校验/revision CRUD；`core/profile_api.py` / `profile_web.py`：组合 CRUD 和组合预览 API；`core/profile_render.py`：纯函数文字/Markdown 渲染；`core/image_snapshots.py`：短期有界图片快照；`core/profile_image.py`：Pillow 固定样式分页渲染；`core/image_service.py`：图片线程池、超时和取消；`core/news.py` / `core/news_service.py`：每日新闻解析和 `/v2/60s` 获取；`core/bili.py` / `core/bili_service.py`：Bilibili 热搜解析和 `/v2/bili` 获取；`core/zhihu.py` / `core/zhihu_service.py`：知乎热榜解析和 `/v2/zhihu` 获取；`core/douyin.py` / `core/douyin_service.py`：抖音热搜解析和 `/v2/douyin` 获取；`core/toutiao.py` / `core/toutiao_service.py`：今日头条热榜解析和 `/v2/toutiao` 获取。
- `core/content_api.py` / `content_web.py`：统一 `POST /<插件名>/content/preview`，只接受 `site_id` 和固定 `content_type`；未知类型在调用任何服务前拒绝。`core/news_api.py` / `news_web.py`：保留每日新闻旧接口。
- `SiteService.get_fetch_config()` 是获取层读取站点地址、超时和凭据的唯一入口；公开 `SiteConfig` 仍不含凭据。获取层不直接读写 SQLite，也不接受页面提供的 URL。
- URL 为站点基础地址分别拼接 `/v2/60s`、`/v2/bili`、`/v2/zhihu`、`/v2/douyin` 或 `/v2/toutiao`，保留反代前缀；已支持内网/Docker/方括号 IPv6。
- 五种 endpoint 共用一个 `ContentHttpClient` 实例、连接池和全局并发预算；`ContentService` 不按 endpoint 分别创建客户端。统一预览经 `ContentCache` 做 TTL/LRU 缓存和同键合并。
- 客户端生命周期：`initialize()` 打开缓存和共享客户端，`terminate()` 先关闭缓存（拒绝新操作、取消并等待缓存自有任务、清空）再关闭客户端，最后关闭站点存储；打开失败释放已创建资源，停用后旧处理器由启用标志拒绝。`ContentHttpClient` 跟踪自己创建的请求任务；`close()` 先拒绝新请求，再取消并等待在途、排队和重试等待任务，最后关闭 `AsyncClient`。只取消本客户端的任务，不碰宿主无关任务；外部取消仍向调用方传播；关闭幂等，重新打开使用新 generation，不受旧请求影响。
- 超时：站点 `timeout_seconds` 是整体上限；连接超时为 `min(5, timeout)`，读/写/连接池使用同一上限。整体超时包住并发等待和重试。
- 重试：最多 2 次尝试（首次 + 1 次重试），只重试网络传输错误和 408/429/502/503/504；重试间隔 0.25 秒，且不会突破整体超时。业务错误、非 JSON、结构变化、重定向和响应过大部分不重试。
- 大小与并发：响应体最多 2 MiB，先检查 Content-Length，再在流式读取中累计限制；错误响应只丢弃最多 8 KiB，不解析、不记录、不返回正文。共享客户端最多 2 个并发请求。
- 错误脱敏：`NewsHTTPStatusError` 只暴露数字 HTTP 状态，`NewsBusinessError` 只暴露数字业务 code；上游错误正文不进入异常 message、后台响应或日志。回归链路从真实 `ContentHttpClient` 经 `NewsService`/`ContentService` 到对应 API，让模拟上游在错误正文回显虚构 Bearer token，断言响应无凭据。
- 可选链接容错：共享 `optional_http_url()` 只捕获 `urlsplit()` 的 `ValueError`/`UnicodeError`，未闭合 IPv6 方括号或 NFKC 展开后非法的 Unicode 主机会返回 `None`；合法 HTTP(S) 和方括号 IPv6 保留。它不捕获其他异常，必要标题缺失仍按结构错误处理，不能因忽略链接而吞掉字段校验。
- 重定向：`follow_redirects=False`，任何 3xx 都返回错误，不把 Authorization 发往新目标；`trust_env=False`，不自动使用环境代理。
- 缓存只覆盖统一预览接口，不改变旧每日新闻 `/daily-news/preview` 的主动获取响应结构。
- 缓存默认 TTL 300 秒、容量 32、LRU；只缓存成功快照（包括合法空列表），错误不缓存。TTL 使用可注入单调时钟，从成功完成获取后开始。键为站点 ID、内容类型、明确空参数和站点 revision，不含 Bearer。
- 每次读取缓存前重新读取站点配置；站点停用/删除拒绝，名称/地址/凭据修改递增 revision，旧快照不可复用。每日新闻用当前中国日期作为 day token，跨日失效。
- 普通获取命中有效缓存不请求上游；`force_refresh` 严格布尔，绕过已完成缓存但复用同键在途获取，不直接返回旧缓存。同键获取合并；单个等待者取消不影响其他等待者，最后一个取消才终止共享获取；等待有 130 秒上限。
- 最后一个等待者退出时原子地移除同键在途记录并标记为已取消，之后同键请求创建新任务，不加入正在清理的旧任务；旧任务保留在缓存自有任务集合中，不能写入缓存或删除新记录，关闭时由 `close()` 取消并等待清理。
- 刷新失败不缓存错误、不覆盖旧成功缓存；后续普通获取仍可命中旧缓存。响应独立返回 `cache.hit/expires_at/ttl_seconds`，不写入领域快照，不改变 `fetched_at`。
- 输出复制使用输出 epoch、格式、文本和操作序号；异步完成/拒绝只允许当前操作修改提示、焦点和选区，格式切换、下载、刷新、失效、组合切换或编辑都会使旧回调失效。
- 组合页面维护“组合配置签名”（ID、revision、名称、站点、栏目顺序和 limit）与请求令牌。列表成功刷新、编辑/删除当前组合时签名变化则清除结果；预览响应只有在令牌、当前 ID、当前签名和响应 `profile_revision` 全部匹配时才渲染。旧请求 finally 仅在仍持有最新令牌时清理 busy，避免解锁新请求。
- 关闭时缓存取消并等待自有在途任务、清空并拒绝新操作；不取消宿主无关任务，不留下后台任务或未处理异常。

### 测试与核验

- `tests/test_news.py`：URL/反代/IPv6、字符串/对象条目、顺序与条数、空内容、缺失/非法/旧日期、业务/结构错误、站点启用/禁用/凭据隐藏、服务层 URL/令牌/超时传递。
- `tests/test_news_http.py`：成功、重定向不跟随、可重试状态、重试上限、非重试状态、Content-Length 和流式大小限制、传输错误、超时、取消传播、关闭客户端、并发上限；并用事件同步覆盖在途、排队、重试等待中的关闭，关闭幂等、重新打开、无关任务不被取消。
- `tests/test_news_api.py`：成功快照、无凭据泄漏、非法请求体、站点/上游错误映射和通用 500；覆盖 HTTP 错误和 HTTP 200 业务错误正文回显虚构 Bearer token 时后台响应不包含凭据。
- `tests/test_bili.py`：正常/空列表、实际顺序与条数、标题/链接可选字段和畸形链接、结构/业务错误、URL/反代/IPv6、服务层 URL/令牌/超时。
- `tests/test_zhihu.py`：正常/空列表、实际顺序和条数、标题/摘要/热度文本/可选链接、畸形链接、结构/业务错误、URL/反代/IPv6、服务层 URL/令牌/超时。
- `tests/test_douyin.py`：正常/空列表、实际顺序和条数、标题/原始热度整数/可选链接、畸形链接、结构/业务错误、URL/反代/IPv6、服务层 URL/令牌/超时。
- `tests/test_toutiao.py`：正常/空列表、实际顺序和条数、标题/原始热度整数/可选链接、畸形链接、结构/业务错误、URL/反代/IPv6、服务层 URL/令牌/超时。
- `tests/test_content_service.py`：五种内容共用一个客户端和并发预算，使用各自站点凭据。
- `tests/test_content_api.py`：五种内容成功映射、未知类型在任何请求前拒绝、Bilibili/知乎/抖音/今日头条空列表/顺序/无伪造指标、HTTP/业务错误回显 token 时无凭据、畸形链接不导致 500，以及严格 `force_refresh` 校验。
- `tests/test_content_cache.py`：首次未命中/命中/TTL 边界/过期、强制刷新、合法空值、错误不缓存、刷新失败保留旧缓存、键隔离、LRU/过期清理、同键合并、等待者取消、等待超时、共享失败重试、关闭/重开。
- `tests/test_content_cache_integration.py`：五种内容缓存、daily 跨中国日期、名称/地址/凭据 revision、禁用/删除、在途配置变化、API 缓存元信息、严格 force_refresh、刷新失败保留旧缓存。
- `tests/test_profiles.py`：模型/类型/重复/顺序/limit/params 校验、CRUD/revision、重开持久化、v1→v2 迁移、站点引用删除拒绝、并发删除/创建无悬空引用。
- `tests/test_profile_api.py`：组合 CRUD/冲突、五类预览顺序、逐栏截取不污染完整缓存、缓存命中/刷新、station revision、部分失败/全部失败/空列表、错误脱敏、禁用站点和严格 force_refresh，以及成功/部分成功响应包含同源 outputs。
- `tests/test_profile_render.py`：纯函数同源、不修改输入；文字字段/顺序/限制/日期提示/空栏目/零值热度/失败栏目；Markdown 转义标题、换行、HTML、围栏、链接括号/空格、非法链接降级和生成结构。
- `tests/test_image_snapshots.py`：TTL、LRU/数量、总字节、单快照大小、关闭后拒绝访问。
- `tests/test_profile_image.py`：真实 PNG、长内容多页、同快照确定性、空列表/热度 0/部分失败、页数/字节/字体/取消限制。
- `tests/test_image_service.py`：线程池超时、关闭阻塞渲染和资源释放。
- `tests/test_profile_image_api.py`：同预览快照、图片不增加上游请求、后改数据不改变同快照图片、归属/过期/修改/删除/停用失效、全部失败无快照、图片超限仍保留文字。

- 宿主与浏览器验证使用本地模拟上游，覆盖每日新闻、Bilibili 热搜、知乎热榜、抖音热搜与今日头条热榜切换、正常、空内容、HTML 文本、HTTP/业务/非 JSON/超时/大小/重定向、停用/启用/重载、未登录和窄屏；另外在慢速上游请求在途时停用/重载，确认旧请求被取消、没有后续重试、没有 RuntimeError，重新启用/重载后五种内容均恢复；不连接真实消息平台。
- 官方公开实例 `https://60s.viki.moe/v2/60s` 只做一次低频 GET 核验：HTTP 200、`code=200`、内容日期与 15 条字符串新闻；保存的响应用解析器验证通过。没有把该实例写入默认配置，也没有用真实实例做浏览器全链路。
- Bilibili 官方公开实例 `https://60s.viki.moe/v2/bili` 于 2026-10-24 低频请求一次：HTTP 500、`code=500`，message 表明 Bilibili 上游返回 HTML，未得到可用列表。因此 Bilibili 的正常/空/错误/HTML 行为全部由本地模拟和官方源码/文档核验；不声称真实列表验证通过。
- 知乎官方公开实例 `https://60s.viki.moe/v2/zhihu` 于 2026-10-24 低频请求一次：HTTP 200、`code=200`、30 条数组；字段与源码映射一致，链接为 `www.zhihu.com/question/...`。真实响应只用于字段/解析核验，未把实例写入默认配置，也未用真实实例完成浏览器全链路。
- 抖音官方公开实例 `https://60s.viki.moe/v2/douyin` 于 2026-10-26 低频请求一次：HTTP 200、`code=200`、50 条数组；`hot_value` 为整数，链接为 `www.douyin.com/search/...`，另有封面和时间字段。未把实例写入默认配置，也未用真实实例完成浏览器全链路。
- 今日头条官方公开实例 `https://60s.viki.moe/v2/toutiao` 于 2026-10-26 低频请求一次：HTTP 200、`code=200`、50 条数组；`title` 字符串、`hot_value` 整数、`link` 头条链接，另有 `cover`。未把实例写入默认配置，也未用真实实例完成浏览器全链路。

## Git 提交规则

- main 保存已经验收的稳定开发基线。新功能、修复和独立文档改动从同步后的 main 创建短期 feat/、fix/、docs/ 分支，不直接在 main 开展新任务。
- 一个可独立验收的功能对应一个分支和 PR；不要求每个小 commit 单独创建 PR，也不把整个里程碑强行塞进一个分支。
- 分支完成并检查后，通过以 main 为目标的 PR 合并。不使用 develop、GitFlow 或多人审批流程。
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

### 分支、推送与 PR

开始新任务前确认工作区干净，使用 `git fetch origin` 核对远端；在 main 执行 `git pull --ff-only`，再创建功能分支。存在未提交改动或 main 分叉时先检查原因，不用强制重置覆盖工作。

实现者在指定分支完成原子提交。经检查后推送该分支，存在实际差异时创建目标为 main 的 PR；空分支不创建 PR。PR 标题也采用 `类别: 中文描述`，正文说明最终功能、验证结果和未验证项。

合并前确认验证针对最终提交完成。默认使用 merge commit 保留所有原子提交，不 squash；合并提交标题同样遵循中文描述格式，例如 `chore: 合并每日新闻获取与预览功能`。合并后在干净工作区同步 main，确认远端与本地一致，再清理已合并分支。推送、合并和发布分别按维护者授权执行。

分支隔离、PR 和本地 hooks 是开发流程；这些约定不等同于 GitHub 已开启服务端分支保护。

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
