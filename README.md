# 算力机账号管理服务

面向可信网络中的 Linux 算力机管理：申请者使用登记姓名申请账号，管理员维护人员、额度、机器、sudo 权限与回收归档。通过 SSH 操作已有机器，无需在每台机器安装常驻代理；Web 和后台 worker 独立运行。

- 机器与额度由管理员配置，账号台账按机器分组，显示账号最近一次可观测登录时间。
- 后台按配置周期采集 CPU、内存、GPU 和登录统计，提供历史折线及手动刷新。
- 支持公告、共享备注、sudo 权限申请、历史账号接管和重新分配。
- 分别管理网页密码、机器 SSH 管理账号密码和普通 Linux 账号密码；机器改密验证新连接后同步凭据。
- 回收先归档并校验 home，再删除账号；失败或连接中断保留数据与核查入口。
- HTTP / HTTPS 可选，角色指南和 PDF 下载按登录身份提供；没有自备图文文件时显示内置文字指南。

申请者的姓名入口是内部身份声明，不是强身份认证。请部署在可信局域网或受控网络；如需面向不可信来源提供访问，应增加可靠的身份与访问控制。HTTPS 负责传输加密。

当前版本：[v0.2.0](https://github.com/Trilives/compute-control/releases/tag/v0.2.0)。发行附件提供 wheel、源码包和 SHA-256 校验文件；不附带运行数据库、密码、主密钥或现场网络参数。

## 快速启动

需要 Linux、Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。从源码启动：

```sh
git clone https://github.com/Trilives/compute-control.git
cd compute-control
uv sync --locked
uv run compute-control init-admin --username admin
uv run compute-control serve
```

在另一终端启动后台任务：

```sh
uv run compute-control worker
```

使用示例配置时，浏览器访问 `http://127.0.0.1:8443`。管理员密码由初始化命令交互设置；在姓名窗口切换“管理员入口”登录，再添加人员和机器。新安装不预置机器或已有人员。实际地址、端口、采集周期和额度均可配置。

部署前，请在目标发行版上用专用测试账号验证 SSH、sudo、账号创建、登录和归档回收；不能用模拟测试替代目标环境验收。

## 可选 HTTPS

示例配置显式使用 `https_enabled = false`，直接启动 HTTP。启用 HTTPS 时设置 `https_enabled = true`，并同时提供证书及私钥路径：

```sh
uv run compute-control serve --ssl-certfile /path/to/cert.pem --ssl-keyfile /path/to/key.pem
```

HTTPS 会使用 Secure Cookie。证书与私钥应由部署者提供并受限保存；HTTP 不加密密码和会话。旧配置仍兼容，迁移说明见[配置文档](docs/CONFIGURATION.md)。

## 使用与部署

| 文档 | 内容 |
| --- | --- |
| [申请者指南](docs/USER_GUIDE.md) | 申请、额度、连接、任务、sudo、备注与回收 |
| [管理员指南](docs/ADMIN_GUIDE.md) | 人员和机器管理、三类密码、归档、故障处理 |
| [配置说明](docs/CONFIGURATION.md) | HTTP/HTTPS、监听、状态目录及可配置周期 |
| [部署与升级](docs/DEPLOYMENT.md) | 独立服务、网络访问、备份恢复及角色图文文件 |
| [开发工具说明](docs/DEVELOPMENT_ENVIRONMENT.md) | 系统、用户、项目工具的维护边界 |
| [架构](ARCHITECTURE.md) | 权限、任务、SSH 协议、数据保护及限制 |
| [验证方法](docs/VERIFICATION.md) | 测试、浏览器、静态检查和构建命令 |
| [开发任务](docs/TODO.md) | 待验证场景及后续维护 |

机器卡片的管理员操作集中在“管理机器”弹窗，重启操作保留确认；姓名表格的添加入口在滚动时保持可见。操作和任务结果以当前系统显示为准。
