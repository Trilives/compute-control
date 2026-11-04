# 管理扩展、操作指南与确认重启

## 范围与决策

人类确定目标和验收标准，Agent 主导既有架构内实现。本次用户明确授权：姓名删除、管理员联系方式、活跃普通用户人数、旧账号显式接管与重新分配、离职密码交接、开发工具说明和检测、完整角色操作指南，以及管理员确认整机重启。它们沿用现有账号、机器、任务、采集及界面职责，没有新的业务依赖方向。

- 姓名软删除保留人员 ID、台账和远端账号，撤销会话；同名重新添加恢复原身份。联系方式以纯文本保存和显示。
- 近 30 天按系统用户名去重，排除机器录入的 SSH 管理员和 root，包含历史普通账号；日志不全仍显式标记，人数突出供申请者选择。
- 历史账号需管理员显式接管，远端重新验证本地 UID/home；不创建同名用户、不改密码或停止进程。重新分配只改归属和额度。未知历史密码不能由 Linux 哈希还原，可记录已知值或显式重设。
- 新申请密码认证加密留存，普通列表/任务 API 不返回密文；仅当姓名已停用或移出时管理员可显式查看，审计不记密码。重设候选与生效密码分离，结果不明先核对。
- 工具政策唯一来源为包内 JSON，含原表全部工具及系统级 CUDA Toolkit/NVIDIA 驱动。检测只读、限时、可信系统路径；首次/手动刷新及约 10 分钟缓存到期查询，不改变账号准入、不安装软件。系统依赖问题联系管理员。
- 完整指南分别用“管理员指南”“用户指南”按钮弹窗；与开发工具说明分开。公开 Markdown 直接作为内容并打包，私密指南缩为管理员记录，不进入应用/发行包。
- 只有机器概览每 5 秒重绘；其他页面不定时重绘，可见登录页面继续续期并探测。普通用户密码继续为至少 3 字符，不要求复杂度。
- 整机重启二次确认，有工作或活动未知时第三次确认；管理员坚持后专用执行槽立即发送固定 `shutdown -r now`，绕过普通任务等待及机器锁。审计记录强制确认；结果不明不自动重复，平台任务仍按自身证据核对。

## 代码与交接

核心文件为 `account_admin.py`、既有 services/app/worker/remote/db/models，以及静态 admin/guides/app 和模板；`account_admin.py` 只是既有账号业务类的管理操作拆分。公开指南和工具清单随 wheel 提供。数据库迁移至版本 2，版本 1 先自动一致性备份，再增加列，测试覆盖数据保留和迁移幂等。

交付清理核对了全部项目文本、README 导航、公开/私密内容边界及规则权威来源，未删除项目文件，无 Git 仓库，不创建虚假提交。删除完成 Todo 条目依用户约定执行，本批没有把仍待实机验收的 T004–T007 标为完成。

## 验证结果

- 修改后全量 98 项自动化通过，含 Chromium 两角色流程、指南、表单不闪刷新、姓名删除、密码/权限、迁移、工具只读及重启二/三次确认；随后补充强制重启绕过普通执行槽和远端锁的回归测试。
- `uv run ruff check src tests`、`uv run ruff format --check src tests` 通过；25 个 Python 文件格式有效。全部静态 JS 分别运行 `node --check` 通过。
- `uv run pytest -q --tb=short`：100 项通过（含真实 Chromium、tar 沙箱及新增强制执行槽/锁回归）；有 1 条 Starlette TestClient/httpx 弃用提示，未隐藏，当前不影响通过。
- `uv build` 成功；检查 wheel 有公开管理员/用户指南、工具政策和静态脚本，sdist/wheel 均无 private、runtime 或 master.key。公开指南是页面和包的共同来源，不复制实际凭据。
- 运行 [VERIFICATION](../VERIFICATION.md) 中的完整文档检查：55 个文本文件登记、格式、长度、相对链接及私密权限通过。
- 开发 Web/worker 已加载新版；数据库版本 1→2 并产生权限 600 的一致性备份，原有机器 1、姓名 1、台账账号 1、任务 5 均保留，已有未知结果任务未被自动重放。
- 真实浏览器只读核对：管理员指南包含第三次确认，开发工具说明含 CUDA/驱动和联系管理员提示，机器卡片有重启按钮；切到姓名页面等待 6.5 秒，联系方式未保存草稿和 DOM 保留，同时后端采样时间继续增长；返回概览可见折线点和突出活跃人数。无浏览器脚本错误，截图保存在忽略的 `runtime/previews/admin-management-guides-tools.png`。
- 实机通过既有 SSH/sudo 只读检测 10 项系统工具均有结果，其中 CUDA Toolkit 13.0、NVIDIA 驱动 580.196.07；这是当前观察，不代表项目依赖兼容性验证。
- 没有真实重启任何主机。本批真实环境只读核对不会创建/删除 Linux 用户或改密码。

## 长度与清理

按本次实际规模更新逐文件基线。上限调整：app 400→500（权限路由）；services 420→500（既有业务增长）；worker 350→450（持久任务与强制重启调度）；remote 700→1000（一次 SSH 自包含脚本含明确接管/重设/重启与工具检测，不拆成远端服务）；浏览器测试 190→280（完整角色操作与确认流程）；登记表 80→100（新增文本逐一登记）。私密指南上限 180→100（仅凭据记录和公开指南链接）；新增文件按职责逐项登记，未放宽通用约束。

## 未完成验证

目标机创建/SSH 登录、回收/PAM 与归档故障链仍见 [Todo](../TODO.md)；正式 TLS/局域网防火墙部署等待正式主机。历史未留存密码无法补还原；不标准主目录或域账号不能自动接管。整机重启代码经模拟验证，真实重启待管理员实际操作，不将它写成已完成实机验收。下一次项目清理按 [维护周期](../MAINTENANCE.md)触发，最迟连续开发满一个月。

## 改动文件

| 范围 | 文件 |
| --- | --- |
| 业务与持久化 | `src/compute_control/account_admin.py`、`services.py`、`db.py`、`models.py` |
| API 与执行 | `src/compute_control/app.py`、`ssh.py`、`remote.py`、`worker.py` |
| 界面与工具政策 | `src/compute_control/static/admin.js`、`guides.js`、`app.js`、`app.css`；`src/compute_control/templates/index.html`、`src/compute_control/tool_policy.json` |
| 回归测试 | `tests/test_admin_management.py`、`test_development_guides.py`、`test_reboot.py`、`test_browser.py` |
| 架构、指南与打包 | `ARCHITECTURE.md`、`README.md`、`pyproject.toml`；`docs/ADMIN_GUIDE.md`、`USER_GUIDE.md`、`DEVELOPMENT_ENVIRONMENT.md`、`CONFIGURATION.md`；`docs/private/ADMIN_GUIDE.md`（不公开内容） |
| 任务与验证记录 | `docs/TODO.md`、`VERIFICATION.md`、`FILE_LENGTH_LIMITS.md`，本记录及此前申请/5 秒采样记录 |

本批无新增必须先确认的产品规则；上述未完成实机及部署前提继续在 Todo 跟进，不将模拟测试写成实机验收。
