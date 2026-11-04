# 显式 HTTP 支持与测试证据

## 行为与部署

- 用户因 HTTPS 证书拦截明确要求使用 HTTP；正式主机改用 IPv4 HTTP 80，保留办公网、受控组网 和 EasyTier 的原有允许范围。
- 新配置 `allow_insecure_http` 默认 `false`，只有显式 `true` 才允许生产 HTTP；生产模式本身保持不变，默认 HTTPS 仍强制要求完整 TLS 参数。
- HTTP 不能同时传 TLS 参数，非布尔值配置及不成对 TLS 参数均拒绝启动。HTTP 配置可显式启用，systemd 用独立 [drop-in](../../deploy/compute-control-http.conf) 去除 TLS 启动参数。
- 显式生产 HTTP 使用 `cc_session_http`，不带 Secure，保留 HttpOnly、SameSite=strict、身份切换和 CSRF；旧 HTTPS 的 Secure Cookie 不影响新入口登录。默认 HTTPS 和开发 Cookie 名称保持原行为。
- 仅切换 Web；worker PID 保持不变，管理员账号、密码及数据库业务数据沿用，原待核查任务没有重放。
- UFW 按原三组接口、来源及目标地址增加 80 允许规则，成功启动后精确删除本应用三条旧 443 规则；原 SSH、VPN、Docker 等其他规则保留。
- 原配置、证书/密钥、数据库、源码及上一版 wheel 在切换前备份；实际目录只记录于私密指南。HTTPS 材料保留用于回滚。
- 安装时曾因私有 umask 导致入口脚本/包不可读，Web 短暂启动失败；已修正安装 umask、恢复可读权限，并在重启前以服务账号验证导入。worker 未受重启影响。

## TDD 与验证

需求直接来自用户本轮指令，没有外部计划文件。测试先于实现编写，使用既有 pytest、FastAPI TestClient 和 Chromium。

| 保证 | 测试 | 类型 | 实际结果 |
| --- | --- | --- | --- |
| HTTP 开关类型严格、默认生产仍要求 TLS | `tests/test_http_transport.py` | 配置 / CLI | 通过 |
| 显式 HTTP 可登录、退出且 CSRF 仍生效 | 同上 | API 集成 | 通过 |
| 旧 HTTPS Secure Cookie 不阻止 HTTP 登录，刷新后会话保留 | 同上 | Chromium | 通过 |
| 原有角色、账号、worker、归档及页面回归 | 全量 `pytest -q` | 单元 / 集成 / 浏览器 | 160 项通过 |

- 第一轮 RED：`uv run --locked pytest -q tests/test_http_transport.py`，10 失败、1 通过，原因是尚无 HTTP 配置支持；检查点 `23568d5`。
- 第一轮 GREEN：HTTP、配置和认证共 36 项通过；实现检查点 `ea40f40`。
- 第二轮 RED：HTTP Cookie 命名与 Chromium 旧 Cookie 隔离 2 项失败；检查点 `8716e69`。
- 第二轮 GREEN：传输、认证、配置、指南共 41 项通过；实现检查点 `64d9ad3`。
- 最终执行 `uv run --locked --with pytest-cov pytest -q --cov=compute_control.config --cov=compute_control.cli --cov=compute_control.app --cov-report=term-missing --cov-report=json:runtime/deployment/http/coverage.json --cov-fail-under=80`：160 通过，三个修改模块合计 85.04%（配置 94%、CLI 61%、HTTP 装配 89%）。覆盖工具仅临时使用，没有增加项目运行依赖。
- CLI 未覆盖主要为已有初始化/重设及 worker 命令分支；本次 HTTP 启动、默认 TLS 拒绝、参数冲突及真实浏览器登录均有直接测试。测试有现存 Starlette 弃用和 SQLite ResourceWarning，未因此扩大修改范围。
- Ruff 静态检查通过；代码格式、文档及差异检查随交接执行。
- 目标机 Python 3.12 下以服务账号成功导入新包，办公网与 受控组网 实测 HTTP health、管理员登录、Cookie、CSRF、既有机器及原待核查任务。
- 正式 Chromium 无证书例外完成两种角色登录、旧 Secure Cookie 保留、刷新、指南、手机布局及退出，无 JavaScript 错误。
- 临时网络命名空间再次验证 HTTP 80 的非允许来源被 UFW 阻断；目标本机检查三个入口均可访问，无 IPv6 Web 监听。
- 通过 HTTP 请求登记机器的只读刷新，原 worker 均生成新的成功采样；切换后另做一致性备份并回传开发机验证摘要。

## 适用范围

- HTTP 不提供传输加密，浏览器地址栏仍可能标注“不安全”；切换只消除 HTTPS 证书拦截页，VPN 隧道仍提供其自身加密。
- EasyTier 目标入口已启用，当前开发机与目标 peer 组不互见，跨节点完整验证仍需对应网络中的设备；本次未修改 EasyTier 组网。
- 不重启整台算力机，不执行真实账号创建/回收；既有实机生命周期及整机重启验收继续留在 Todo。
- 恢复 HTTPS 需配套恢复旧配置、去掉 HTTP drop-in、安装匹配 wheel，并将三条应用防火墙规则换回 443；不恢复旧数据库覆盖后续业务操作。

v0.2.0 增加 `https_enabled`，以明确选项提供 HTTP/HTTPS；本记录的旧配置兼容行为继续保留，当前用法见[配置文档](../CONFIGURATION.md)。
