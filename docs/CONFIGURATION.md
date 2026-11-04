# 配置与秘密供应

应用启动时读取根目录 `config.toml`。可用 `compute-control --config /absolute/path.toml serve` 指定，或设置 `COMPUTE_CONTROL_CONFIG`；优先级为命令行路径、该环境变量、当前目录 `config.toml`。默认文件缺失时使用内置默认值，明确指定却缺失、未知字段或非法值会拒绝启动。

| 配置项 | 允许值 | 默认值 | 用途与影响 |
| --- | --- | --- | --- |
| `mode` | `development` / `production` | `development` | 开发只允许回环监听；生产允许按部署配置监听网络，不决定显式选择的传输方式 |
| `https_enabled` | 布尔值 | 示例为 `false`；旧配置省略时保留旧规则 | `false` 选择 HTTP；`true` 启用 HTTPS，并要求同时提供证书和私钥，Cookie 使用 Secure；两种模式都保留 HttpOnly、SameSite 和 CSRF |
| `allow_insecure_http` | 布尔值，旧配置兼容 | `false` | 未设置 `https_enabled` 时沿用旧行为：生产默认 TLS，此项为 `true` 时允许 HTTP；不能与 `https_enabled=true` 或 TLS 证书一起使用 |
| `host` | 非空监听地址；开发限 `127.0.0.1`、`::1`、`localhost` | `127.0.0.1` | Web 监听网卡；生产可用 `0.0.0.0`，防火墙需另行按网段配置 |
| `port` | 整数 1–65535 | `8443` | Web TCP 端口，不自动调整防火墙 |
| `runtime_dir` | 路径字符串 | `runtime` | 相对所选配置文件目录解析，保存数据库、密钥、worker 锁和 Unix socket；目录权限 700 |
| `session_seconds` | 正整数，秒 | `28800` | 会话绝对有效期，不随轮询无限延期 |
| `snapshot_seconds` | 正整数，秒 | `3600` | 不依赖网页的后台探测间隔；失败后记录尝试时间并按此退避，手动刷新可越过等待 |
| `login_seconds` | 正整数，秒 | `3600` | 登录历史补读间隔；依附快照采样，因此不比快照采样更快 |
| `ssh_timeout` | 正整数，秒 | `30` | SSH 建连、认证和 sudo 握手超时；普通远端结果等待为其 4 倍，归档/清理/删除最长 7500 秒 |
| `ssh_concurrency` | 正整数 | `4` | worker 常规执行并发；同机普通变更串行，明确第三次确认的强制重启有独立执行槽 |

修改文件后重启 Web 和 worker 生效。默认及个人账号额度、管理员联系方式文本通过管理员界面持久化在 SQLite，不放进启动配置：全局默认值可在界面修改，个人留空则继承。首次没有任何机器。

## 实际读取验证

`tests/test_remote_config.py` 验证替代配置文件和相对路径，且通过 `COMPUTE_CONTROL_CONFIG` 创建真实应用，检查程序使用的端口、数据库目录及生产 Cookie 标志。启动行为见[验证命令](VERIFICATION.md)。

## 秘密

- `init-admin` / `reset-admin` 从交互终端隐藏读取管理员密码，存储 Argon2id 哈希；不支持通过命令参数传入明文。
- 机器 SSH/sudo 密码通过已登录管理员表单录入，使用 Fernet 认证加密后存入数据库。主密钥自动首次生成于 `<runtime_dir>/master.key`，权限 600，不能只备份数据库而丢弃密钥。
- 普通账号密码按离职交接需求认证加密留存；创建经权限 600 的 Unix socket 交给 worker，内存丢失仍在原任务重新输入。重设候选仅以密文进入任务，核对生效后写台账；列表/任务 API 不返回密文，日志不记密码，查看权限见[架构](../ARCHITECTURE.md#5-机器添加与凭据)。
- TLS 通过 `serve --ssl-certfile /path/cert.pem --ssl-keyfile /private/path/key.pem` 指定，只传路径。私钥置于受限文件中，不提交仓库。

## 选择 HTTP 或 HTTPS

示例配置设置 `https_enabled = false`，可直接使用 HTTP。选择 HTTPS 时将其设为 `true`，并为 `serve` 同时提供 `--ssl-certfile`、`--ssl-keyfile`；证书由部署者提供，应用不自动签发证书或修改防火墙。HTTP 不加密密码及会话。

旧配置未设置 `https_enabled` 时保持兼容：开发回环 HTTP、生产要求 TLS，或通过原 `allow_insecure_http=true` 明确使用 HTTP。迁移时增加 `https_enabled`，启用 HTTPS 前将旧 HTTP 选项改回 `false` 或删除。仅传入 TLS 证书的旧开发启动也会正确使用 Secure Cookie。

HTTP 和 HTTPS 会话 Cookie 分开处理，避免旧 Secure Cookie 影响切换后的登录。配置变更后按实际部署方式重启相应服务；反向代理 TLS 终止及可信代理头不是本版的自动配置功能。

- 现有 `.gitignore` 排除标准 `runtime/`、私密指南等路径；自定义运行目录应放在仓库外，若位于仓库内须先补忽略规则。管理员私密指南可人工保管密码，程序不会读取它。

页面仅在进入、操作或手动更新显示时读取缓存，没有周期心跳/刷新；浏览器关闭不影响按配置周期采集。最近 24 小时真实指标存于 SQLite，机器卡片下拉展开时读取。旧 `lease_seconds` 已移除，旧配置应删掉此项；未知项仍拒绝启动。

数据库现版本为 4；从版本 1/2/3 首次升级时生成 `runtime/backups/before-v4-from-v<旧版本>-*.sqlite3` 一致性快照（权限 600），再保留原数据并增加表/列。需回退先停服务、恢复配套备份，不直接以旧程序打开新库。公告、联系名单、共享备注、台账显示标记及权限申请通过界面存入数据库，不新增未使用的启动配置。明暗主题仍为浏览器偏好。
