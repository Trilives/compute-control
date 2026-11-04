# 文件长度限制

适用全部项目文本，包括源码、测试、点文件、README 和私密文档；二进制、外部依赖及生成的运行/缓存数据不逐文件登记。排除 `.git/`、`.venv/`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`、`runtime/`、`backups/`、`dist/`；保留在项目中的依赖锁照常登记。按 `splitlines()` 计物理行，空行计入。不在各文件头复写限制，也不要求每次编辑执行检查。

下表为本次清理后的逐文件登记。“基线”是该次清理的实际长度，不是随每次编辑刷新的计数；最新长度在项目级清理时重新测量。尚未出现的文件不预造路径，新增文本文件在交付时登记。

| 路径 | 上限（行） | 基线（行） | 例外 | 理由 |
| --- | --- | --- | --- | --- |
| `.gitignore` | 30 | 27 | 无 | 仅登记秘密和运行产物，不承载工作流程 |
| `.env` | 10 | 3 | 私密且 Git 忽略 | 仅提供用户授权的目标 SSH 接入信息，不含公开配置 |
| `ARCHITECTURE.md` | 460 | 429 | 无 | 现有完整生命周期及安全边界集中说明，补充依赖与数据流仍可单篇阅读 |
| `README.md` | 80 | 43 | 无 | 小型设计项目的导航入口，保留状态与链接即可 |
| `config.toml` | 50 | 10 | 无 | 实际启动配置，逐项解释由配置说明负责 |
| `docs/CONFIGURATION.md` | 100 | 33 | 无 | 逐项配置及秘密供应，避免混入产品指南 |
| `docs/DEPLOYMENT.md` | 100 | 44 | 无 | 正式部署、HTTPS、systemd、精确防火墙、备份与回滚 |
| `deploy/compute-control-web.service` | 60 | 30 | 无 | 独立非 root HTTPS Web 服务及低端口绑定能力 |
| `deploy/compute-control-worker.service` | 60 | 28 | 无 | 单主后台 worker 的 systemd 运行和持久状态约束 |
| `deploy/compute-control-http.conf` | 30 | 6 | 无 | 明确授权 HTTP 时覆盖 Web TLS 启动参数 |
| `tests/test_http_transport.py` | 280 | 132 | 无 | 显式 HTTP、默认 TLS、配置类型、Cookie/CSRF 及真实浏览器迁移回归 |
| `docs/records/2026-11-04-http-access.md` | 120 | 43 | 无 | 本次 HTTP 授权、部署、RED/GREEN、覆盖率和实际验证交接 |
| `docs/records/2026-11-04-illustrated-tutorials.md` | 80 | 21 | 无 | 分角色图文教程、内部凭据隔离、范围修改及制作中发现的 HTTP 表单兼容修复 |
| `docs/records/2026-11-04-production-deployment.md` | 100 | 42 | 无 | 本次中断恢复、实际部署、数据迁移、备份及验收边界 |
| `docs/records/2026-11-04-vpn-access.md` | 80 | 16 | 无 | 授权 VPN 访问入口、证书扩展、实际验证边界及回滚 |
| `docs/FILE_LENGTH_LIMITS.md` | 140 | 90 | 无 | 当前全部文本逐文件登记及清理规则，新增部署记录和回归测试条目 |
| `docs/MAINTENANCE.md` | 100 | 36 | 无 | 维护周期、触发条件与改进方向，不复制任务状态 |
| `docs/TODO.md` | 160 | 54 | 无 | 开头保留简短约束，下方只留未完成任务，完成结果转存记录 |
| `docs/USER_GUIDE.md` | 100 | 95 | 无 | 面向申请者的短操作指南和常见问题 |
| `docs/VERIFICATION.md` | 170 | 123 | 无 | 包含当前环境证据和可直接执行的文档检查命令 |
| `docs/private/ADMIN_GUIDE.md` | 100 | 51 | 无 | 只保存私密部署与凭据记录，完整操作链接公开管理员指南 |
| `docs/records/2026-11-03-constraints-simplification.md` | 40 | 16 | 无 | 仅记录一次简短调整及验证，不累积其他工作 |
| `docs/records/2026-11-03-first-implementation.md` | 160 | 53 | 无 | 一次实现、依赖依据、验收及清理交接，不积累后续无关工作 |
| `docs/records/2026-11-03-project-baseline.md` | 160 | 63 | 无 | 一次基线检查、决策、清理与验收证据，避免累加无关后续工作 |
| `docs/records/2026-11-03-todo-consolidation.md` | 60 | 9 | 无 | 保存一次用户澄清、合并删除及验证结果 |
| `pyproject.toml` | 100 | 44 | 无 | 项目入口、依赖与 Python 工具配置，保持单一来源 |
| `src/compute_control/__init__.py` | 30 | 1 | 无 | 包标识，不承载业务 |
| `src/compute_control/app.py` | 500 | 437 | 无 | HTTP 路由及角色权限装配，包含已授权的账号管理和重启接口 |
| `src/compute_control/cli.py` | 140 | 87 | 无 | 初始化、Web 和 worker 的本机命令装配 |
| `src/compute_control/config.py` | 110 | 59 | 无 | 启动配置加载与校验，说明留在配置文档 |
| `src/compute_control/db.py` | 180 | 163 | 无 | 首版表结构、连接、事务和基础审计持久化 |
| `src/compute_control/models.py` | 130 | 114 | 无 | 公共请求的类型和校验规则 |
| `src/compute_control/remote.py` | 1100 | 980 | 无 | 一次 SSH 传输的自包含固定操作；含只读工具检测、接管、改密和立即重启，不安装远端服务 |
| `src/compute_control/security.py` | 190 | 138 | 无 | 密码、会话及凭据加密，不混入 HTTP 页面 |
| `src/compute_control/services.py` | 550 | 498 | 无 | 既有机器、额度、台账业务及重启维护检查；管理操作拆到同职责文件 |
| `src/compute_control/ssh.py` | 190 | 142 | 无 | 固定指纹及分离 sudo/操作输入协议 |
| `src/compute_control/static/app.css` | 180 | 126 | 无 | 当前页面布局与响应式样式 |
| `src/compute_control/static/app.js` | 280 | 258 | 无 | 单控制台交互与 API 调用，无 Linux 命令或业务授权规则 |
| `src/compute_control/templates/index.html` | 140 | 94 | 无 | 单页入口与操作表单，不含业务脚本 |
| `src/compute_control/worker.py` | 450 | 402 | 无 | 持久任务、采集调度、密码交接及独立强制重启执行槽 |
| `tests/conftest.py` | 190 | 165 | 无 | 按被验证职责组织，覆盖异常和并发；当前小型测试集保持单篇可读 |
| `tests/test_accounts.py` | 190 | 179 | 无 | 按被验证职责组织，覆盖异常和并发；当前小型测试集保持单篇可读 |
| `tests/test_archive.py` | 280 | 194 | 无 | 按被验证职责组织，覆盖异常和并发；当前小型测试集保持单篇可读 |
| `tests/test_auth.py` | 190 | 186 | 无 | 按被验证职责组织，覆盖异常和并发；当前小型测试集保持单篇可读 |
| `tests/test_browser.py` | 280 | 245 | 无 | 真实 Chromium 两角色流程、图表、完整指南、稳定表单与重启确认 |
| `tests/test_remote_config.py` | 190 | 109 | 无 | 按被验证职责组织，覆盖异常和并发；当前小型测试集保持单篇可读 |
| `tests/test_ssh_transport.py` | 190 | 123 | 无 | 按被验证职责组织，覆盖异常和并发；当前小型测试集保持单篇可读 |
| `tests/test_worker.py` | 280 | 241 | 无 | 按被验证职责组织，覆盖异常和并发；当前小型测试集保持单篇可读 |
| `uv.lock` | 2000 | 974 | 机器生成，不人工裁剪 | 锁定全部依赖、来源及校验值，当前不足千行；升级时按解析结果重估 |
| `docs/records/2026-11-03-machine-enrollment-fix.md` | 100 | 12 | 无 | 一次接入交互、能力诊断、修复及实际只读复测记录 |
| `tests/test_capabilities.py` | 190 | 47 | 无 | NSS/SSSD 检测误判及实际启用条件的分支验证 |
| `tests/test_machine_enrollment.py` | 190 | 143 | 无 | 自动指纹接入、失败不落库、旧客户端兼容及提示验证 |
| `src/compute_control/static/charts.js` | 160 | 95 | 无 | 读取 24 小时持久指标、机器下拉及 SVG 折线渲染，不混入表单和会话逻辑 |
| `docs/records/2026-11-03-monitoring-charts-theme.md` | 100 | 13 | 无 | 记录一次监控频率、可见性和主题交付与实际验证 |
| `docs/records/2026-11-03-application-entry-and-five-second-sampling.md` | 100 | 13 | 无 | 一次申请入口、短周期采样、用户名和普通密码规则交付及验证 |
| `src/compute_control/account_admin.py` | 340 | 312 | 无 | 账号生命周期已有职责内的管理员接管、分配、密码交接及机器重启请求 |
| `src/compute_control/static/admin.js` | 130 | 83 | 无 | 管理员表单、密码显示清除及二次/第三次重启确认，授权留在服务端 |
| `src/compute_control/static/guides.js` | 110 | 59 | 无 | 安全 DOM Markdown 展示及工具说明弹窗，不复制指南正文 |
| `src/compute_control/tool_policy.json` | 350 | 234 | 无 | 两模式共享的工具分层及限时只读检测清单，保留可读 JSON |
| `tests/test_admin_management.py` | 240 | 198 | 无 | 姓名删除、接管、分配、密码留存、权限和数据库迁移回归 |
| `tests/test_development_guides.py` | 150 | 72 | 无 | 工具检测只读边界及指南按角色选择、不访问私密内容 |
| `tests/test_reboot.py` | 260 | 185 | 无 | 模拟确认、重启命令幂等及绕过普通执行槽和远端锁，不真实重启 |
| `docs/ADMIN_GUIDE.md` | 180 | 158 | 无 | 完整可公开管理员操作说明，直接作为页面弹窗正文 |
| `docs/DEVELOPMENT_ENVIRONMENT.md` | 60 | 13 | 无 | 工具分层入口及检测边界，清单链接包内唯一数据源 |
| `docs/records/2026-11-03-admin-management-guides-reboot.md` | 130 | 53 | 无 | 本批授权、实现、验证及交付清理交接，不累加后续无关事项 |
| `src/compute_control/portal.py` | 300 | 236 | 无 | 公告、联系名单、sudo 权限申请、备注事务删除和历史读取，沿用现有数据与任务契约 |
| `src/compute_control/portal_api.py` | 120 | 84 | 无 | 新增接口的角色/CSRF 依赖装配，不混入 Linux 实现 |
| `src/compute_control/static/portal.js` | 150 | 116 | 无 | 公告、联系人、权限申请和账号开关页面，正文来自 API，不存秘密 |
| `tests/test_portal.py` | 300 | 224 | 无 | 公告/联系/申请权限、小时采集、失败与历史过滤及旧版迁移验证 |
| `tests/test_privileges.py` | 240 | 149 | 无 | sudo 授权、撤权、保护身份和失败核对，全部模拟系统变更 |
| `tests/test_portal_browser.py` | 180 | 136 | 无 | 真实 Chromium 验证登录公告、双机下拉、联系人与权限申请处理闭环 |
| `docs/records/2026-11-04-announcements-permissions-hourly-history.md` | 140 | 51 | 无 | 本批产品确认、实现、验证及清理记录，保存交接边界 |
| `docs/records/2026-11-04-machine-dialogs-sticky-header.md` | 80 | 24 | 无 | 一次按钮弹窗和固定顶栏的实现与只读验证交接 |
| `src/compute_control/static/dialogs.js` | 100 | 49 | 无 | 页面层统一弹窗布局、焦点和滚动入口，保留原表单与事件 |
| `docs/records/2026-11-04-compact-cards-and-modal-layout.md` | 100 | 23 | 无 | 紧凑卡片和全部弹窗交互修复及验证的交接 |
| `tests/test_ledger_cleanup.py` | 260 | 178 | 无 | 台账展示清理/恢复、对象权限、保留历史、迁移及真实浏览器流程 |
| `tests/test_machine_notes.py` | 200 | 82 | 无 | 共享备注的可见范围、作者防伪、双角色追加和纯文本浏览器验证 |
| `docs/records/2026-11-04-ledger-notes-and-account-count.md` | 120 | 38 | 无 | 本批台账/备注/同机总额规则的决策、验收和交接 |
| `docs/records/2026-11-04-alerts-row-actions-root-filter.md` | 100 | 23 | 无 | 本批告警、三列对齐和账号展示修复的决策与验证 |

| `tests/test_ui_review.py` | 140 | 93 | 无 | 两种角色入口、按钮分组、手动刷新、弹窗和四种屏宽的浏览器复查 |
| `tests/test_name_matching.py` | 120 | 64 | 无 | 英文姓名大小写、重复/恢复/改名及旧冲突的身份保护回归 |
| `docs/records/2026-11-04-ui-review-and-public-release.md` | 120 | 51 | 无 | 一次全面检查、界面调整、限频、姓名匹配及公开发布的验收交接 |

| `tests/test_note_deletion.py` | 190 | 112 | 无 | 备注归属、机器可见性、CSRF、审计和真实浏览器删除交互 |
| `tests/test_illustrated_guides.py` | 150 | 76 | 无 | 分角色 Markdown、本地图片、安全路径、登录退出和不读取运行资料 |
| `tests/test_machine_ledger.py` | 120 | 41 | 无 | 按机器分组、跨人员账号归属及 sudo 滑动开关 |
| `docs/records/2026-11-04-notes-and-role-guides.md` | 120 | 35 | 无 | 备注删除、机器台账、分角色图文教程及部署验收 |

| `src/compute_control/management_passwords.py` | 180 | 111 | 无 | 管理连接凭据的加密候选、远端改密及断线核查，不自动重放 |
| `src/compute_control/credential_api.py` | 100 | 39 | 无 | 网页与机器管理密码修改、再次认证和角色装配 |
| `src/compute_control/static/credentials.js` | 100 | 38 | 无 | 两种管理密码表单、确认、核查及关闭清除密码 |
| `tests/test_management_passwords.py` | 280 | 211 | 无 | 改密认证、加密、并发保护、新连接验证及持久核查 |
| `tests/test_management_password_ui.py` | 200 | 140 | 无 | 真实浏览器改密表单和固定远端命令、回执及取消墓碑 |
| `docs/records/2026-11-04-management-passwords.md` | 120 | 30 | 无 | 网页及机器管理改密能力、部署和验证交接 |

| `docs/records/2026-11-04-generic-guides-and-sticky-people.md` | 100 | 27 | 无 | 通用图文教程、可配置参数表述和长名单固定添加入口的验收交接 |

| `deploy/compute-control-https.conf` | 30 | 6 | 无 | 可选 HTTPS 的独立 systemd 启动覆盖示例 |
| `src/compute_control/static/machine_admin.js` | 110 | 40 | 无 | 单一机器管理入口、分组说明与原有表单路由 |
| `tests/test_machine_management_ui.py` | 190 | 97 | 无 | 管理弹窗、目标隔离、手机布局、确认取消和待核查入口 |
| `tests/test_last_active.py` | 180 | 101 | 无 | 账号最近登录时间、机器与人员隔离、旧库回填和持久保留 |
| `docs/records/2026-11-04-v0.2.0-release.md` | 120 | 40 | 无 | 面向公开发行的传输、界面、活跃时间与隐私验收记录 |

| `docs/records/2026-11-04-v0.2.1-guide-isolation.md` | 100 | 25 | 无 | Web 通用图文与线下资料的目录隔离、链接拒绝及公开文本检查 |

| `docs/records/2026-11-04-v0.2.2-markdown-guides.md` | 100 | 25 | 无 | 内嵌 Markdown 通用图文、演示图片、安全渲染与私密 PDF 分离 |

## 清理时如何使用

1. 依据[维护计划](MAINTENANCE.md)扫描全部文本，包括被 Git 忽略的项目私密文档；不得把秘密内容打印到公共记录。
2. 对照上限与用途，先删除重复表述或拆分混合职责；拆分必须更新链接，原内容需要删除文件时仍遵守删除审批规则。
3. 内容必须完整保留时可提高上限；在当前清理记录中说明旧值、新值、实际长度及原因。临时例外也在表内列明适用范围、到期里程碑，不默许超限。
4. 更新全部基线长度，新增文件逐一登记。已完成任务写入记录后从 Todo 删除条目，保留交接记录并更新导航。

限制按现有单体首版的文件用途和实际长度制定，没有统一的每文件行数门槛。后续源代码按模块用途逐文件登记，不预先声称所有模块应采用同一限制。
