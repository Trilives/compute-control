# 局域网算力机账号管理服务

已有可运行的本机开发版本：姓名入口、管理员、额度、机器接入、小时后台探测、24 小时历史、公告、权限申请、账号任务和归档流程。初始机器为 0 台，不预置本机。目标 Linux/NVIDIA 实机验收及正式局域网部署尚未完成。

源码：[Trilives/compute-control](https://github.com/Trilives/compute-control)；发行版：[v0.1.0](https://github.com/Trilives/compute-control/releases/tag/v0.1.0)。本版检查与边界见[发布记录](docs/records/2026-11-04-ui-review-and-public-release.md)。

## 文档入口

| 文档 | 用途 |
| --- | --- |
| [架构](ARCHITECTURE.md) | 需求、逻辑职责、依赖、数据流、接口与验收场景 |
| [文件长度限制](docs/FILE_LENGTH_LIMITS.md) | 所有文本文件的长度登记、例外与调整理由 |
| [长期维护计划](docs/MAINTENANCE.md) | 清理周期、改进方向和触发条件 |
| [开发约束与 Todo](docs/TODO.md) | 开头为简短开发规则，下方仅保留未完成任务 |
| [配置与秘密](docs/CONFIGURATION.md) | 实际配置项、默认值、生效方式及秘密供应 |
| [验证方法](docs/VERIFICATION.md) | 可执行的运行、测试、格式及文档检查命令 |
| [基线与清理记录](docs/records/2026-11-03-project-baseline.md) | 检查证据、决策依据、验证结果和待确认项 |
| [申请者指南](docs/USER_GUIDE.md) | 可向内部用户提供的操作说明 |
| [管理员操作指南](docs/ADMIN_GUIDE.md) | 管理员界面完整操作指南弹窗的权威内容，不含实际密码 |
| [开发工具说明](docs/DEVELOPMENT_ENVIRONMENT.md) | 工具分层、系统检测与管理员处理入口 |
| [管理员私密指南](docs/private/ADMIN_GUIDE.md) | 管理及部署记录，可含密码；不会进入 Git，需单独保存 |

开始工作先读架构和 Todo 开头的开发约束，再选择未完成任务。管理员私密指南在其他检出中可能不存在，应由管理员私下提供，不能补造凭据。

## 本机启动

先安装 Python 3.12+ 和 uv，在项目根目录运行：

```sh
uv sync --locked
uv run compute-control init-admin --username admin
uv run compute-control serve
```

另开终端启动后台任务：

```sh
uv run compute-control worker
```

打开 `http://127.0.0.1:8443`，默认输入申请者姓名，姓名输入窗口右上角切换管理员登录。初始化只能执行一次，忘记控制台密码可在本机运行 `uv run compute-control reset-admin`。已有开发账号时不再重复初始化，凭据见私密管理员指南。

测试、格式化、浏览器依赖及范围见[验证方法](docs/VERIFICATION.md)，配置见[配置说明](docs/CONFIGURATION.md)。本机开发不改变防火墙；正式监听、TLS 和网段规则在部署阶段落实。
