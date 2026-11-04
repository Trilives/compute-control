# 公告、权限申请、管理员联系名单和小时历史监控

## 已确认方向

人类设置目标与验收标准，Agent 主导实现。用户本次明确要求五项变更，后续确认历史图放在每台机器卡片下方下拉展开/收起，可同时展开多台对比，默认当前状态不显示折线。依此扩展现有 Web、业务、数据库、SSH 和 worker 职责；不引入新依赖或远端常驻服务。

- 公告支持发布、置顶/取消置顶、软删除；用户可看全部未删除历史，仅置顶在成功姓名登录后弹窗，切页/刷新不重复弹。
- 原系统安装说明按钮改为“申请管理权限”，选择本人的有效账号及事由；管理员可安排帮助安装、授权 sudo 或拒绝并回复。帮助安装只记安排，不声称自动安装。
- 管理员在台账和机器账号列表查看权限，已登记普通账号提供开关；历史普通账号先接管，root/系统/机器管理账号受保护。新账号仍默认普通权限。
- 联系名单独立维护姓名和联系方式，仅展示，不增加控制台认证账号。保留旧联系方式文本，避免升级丢失信息。
- 取消网页轮询/心跳；worker 每小时探测所有已添加机器，页面关闭不影响。可申请刷新，按机 30 秒合并，采集后手动“更新显示”。失败记录尝试时间并按小时退避，保留旧成功快照。
- 保存 24 小时紧凑真实指标及失败点；展开机器下拉才读取历史，跨刷新保留。升级只保留已有真实快照，不回填不存在的数据。CPU 使用单次探测内计数，不冒充小时平均。

## 实现与权限边界

`portal.py` / `portal_api.py` / `static/portal.js` 分别承载本次批准功能的业务、HTTP 装配和页面，不反向依赖 Web 或 SSH 实现；继续使用现有角色、CSRF、数据访问、审计和持久任务。权限变更任务沿用同机锁和已创建/接管身份凭据，先记录意图，断线不自动重放。

授予 sudo 用独立、经 visudo 检查的规则，保留密码认证；关闭移除平台规则及目标 sudo/wheel 附属组，再查询实际授权。其他 sudoers 或管理主组可能阻止关闭，任务不会报成成功；核对后可重试。关闭不终止已有 root 进程/会话，组变化可能需重新登录。显示 sudo 授权包含受限命令授权，并不把未知状态当作关闭。

依据：[sudoers 手册](https://man7.org/linux/man-pages/man5/sudoers.5.html)、[sudo 查询手册](https://man7.org/linux/man-pages/man8/sudo.8.html)、[gpasswd 维护说明](https://www.man7.org/linux/man-pages/man1/gpasswd.1.html)。保留默认认证，未引入免密 sudo；回收前清理平台 sudoers 文件以免同名用户继承。

数据库升为版本 3；从旧版先一致性备份，增加公告、联系人、申请和历史表及权限/采样尝试字段；版本 1/2 迁移和重复启动均有测试。废弃租约配置删除，旧 API 仅兼容无采集效果的响应；配置中只保留实际使用项。

## 验证

- `uv run pytest -q --tb=short`：117 项通过，1 条已有 Starlette TestClient/httpx 弃用提示。包含真实 Chromium 两角色、置顶只在登录弹窗、安全文本、双机同时展开、默认无折线、申请处理/开关、权限失败核对、无网页后台采样、手动越过小时等待、失败保留与 24 小时过滤、版本迁移与服务重启不重复生成历史点。另一次 Chromium 定向回归验证并发加载不重复显示申请，1 项通过。
- `uv run ruff check src tests` / `uv run ruff format --check src tests` 通过，30 个 Python 文件已格式化；全部 JS 的 `node --check` 通过。
- [验证文档](../VERIFICATION.md)中的完整文档检查通过：62 个项目文本文件，登记/长度/格式/相对链接及私密权限有效。按用途登记新增文件，现有文件均在原限制内，没有放宽通用约束。
- `uv build` 成功；发行包含最新公开指南、portal 业务/API/静态文件及模板，私密指南和 runtime/master.key 排除。构建后与源文件内容比对，不发布到外部。
- 开发 Web/worker 已正常加载新版；真实配置为每小时采集，SQLite 版本 3，版本 2 升级前的一致性备份存在；原机器 1、人员 1、台账 1、任务 5 数量不变，未自动重放待核对任务。开发升级遗留的一个旧完整历史快照已裁为指标，未删除采样记录。
- 实际主机经 SSH/sudo 只读观察 9 个 root/普通账号，4 个有 sudo 授权、0 个未知；仅为当前快照，不是实际授权/撤权验收。手动请求刷新由后台完成，24 小时 API 只返回指标，未伪造全天数据。
- 实际 Chromium 确认：默认无折线，当前状态及新增管理入口存在，空闲等待 5.5 秒没有任何 API 轮询，展开机器下拉后出现 CPU/内存/GPU/显存历史图，指南与新行为一致，无脚本错误。截图在忽略的 `runtime/previews/hourly-history-expanded.png`。
- 本批没有真实 sudo 授权/撤权或重启，也没有创建/删除远端用户；未在实际库发布测试公告、联系人或权限申请。

## 改动文件

| 范围 | 文件 |
| --- | --- |
| API / 业务 / 持久化 | `src/compute_control/app.py`、`services.py`、`db.py`、`models.py`；新增 `portal.py`、`portal_api.py` |
| 执行 / 采集 / 配置 | `src/compute_control/worker.py`、`remote.py`、`config.py`，根目录 `config.toml` |
| 页面 | `src/compute_control/static/app.js`、`admin.js`、`charts.js`、`app.css`；新增 `portal.js`；`src/compute_control/templates/index.html` |
| 测试 | `tests/conftest.py`、`test_worker.py`、`test_admin_management.py`、`test_browser.py`；新增 `test_portal.py`、`test_privileges.py`、`test_portal_browser.py` |
| 文档 / 任务 | `ARCHITECTURE.md`、`README.md`；`docs/ADMIN_GUIDE.md`、`USER_GUIDE.md`、`CONFIGURATION.md`、`DEVELOPMENT_ENVIRONMENT.md`、`TODO.md`、`FILE_LENGTH_LIMITS.md`；本记录 |

## 清理与未完成事项

交付清理核对全项目引用、旧 5 秒/浏览器租约规则、指南和实际配置；页面加载使用请求代次，旧请求晚到不再重复追加或覆盖新页面，历史记录保留为历史证据。新增文件逐项登记长度，无项目文件删除，无 Git 仓库，未虚构提交；本批不改变通用开发约束。已完成功能结果放本记录，不留下已完成 Todo 条目。

实机真实权限变更、创建/回收和正式 TLS/防火墙部署未在本批执行，继续见 [Todo](../TODO.md)。24 小时历史需要真实时间积累，不宣称已有全天数据。无额外待确认产品选择；未来正式部署和测试账号前提仍需用户提供。下一次项目级清理按 [维护周期](../MAINTENANCE.md)执行。

后续卡片交互和固定顶栏见[调整记录](2026-11-04-machine-dialogs-sticky-header.md)。
