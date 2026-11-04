# 显式 HTTP 兼容与测试证据

- 显式 HTTP 保留 HttpOnly、SameSite、CSRF 及角色校验，HTTPS 则增加 Secure Cookie。传输方式不替代身份验证。
- 旧 `allow_insecure_http` 兼容开关和独立 HTTP Cookie 避免旧 HTTPS 的 Secure Cookie 阻止 HTTP 登录。
- 初始 HTTP 支持的 RED/GREEN 检查点及后续传输选择测试覆盖 CLI、参数完整性、冲突、登录、退出与旧 Cookie 隔离。
- 普通 HTTP 的账号申请使用兼容非安全上下文的随机标识生成方式，避免依赖仅安全上下文可用的 API。
- 当前版本通过 `https_enabled` 明确选择 HTTP/HTTPS，省略时保留旧安装的兼容规则，详见[配置说明](../CONFIGURATION.md)。
- 部署者按自身监听、端口和访问来源配置防火墙；任何真实环境的连接或认证资料均由其私下保管。
