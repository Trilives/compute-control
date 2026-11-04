# 验证方法与实际工具

## 当前能力

本机使用 uv 0.11.13，锁定环境实际 Python 3.14.5（项目要求 3.12+）；确切依赖版本见 `uv.lock` 和 `uv tree --depth 1`。已有 API、事务、worker、SSH 协议、真实 tar 沙箱及 Chromium 测试，均不操作现有系统用户或真实算力机。

| 用途 | 实际命令 | 范围 |
| --- | --- | --- |
| 安装锁定依赖 | `uv sync --locked` | 开发依赖含 pytest、Ruff、Playwright；部署可加 `--no-dev` |
| 浏览器准备 | `uv run playwright install chromium` | 首次浏览器测试前安装，位于工具缓存 |
| 初始化管理员 | `uv run compute-control init-admin --username admin` | 本机交互密码输入，仅首次 |
| Web / worker | `uv run compute-control serve` / `uv run compute-control worker` | 分别在两个终端；默认 HTTP 回环 8443 |
| 全部测试 | `uv run pytest -q` | 含浏览器和真实 tar 沙箱，目标 SSH/GPU 已只读复测，创建/回收实机验收仍待完成 |
| 静态检查 | `uv run ruff check src tests` | Python 导入、错误及约定 |
| 格式化 / 检查 | `uv run ruff format src tests` / `uv run ruff format --check src tests` | Python；HTML/CSS/JS 当前人工排版 |
| 构建 | `uv build` | 生成发行包并核查静态文件、工具清单、两份公开指南入包；私密指南及运行密钥不得入包 |
| JavaScript 语法 | `for f in src/compute_control/static/*.js; do node --check "$f"; done` | 开发主机已有 Node；应用运行不依赖 Node |
| 文档、长度、引用 | 下方 `python3` 命令 | 只检查项目文本，排除依赖、缓存和运行数据 |
| Git 检查 | [下方命令](#git-提交检查) | 提交前检查暂存范围、差异及秘密排除 |

以下命令从项目根目录执行，不写文件，不打印私密指南内容。它验证项目所有 UTF-8 文本；未知文本编码会报错，二进制（含 NUL）跳过。私密指南在其他检出中允许缺席，但存在时必须登记且符合权限。

```sh
python3 - <<'PY'
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit

root = Path.cwd()
limits = {}
for line in (root / "docs/FILE_LENGTH_LIMITS.md").read_text().splitlines():
    match = re.match(r"\| `([^`]+)` \| (\d+) \|", line)
    if match:
        assert match[1] not in limits, f"重复登记: {match[1]}"
        limits[match[1]] = int(match[2])
assert limits, "未找到长度登记"
errors, texts = [], {}
for path in sorted(root.rglob("*")):
    rel = path.relative_to(root).as_posix()
    excluded = {'.git', '.venv', '__pycache__', '.pytest_cache', '.ruff_cache', 'runtime', 'backups', 'dist'}
    if excluded.intersection(path.relative_to(root).parts) or not path.is_file():
        continue
    raw = path.read_bytes()
    if b"\x00" in raw:
        continue
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(f"需明确编码/二进制分类: {rel}")
        continue
    texts[rel] = body
    count = len(body.splitlines())
    if rel not in limits or count > limits[rel]:
        errors.append(f"未登记或超限: {rel} ({count})")
    if (body and not body.endswith("\n")) or "\r" in body:
        errors.append(f"末尾换行或 LF 格式错误: {rel}")
    if any(line.rstrip() != line for line in body.splitlines()):
        errors.append(f"行尾空白: {rel}")
optional = {"docs/private/ADMIN_GUIDE.md"}
for rel in limits.keys() - texts.keys() - optional:
    errors.append(f"登记文件缺失: {rel}")

def prose(body):
    lines, fenced = [], False
    for line in body.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            lines.append(line)
    return "\n".join(lines), fenced

def anchors(body):
    result = set()
    for title in re.findall(r"^#{1,6} (.+)$", prose(body)[0], re.M):
        result.add(re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-"))
    return result

for rel, body in texts.items():
    if not rel.endswith(".md"):
        continue
    visible, unclosed = prose(body)
    if unclosed:
        errors.append(f"代码围栏未闭合: {rel}")
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", visible):
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        path = (root / rel).parent / unquote(url.path) if url.path else root / rel
        target_rel = path.resolve().relative_to(root).as_posix()
        if not path.exists() and target_rel not in optional:
            errors.append(f"链接目标缺失: {rel} -> {target}")
        elif url.fragment and path.is_file():
            if unquote(url.fragment) not in anchors(path.read_text()):
                errors.append(f"锚点缺失: {rel} -> {target}")
private = root / "docs/private"
guide = private / "ADMIN_GUIDE.md"
for path, mode in ((private, 0o700), (guide, 0o600)):
    if path.exists() and stat.S_IMODE(path.stat().st_mode) != mode:
        errors.append(f"私密权限应为 {mode:o}: {path.relative_to(root)}")
if "/docs/private/" not in (root / ".gitignore").read_text().splitlines():
    errors.append("私密指南未在 .gitignore 排除")
assert not errors, "\n".join(errors)
print(f"PASS: {len(texts)} 个文本文件；格式、登记、长度、相对链接及私密权限通过")
PY
```

该命令只适用于当前项目内容约定，Markdown 链接检查覆盖现有内联链接及简单标题锚点，不验证外部网址可用性或业务需求是否实现。出现新的生成目录、其他编码、复杂 Markdown 或符号链接时，先明确扫描范围及语法支持，再更新检查，不能用跳过业务文件的方式掩盖问题。

只在文档交付或[周期清理](MAINTENANCE.md)时运行；无需每次编辑检查长度。完整验收结果保存在[基线记录](records/2026-11-03-project-baseline.md)，以后各任务建立自己的记录。

## Git 提交检查

项目已按用户授权初始化 Git，提交范围遵守[开发约束](TODO.md#开发约束)：

```sh
git status --short
git diff --stat
git diff --check
git diff --cached --stat
git diff --cached
git diff --cached --check
```
