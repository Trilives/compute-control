# 部署、升级与恢复

采用单控制主机、独立 Web/worker 的部署方式，通过 SSH 管理算力机。以下路径为可调整的安装示例；实际机器、网络、凭据及目录由部署者配置，不从教程复制现场资料。

## 准备环境

- 控制端需要 Linux、Python 3.12+、uv 和持久可写的状态目录；使用独立非 root 服务账号。
- 目标机需要可核验身份的 SSH、可用的 sudo、本地用户管理和归档工具。添加机器会给出能力报告；不满足条件时先修复环境。
- 在目标发行版用专用测试账号完成创建、SSH 登录、回收、归档校验及恢复验收；添加机器不会自动接管已有账号。
- 程序和虚拟环境由安装者管理、服务账号只读；数据库和主密钥由服务账号持有，运行目录 700、敏感文件 600。数据库不能放公共静态目录。

## 安装与配置

1. 从 Release 下载 wheel 或源码包，核对 `SHA256SUMS`；源码安装可执行 `uv sync --locked --no-dev`。wheel 需安装其声明的运行依赖，不默认附带依赖包。
2. 复制并调整 `config.toml`。局域网监听使用 `mode = "production"`，按部署需要选择地址、端口和状态目录；不要把实际密码写入 TOML。
3. 新安装用 `compute-control --config <配置路径> init-admin` 交互设置网页管理员密码，再分别启动 `serve` 和 `worker`。已有数据库不重复初始化。
4. 页面添加人员和机器，核对当前有效额度及身份；机器的 SSH/sudo 密码由管理员表单加密录入，不需要项目专用 `.env`。

## HTTP 与可选 HTTPS

- HTTP：设置 `https_enabled = false`，运行 `compute-control --config <配置路径> serve`。HTTP 本身不加密密码和会话。
- HTTPS：设置 `https_enabled = true`，运行 `compute-control --config <配置路径> serve --ssl-certfile <证书路径> --ssl-keyfile <私钥路径>`；证书必须覆盖实际访问的名称或地址，私钥仅让所需服务身份读取。
- 同时提供证书和私钥；不完整或相互冲突的配置会拒绝启动。HTTPS 使用 Secure Cookie，HTTP 保留 HttpOnly、SameSite 和 CSRF，但没有 Secure 标志。
- 旧的 `allow_insecure_http` 配置仍兼容，建议迁移到明确的 `https_enabled`。不因升级自动切换已有安装的传输方式。

## systemd 示例

[Web unit](../deploy/compute-control-web.service) 和 [worker unit](../deploy/compute-control-worker.service) 展示独立账号与权限隔离。使用前调整安装路径、账号、配置路径及监听端口；示例 Web unit 运行 HTTP，配置应明确 `https_enabled = false`。

启用 HTTPS 时，将 [HTTPS drop-in](../deploy/compute-control-https.conf) 安装到 Web unit 的 drop-in 目录并调整证书路径，同时设置 `https_enabled = true`。旧安装可参考 [HTTP drop-in](../deploy/compute-control-http.conf) 切回 HTTP。

修改后执行 `systemd-analyze verify`、`systemctl daemon-reload`，分别启动 Web/worker 并按需要设为开机启动。只有绑定低端口时才需要相应能力；正常运行不授予本机 sudo。worker 保持单实例，不对网络开放数据库或 Unix socket。

## 网络与访问

仅允许实际需要的来源到所选 Web 端口，保留原有 SSH 和其他服务。使用已有防火墙管理方式，不清空规则；分别检查 IPv4、IPv6 和上游访问策略。

如使用组网软件，由部署者提供自己的网络参数和当前可达地址。控制台入口与各算力机 SSH 地址可能不同；不预设组网产品、虚拟网段、机器数量或固定端口。

申请者姓名入口适用于可信环境，不是密码或强身份认证。面向不可信来源时需增加可靠访问控制，不能仅依靠 HTTPS 替代身份校验。

## 角色图文指南

内置文字指南随包提供。可在 `<runtime_dir>/web-tutorials/` 安装自备的 `admin.html`、`admin.pdf`、`applicant.html`、`applicant.pdf`；目录及文件仅允许服务身份读取。

文件按实际会话角色返回，申请者不能用查询参数读取管理员版，匿名请求被拒绝。HTML 应自包含图片与样式；页面以禁止脚本的 iframe 展示。缺少对应 HTML/PDF 时回退文字指南。Web UI 通用图文只放操作说明；现场部署和接入资料应另行线下分发，不放在 `web-tutorials`。旧 `tutorials` 和 `internal-tutorials` 不会被 Web UI 自动读取。

## 升级、备份与回滚

1. 核对未完成任务与目标机器状态，避免在变更执行时替换 worker；不启动第二个 worker。
2. 用 SQLite backup API 创建一致性数据库快照，同时受限保存匹配的主密钥、配置、主机身份记录和版本信息；在隔离目录验证完整性和凭据可解密。
3. 安装经过校验的新包，保留原运行状态；只在需要时重启对应服务，验证健康检查、登录、CSRF、任务核查和目标 SSH。
4. 需要回滚时先保留现场与新状态，检查数据库版本。已执行的远端操作不会因恢复旧数据库而撤销，先核对证据再决定恢复或继续。

主密钥与数据库必须匹配；单独保管异地备份。远端用户 home 和归档仍在所属机器，控制台升级不自动搬迁这些数据。各类密码变更和失败核查见[管理员指南](ADMIN_GUIDE.md)。
