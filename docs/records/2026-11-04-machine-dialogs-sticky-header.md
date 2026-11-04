# 机器信息弹窗与固定顶栏

人类确定目标与验收标准，Agent 在已有页面职责内实施。用户要求系统开发工具和机器账号权限使用按钮弹窗，后续要求页面最上方一行滚动时持续可见。

- 两个入口改为机器卡片操作按钮；弹窗标题标明对应机器，工具显示状态/版本/路径，账号弹窗保留权限开关及历史账号接管入口。
- 账号权限操作提交后关闭信息弹窗并进入任务页；接管前关闭原弹窗，避免遮挡后续表单。关闭时清空弹窗内容。
- 历史 24 小时折线继续原下拉，可多机同时展开；工具和账号不再插入下拉面板。
- 顶栏使用 sticky、独立层级与主题背景，桌面及手机滚动时均保持可见；不增加轮询或后台操作。

## 改动文件

- `src/compute_control/static/app.js`、`app.css`、`src/compute_control/templates/index.html`。
- `tests/test_portal_browser.py` 更新既有流程，验证双机工具弹窗各自内容、申请者不可见管理入口、弹窗内权限开关提交及正常关闭。
- `ARCHITECTURE.md`、`docs/ADMIN_GUIDE.md`、`docs/USER_GUIDE.md` 同步入口和顶栏说明。
- 本记录、前批记录的后续链接及 `docs/FILE_LENGTH_LIMITS.md` 登记。未删除项目文件，不改变通用约束；无 Git 仓库，未创建提交。

## 验证与交接

- `uv run pytest tests/test_portal_browser.py tests/test_browser.py -q --tb=short`：2 项 Chromium 流程通过，保留 1 条已有 Starlette/httpx 弃用提示。
- 实际开发服务只读浏览器复测通过：工具 10/10 按钮弹窗含 CUDA，账号按钮弹窗含 root，页面仅历史图使用下拉；桌面 1440 与手机 390 宽度下滚动后顶栏 y=0、无水平溢出，夜间模式有效，无脚本错误。截图在忽略的 `runtime/previews/machine-dialogs-sticky-header.png`。
- 本批未变更真实账号或权限，前后端接口与后台小时采集未改变，无新增待确认项；正式实机/部署前提仍见 [Todo](../TODO.md)。
- JavaScript 语法、Python 格式/静态检查通过；文档检查 63 个文本的登记、长度、格式、链接及私密权限通过；`uv build` 成功。公开指南随包提供，私密记录和运行密钥排除。

后续紧凑卡片与全部弹窗统一布局见[调整记录](2026-11-04-compact-cards-and-modal-layout.md)。
