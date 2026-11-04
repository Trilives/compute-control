"""Fixed Linux operations transmitted over SSH; no resident service is installed."""

import base64
import datetime
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path


class Refuse(Exception):
    pass


def run(args, input=None, check=True):
    result = subprocess.run(
        args, input=input, text=True, capture_output=True, timeout=7200 if args[0] == "tar" else 30
    )
    if check and result.returncode:
        raise Refuse(f"远端工具执行失败: {args[0]}，请在主机核查")
    return result.stdout


def file_hash(handle):
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def local_user(name):
    for line in Path("/etc/passwd").read_text().splitlines():
        fields = line.split(":")
        if fields[0] == name:
            return fields
    return None


def user_exists(name):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def safe_directory(path, create=False):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise Refuse("目录路径不安全")
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        if not current.exists() and create:
            current.mkdir(mode=0o700)
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refuse("目录必须由 root 持有且不可被其他用户写入，不允许符号链接")
    return path


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def receipt_path(account_id):
    if not re.fullmatch("[0-9a-f]{32}", account_id):
        raise Refuse("账号标识非法")
    return safe_directory("/var/lib/compute-control/jobs", True) / (account_id + ".json")


def sssd_in_use():
    # A stock NSS entry alone does not mean a directory service is configured.
    try:
        for path in [Path("/etc/sssd/sssd.conf"), Path("/var/lib/sss/pipes/nss")]:
            if path.exists() or path.is_symlink():
                return True
        if any(Path("/etc/sssd/conf.d").glob("*.conf")):
            return True
        for process in Path("/proc").glob("[0-9]*/comm"):
            try:
                if process.read_text().strip().startswith("sssd"):
                    return True
            except FileNotFoundError:
                continue
        return False
    except OSError:
        return True  # Insufficient evidence must not turn into a successful check.


def account_sources():
    nss = Path("/etc/nsswitch.conf").read_text()
    match = re.search(r"^\s*passwd\s*:\s*([^\n#]*)", nss, re.M)
    sources = re.sub(r"\[[^]]*\]", "", match[1]).split() if match else []
    inactive = ["sss"] if "sss" in sources and not sssd_in_use() else []
    compat_local = not any(
        line.lstrip().startswith(("+", "-"))
        for line in Path("/etc/passwd").read_text().splitlines()
    )
    allowed = {"files", "systemd", *inactive}
    if compat_local:
        allowed.add("compat")
    blocking = sorted(set(sources) - allowed)
    local = bool({"files", "compat"} & set(sources)) and not blocking
    return sources, inactive, blocking, local


def capabilities():
    required = ["useradd", "usermod", "userdel", "chpasswd", "tar", "getent", "sshd"]
    missing = [name for name in required if not shutil.which(name)]
    sources, inactive, blocking, local = account_sources()
    tar_ok = bool(shutil.which("tar") and "GNU tar" in run(["tar", "--version"]))
    reasons = []
    if missing:
        reasons.append("缺少系统工具：" + ", ".join(missing))
    if not local:
        reasons.append("账号来源需要核查：" + ", ".join(blocking or sources or ["未知"]))
    if not tar_ok:
        reasons.append("未检测到支持归档校验的 GNU tar")
    return {
        "missing_tools": missing,
        "local_accounts_only": local,
        "account_sources": sources,
        "inactive_account_sources": inactive,
        "blocking_account_sources": blocking,
        "block_reasons": reasons,
        "gnu_tar": tar_ok,
        "can_create": not reasons,
        "python": sys.version.split()[0],
        "nvidia": bool(shutil.which("nvidia-smi")),
        "can_reboot": bool(shutil.which("shutdown")),
    }


def system_tool_inventory(checks):
    result = []
    for check in checks:
        item = {
            "key": check["key"],
            "name": check["label"],
            "installed": False,
            "path": None,
            "version": None,
        }
        for candidate in check["candidates"]:
            found = shutil.which(candidate)
            if not found:
                continue
            path = Path(found).resolve()
            try:
                safe_directory(path.parent)
                info = path.stat()
                if info.st_uid != 0 or info.st_mode & 0o022:
                    continue
            except (OSError, Refuse):
                continue
            item.update(installed=True, path=str(path))
            try:
                response = subprocess.run(
                    [str(path), *check["args"]], capture_output=True, text=True, timeout=3
                )
                text = (response.stdout or response.stderr).splitlines()
                preferred = next(
                    (
                        line
                        for line in text
                        if check.get("version_contains") and check["version_contains"] in line
                    ),
                    text[0] if text else "",
                )
                item["version"] = (
                    preferred[:300] if response.returncode == 0 and text else "版本不可读取"
                )
                if check.get("version_must_succeed") and response.returncode != 0:
                    item["installed"] = False
                    item["version"] = "命令存在，但驱动查询失败"
            except (OSError, subprocess.TimeoutExpired):
                item["version"] = "版本检查失败或超时"
                if check.get("version_must_succeed"):
                    item["installed"] = False
            break
        result.append(item)
    return result


def user_activity(users):
    names = {user.pw_uid: user.pw_name for user in users}
    skipped, pid = set(), os.getpid()
    while pid > 1 and pid not in skipped:
        skipped.add(pid)
        try:
            value = Path("/proc") / str(pid) / "stat"
            text = value.read_text()
            pid = int(text[text.rfind(")") + 1 :].split()[1])
        except (OSError, ValueError, IndexError):
            break
    active, unknown = [], False
    for path in Path("/proc").glob("[0-9]*"):
        try:
            uid = path.stat().st_uid
            if uid < 1000 or int(path.name) in skipped or uid == 65534:
                continue
            active.append(
                {
                    "pid": int(path.name),
                    "username": names.get(uid, str(uid)),
                    "name": (path / "comm").read_text().strip(),
                }
            )
        except FileNotFoundError:
            continue
        except OSError:
            unknown = True
    return active[:200], unknown


def inspect(payload):
    cpu = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
    cpu_times = {"total": sum(cpu), "idle": sum(cpu[3:5])}
    system_users = pwd.getpwall()
    users = [
        {
            "username": item.pw_name,
            "uid": item.pw_uid,
            "home": item.pw_dir,
            **sudo_status(item.pw_name),
        }
        for item in system_users
        if item.pw_uid == 0 or (item.pw_uid >= 1000 and item.pw_uid != 65534)
    ]
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in ["MemTotal", "MemAvailable"]:
            memory[key] = int(value.strip().split()[0]) * 1024
    gpus, processes, gpu_error = [], [], None
    if shutil.which("nvidia-smi"):
        query = run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,memory.total,memory.used,"
                "memory.free,utilization.gpu,utilization.memory,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            check=False,
        )
        for line in query.splitlines():
            parts = [value.strip() for value in line.split(",")]
            if len(parts) == 10:
                keys = [
                    "index",
                    "uuid",
                    "name",
                    "memory_total_mib",
                    "memory_used_mib",
                    "memory_free_mib",
                    "gpu_percent",
                    "memory_percent",
                    "temperature",
                    "power_w",
                ]
                gpus.append(dict(zip(keys, parts, strict=True)))
        query = run(
            [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            check=False,
        )
        for line in query.splitlines():
            parts = [value.strip() for value in line.split(",")]
            if len(parts) == 4 and parts[1].isdigit():
                try:
                    uid = Path("/proc") / parts[1]
                    username = pwd.getpwuid(uid.stat().st_uid).pw_name
                except (FileNotFoundError, KeyError):
                    username = "未知"
                processes.append(
                    dict(zip(["gpu_uuid", "pid", "name", "memory_mib"], parts, strict=True))
                    | {"username": username}
                )
        if not gpus:
            gpu_error = "无法读取 GPU；检查驱动、权限或 MIG 支持"
    else:
        gpu_error = "未安装 nvidia-smi"
    login_events = []
    if payload.get("include_logins") and shutil.which("last"):
        output = run(["last", "-w", "--time-format", "iso", "-s", "-30days"], check=False)
        for line in output.splitlines():
            fields = line.split()
            if not fields or fields[0] in ["reboot", "shutdown", "wtmp", "btmp", "runlevel"]:
                continue
            for value in fields[1:]:
                if re.match(r"^\d{4}-\d\d-\d\dT", value):
                    try:
                        when = datetime.datetime.fromisoformat(value).timestamp()
                        login_events.append({"username": fields[0], "ts": when})
                    except ValueError:
                        pass
                    break
    disk = shutil.disk_usage("/home" if Path("/home").exists() else "/")
    active_user_processes, activity_unknown = user_activity(system_users)
    tools = (
        {
            "system_tools": system_tool_inventory(payload.get("tool_checks", [])),
            "tools_sampled": time.time(),
        }
        if payload.get("include_tools")
        else {}
    )
    final_cpu = [
        int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]
    ]
    delta_total = sum(final_cpu) - cpu_times["total"]
    delta_idle = sum(final_cpu[3:5]) - cpu_times["idle"]
    cpu_percent = (
        round(100 * (delta_total - delta_idle) / delta_total, 1)
        if delta_total > 0 and 0 <= delta_idle <= delta_total
        else None
    )
    return {
        "cpu_percent": cpu_percent,
        **tools,
        "identity": Path("/etc/machine-id").read_text().strip(),
        "users": users,
        "active_user_processes": active_user_processes,
        "activity_unknown": activity_unknown,
        "existing_usernames": [item.pw_name for item in system_users],
        "capabilities": capabilities(),
        "gpus": gpus,
        "processes": processes,
        "gpu_error": gpu_error,
        "memory": memory,
        "cpu_times": cpu_times,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "load": os.getloadavg(),
        "disk": {"total": disk.total, "free": disk.free},
        "login_events": login_events,
        "login_status": "记录可能不完整" if Path("/var/log/wtmp").exists() else "无法统计",
        "sampled": time.time(),
    }


def create(payload):
    if not capabilities()["can_create"]:
        raise Refuse("主机不满足本地账号创建条件")
    name = payload["username"]
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name) or name in ["root", "admin", "nobody"]:
        raise Refuse("用户名不允许")
    if name == payload["admin_username"]:
        raise Refuse("不能修改机器管理账号")
    password = payload.get("password")
    path = receipt_path(payload["account_id"])
    evidence = json.loads(path.read_text()) if path.exists() else None
    account = user_exists(name)
    if evidence and evidence.get("name") == name and account:
        if evidence.get("uid") != account.pw_uid or evidence.get("home") != account.pw_dir:
            raise Refuse("账号创建证据与主机状态不符，需管理员核查")
        if evidence.get("state") in ["active", "quiesced", "verified", "retired"]:
            return {key: evidence[key] for key in ["uid", "home", "state"]}
        if evidence.get("state") == "setting_password" and password:
            run(["chpasswd"], f"{name}:{password}\n")
            evidence["state"] = "active"
            atomic_json(path, evidence)
            return {"uid": account.pw_uid, "home": account.pw_dir, "state": "active"}
        raise Refuse("创建中断，需管理员核查原任务证据")
    if account or Path("/home", name).exists() or evidence:
        raise Refuse("用户名、主目录或任务证据已存在，禁止覆盖")
    if not password or not 3 <= len(password) <= 256 or any(x in password for x in "\n\r\x00:"):
        raise Refuse("密码不符合规则")
    safe_directory("/home")
    burned = {int(item.pw_uid) for item in pwd.getpwall()}
    for item in path.parent.glob("*.json"):
        old = json.loads(item.read_text())
        if "uid" in old:
            burned.add(old["uid"])
    uid = next((candidate for candidate in range(1000, 60000) if candidate not in burned), None)
    if uid is None:
        raise Refuse("无可用 UID")
    home = "/home/" + name
    evidence = {"name": name, "uid": uid, "home": home, "state": "creating"}
    atomic_json(path, evidence)
    run(
        [
            "useradd",
            "--uid",
            str(uid),
            "--user-group",
            "--create-home",
            "--home-dir",
            home,
            "--shell",
            "/bin/bash",
            "--",
            name,
        ]
    )
    evidence["state"] = "setting_password"
    atomic_json(path, evidence)
    run(["chpasswd"], f"{name}:{password}\n")
    evidence["state"] = "active"
    atomic_json(path, evidence)
    return {"uid": uid, "home": home, "state": "active"}


def active_processes(uid, home):
    busy = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid == uid:
                busy.append(int(process.name))
                continue
            # Conservatively block external processes with any open home file or cwd.
            links = [process / "cwd", *list((process / "fd").iterdir())]
            if any(
                os.readlink(link).startswith(home + "/") or os.readlink(link) == home
                for link in links
                if link.is_symlink()
            ):
                busy.append(int(process.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return busy


def manifest(home):
    entries = {}

    def visit(path):
        info = path.lstat()
        relative = str(path.relative_to(home))
        entry = {
            "mode": info.st_mode,
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mtime_ns": info.st_mtime_ns,
            "xattrs": {
                key: base64.b64encode(os.getxattr(path, key, follow_symlinks=False)).decode()
                for key in os.listxattr(path, follow_symlinks=False)
            },
        }
        if stat.S_ISREG(info.st_mode):
            with path.open("rb") as handle:
                entry["sha256"] = file_hash(handle)
            entry["size"] = info.st_size
            entry["inode"] = info.st_ino
            entry["links"] = info.st_nlink
        elif stat.S_ISLNK(info.st_mode):
            entry["link"] = os.readlink(path)
        elif not stat.S_ISDIR(info.st_mode):
            raise Refuse("主目录存在特殊对象，需管理员处理")
        entries[relative] = entry
        if stat.S_ISDIR(info.st_mode):
            for child in sorted(path.iterdir()):
                visit(child)

    visit(home)
    return entries


def retire(payload):
    path = receipt_path(payload["account_id"])
    if not path.exists():
        raise Refuse("无本系统创建证据，禁止回收")
    proof = json.loads(path.read_text())
    name, uid, home = proof["name"], proof["uid"], Path(proof["home"])
    if name != payload["username"] or uid < 1000 or name == payload["admin_username"]:
        raise Refuse("受保护账号或身份不匹配")
    account = user_exists(name)
    if proof["state"] == "retired" and not account:
        return {key: proof[key] for key in ["state", "archive", "checksum", "size"]}
    if not account or account.pw_uid != uid or account.pw_dir != str(home):
        raise Refuse("账号/UID/home 与创建证据不符")
    if not local_user(name) or home != Path("/home") / name or home.is_symlink():
        raise Refuse("不是受支持的独立主目录")
    safe_directory(home.parent)
    if not home.is_dir() or home.stat().st_uid != uid:
        raise Refuse("主目录不存在或所有权异常")
    if any(
        item.pw_name != name and (item.pw_uid == uid or item.pw_dir == str(home))
        for item in pwd.getpwall()
    ):
        raise Refuse("检测到共享 UID 或主目录，禁止自动回收")
    mounts = Path("/proc/mounts").read_text().splitlines()
    for line in mounts:
        parts = line.split()
        target = parts[1].replace("\\040", " ")
        if target == str(home) or target.startswith(str(home) + "/"):
            raise Refuse("主目录内存在挂载点，需管理员处理")
        if str(home).startswith(target.rstrip("/") + "/") and parts[2] in [
            "nfs",
            "nfs4",
            "cifs",
            "ceph",
            "lustre",
        ]:
            raise Refuse("共享文件系统不允许自动回收")
    busy = active_processes(uid, str(home))
    if busy:
        raise Refuse("账号或主目录仍被进程使用，请先结束工作")
    for directory in ["/var/spool/cron", "/var/spool/cron/crontabs", "/var/lib/systemd/linger"]:
        if Path(directory, name).exists():
            raise Refuse("存在 cron 或 lingering，需管理员先停用并保存恢复信息")
    if shutil.which("atq") and run(["atq"]).strip():
        raise Refuse("存在 at 任务，需管理员核查后处理")
    sshd = run(["sshd", "-T", "-C", f"user={name},host=localhost,addr=127.0.0.1"])
    if "usepam yes" not in sshd.splitlines():
        raise Refuse("SSH 未启用 PAM，不能确认账号过期可阻止密钥登录")
    if proof["state"] == "active":
        shadow = next(
            line.split(":")
            for line in Path("/etc/shadow").read_text().splitlines()
            if line.split(":", 1)[0] == name
        )
        proof["restore"] = {"password_hash": shadow[1], "expire": shadow[7]}
        # Persist intent before disabling; an interrupted operation is never auto-unlocked.
        proof["state"] = "quiescing"
        atomic_json(path, proof)
    run(["usermod", "--expiredate", "1", "--lock", "--", name])
    proof["state"] = "quiesced"
    atomic_json(path, proof)
    if active_processes(uid, str(home)):
        raise Refuse("停用后仍有进程，暂停回收")
    archive_root = safe_directory(payload["archive_root"], True)
    if (
        archive_root == home
        or archive_root.is_relative_to(home)
        or home.is_relative_to(archive_root)
    ):
        raise Refuse("归档目录与 home 重叠")
    target = safe_directory(archive_root / payload["account_id"] / payload["job_id"], True)
    before = manifest(home)
    needed = sum(entry.get("size", 0) for entry in before.values())
    if shutil.disk_usage(target).free < needed + max(needed // 10, 64 * 1024 * 1024):
        raise Refuse("归档空间不足")
    temporary = target / "home.partial.tar"
    if temporary.exists():
        raise Refuse("已有未完成归档，需管理员核查，不能覆盖")
    run(
        [
            "tar",
            "--acls",
            "--xattrs",
            "--sparse",
            "--numeric-owner",
            "--format=pax",
            "-cpf",
            str(temporary),
            "-C",
            str(home),
            ".",
        ]
    )
    run(["tar", "--acls", "--xattrs", "--compare", "-f", str(temporary), "-C", str(home)])
    if manifest(home) != before or active_processes(uid, str(home)):
        raise Refuse("归档期间主目录发生变化，保留原账号及数据")
    with tarfile.open(temporary) as archive:
        archived = set()
        for member in archive:
            relative = member.name.removeprefix("./") or "."
            archived.add(relative)
            entry = before.get(relative)
            if entry is None:
                raise Refuse("归档条目不符")
            if member.isfile():
                content = archive.extractfile(member)
                if content is None or file_hash(content) != entry["sha256"]:
                    raise Refuse("归档内容校验失败")
        if archived != set(before):
            raise Refuse("归档缺少条目")
    with temporary.open("rb") as handle:
        checksum = file_hash(handle)
    final = target / "home.tar"
    os.replace(temporary, final)
    atomic_json(target / "manifest.json", before)
    atomic_json(target / "retirement-metadata.json", proof)
    (target / "checksum.sha256").write_text(checksum + "  home.tar\n")
    proof.update(state="verified", archive=str(final), checksum=checksum, size=final.stat().st_size)
    atomic_json(path, proof)
    if user_exists(name).pw_uid != uid or home.is_symlink() or active_processes(uid, str(home)):
        raise Refuse("删除前状态变化，暂停")
    if Path("/etc/sudoers.d").is_dir():
        grant_file = privilege_path(payload["account_id"])
        if grant_file.exists():
            grant_file.unlink()
    run(["userdel", "--", name])
    if user_exists(name):
        raise Refuse("删除后账号仍存在，保留额度待核查")
    proof["state"] = "retired"
    atomic_json(path, proof)
    # Original data is deliberately retained for admin cleanup after remote deletion is confirmed.
    return {key: proof[key] for key in ["state", "archive", "checksum", "size"]}


def known_account(payload):
    path = receipt_path(payload["account_id"])
    if not path.exists():
        raise Refuse("无本系统创建证据")
    proof = json.loads(path.read_text())
    if proof["name"] != payload["username"] or proof["name"] == payload["admin_username"]:
        raise Refuse("账号身份不匹配或为管理账号")
    return path, proof


def reconcile(payload):
    raw_path = receipt_path(payload["account_id"])
    if not raw_path.exists():
        return {"state": "not_created"}
    path, proof = known_account(payload)
    account = user_exists(proof["name"])
    if proof["state"] == "creating" and not account and not Path(proof["home"]).exists():
        return {"state": "not_created"}
    if account and account.pw_uid == proof["uid"] and account.pw_dir == proof["home"]:
        if proof["state"] == "active":
            return {
                "state": "active",
                "uid": proof["uid"],
                "home": proof["home"],
                "last_password_job": proof.get("last_password_job"),
                "last_privilege_job": proof.get("last_privilege_job"),
                **sudo_status(proof["name"]),
            }
        raise Refuse("账号存在但未处于可用状态，请检查原任务或恢复登录")
    if not account and proof["state"] in ["retired", "cleaned"]:
        return {key: proof[key] for key in ["state", "archive", "checksum", "size"]}
    raise Refuse("远端状态不明，不释放额度或自动重试")


def verify_archive(proof):
    archive = Path(proof["archive"])
    safe_directory(archive.parent)
    if archive.is_symlink() or not archive.is_file():
        raise Refuse("归档不存在或路径异常")
    with archive.open("rb") as handle:
        if file_hash(handle) != proof["checksum"]:
            raise Refuse("归档校验摘要不符")
    return archive


def cleanup(payload):
    path, proof = known_account(payload)
    if user_exists(proof["name"]) or proof["state"] not in ["retired", "cleaned"]:
        raise Refuse("账号尚未确认删除，禁止清理 home")
    home = Path(proof["home"])
    if proof["state"] == "cleaned" and not home.exists():
        return {"state": "home_cleaned"}
    archive = verify_archive(proof)
    safe_directory(home.parent)
    if any(item.pw_uid == proof["uid"] or item.pw_dir == str(home) for item in pwd.getpwall()):
        raise Refuse("UID 或 home 被其他账号使用，禁止清理")
    if (
        home != Path("/home") / proof["name"]
        or home.is_symlink()
        or home.stat().st_uid != proof["uid"]
    ):
        raise Refuse("home 路径或所有权变化，暂停清理")
    for line in Path("/proc/mounts").read_text().splitlines():
        target = line.split()[1].replace("\\040", " ")
        if target == str(home) or target.startswith(str(home) + "/"):
            raise Refuse("home 内存在挂载点，暂停清理")
    saved = json.loads((archive.parent / "manifest.json").read_text())
    if manifest(home) != saved or active_processes(proof["uid"], str(home)):
        raise Refuse("home 内容变化或仍被使用，禁止清理")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise Refuse("平台不支持安全目录清理")
    shutil.rmtree(home)
    proof["state"] = "cleaned"
    atomic_json(path, proof)
    return {"state": "home_cleaned"}


def restore(payload):
    path, proof = known_account(payload)
    account = user_exists(proof["name"])
    if (
        not account
        or account.pw_uid != proof["uid"]
        or proof["state"] not in ["quiescing", "quiesced", "verified"]
    ):
        raise Refuse("当前状态不能恢复登录，需管理员核查")
    original = proof["restore"]
    # Hash is supplied on stdin, never in argv or operation results.
    run(["chpasswd", "--encrypted"], proof["name"] + ":" + original["password_hash"] + "\n")
    expire = original["expire"]
    expiry = (
        str(datetime.date(1970, 1, 1) + datetime.timedelta(days=int(expire)))
        if expire and int(expire) >= 0
        else ""
    )
    run(["usermod", "--expiredate", expiry, "--", proof["name"]])
    proof["state"] = "active"
    atomic_json(path, proof)
    return {
        "state": "active",
        "uid": proof["uid"],
        "home": proof["home"],
        "last_password_job": proof.get("last_password_job"),
        "last_privilege_job": proof.get("last_privilege_job"),
        **sudo_status(proof["name"]),
    }


def delete_archive(payload):
    path, proof = known_account(payload)
    if proof["state"] != "cleaned" or user_exists(proof["name"]):
        raise Refuse("尚有账号或 home 清理依赖，禁止删除归档")
    if payload["archive_path"] != proof["archive"] or payload["checksum"] != proof["checksum"]:
        raise Refuse("归档记录不匹配")
    archive = Path(proof["archive"])
    if proof.get("archive_deleted") and not archive.parent.exists():
        return {"state": "archive_deleted"}
    safe_directory(archive.parent)
    allowed = {"home.tar", "manifest.json", "retirement-metadata.json", "checksum.sha256"}
    present = {entry.name for entry in archive.parent.iterdir()}
    if not present.issubset(allowed) or (
        not proof.get("archive_delete_intent") and present != allowed
    ):
        raise Refuse("归档目录存在未识别内容，禁止删除")
    for entry in archive.parent.iterdir():
        if entry.is_symlink() or not entry.is_file():
            raise Refuse("归档目录条目异常")
    if not proof.get("archive_delete_intent"):
        verify_archive(proof)
        proof["archive_delete_intent"] = time.time()
        atomic_json(path, proof)
    for name in allowed:
        (archive.parent / name).unlink(missing_ok=True)
    archive.parent.rmdir()
    proof["archive_deleted"] = time.time()
    atomic_json(path, proof)
    return {"state": "archive_deleted"}


def adopt(payload):
    name = payload["username"]
    user = user_exists(name)
    if (
        not user
        or not local_user(name)
        or user.pw_uid < 1000
        or name in ["root", "admin", "nobody", payload["admin_username"]]
    ):
        raise Refuse("禁止接管系统、管理或非本地账号")
    local = local_user(name)
    if int(local[2]) != user.pw_uid or local[5] != user.pw_dir:
        raise Refuse("本地用户与 NSS 返回身份不一致")
    if user.pw_uid != payload["uid"] or user.pw_dir != payload["home"]:
        raise Refuse("接管对象与观察结果不符，请刷新核查")
    if Path(user.pw_dir) != Path("/home") / name or Path(user.pw_dir).is_symlink():
        raise Refuse("接管仅支持独立 /home/用户名 目录")
    path = receipt_path(payload["account_id"])
    proof = {
        "name": name,
        "uid": user.pw_uid,
        "home": user.pw_dir,
        "state": "active",
        "origin": "imported",
    }
    if path.exists() and json.loads(path.read_text()) != proof:
        raise Refuse("已有不同的管理证据，需核查")
    atomic_json(path, proof)
    return {key: proof[key] for key in ["state", "uid", "home"]}


def set_password(payload):
    path, proof = known_account(payload)
    user = user_exists(proof["name"])
    if not user or user.pw_uid != proof["uid"] or user.pw_dir != proof["home"]:
        raise Refuse("账号与接管/创建证据不符，禁止改密")
    local = local_user(proof["name"])
    if not local or int(local[2]) != user.pw_uid or local[5] != user.pw_dir:
        raise Refuse("本地用户身份不符，禁止改密")
    password = payload.get("password", "")
    if not 3 <= len(password) <= 256 or any(value in password for value in "\n\r\x00:"):
        raise Refuse("密码至少 3 字符，不能含换行、NUL 或冒号")
    run(["chpasswd"], proof["name"] + ":" + password + "\n")
    proof["last_password_job"] = payload["job_id"]
    atomic_json(path, proof)
    return {
        "state": "active",
        "uid": proof["uid"],
        "home": proof["home"],
        "last_password_job": payload["job_id"],
    }


def management_password_status(payload):
    change_id = payload["change_id"]
    if not re.fullmatch("[0-9a-f]{32}", change_id):
        raise Refuse("密码变更标识非法")
    path = safe_directory("/var/lib/compute-control/jobs", True) / (
        "management-" + change_id + ".json"
    )
    if not path.exists() and payload.get("cancel_if_not_started"):
        # Runs under machine.lock: tombstone an unsent request before any late packet executes it.
        atomic_json(path, {"state": "cancelled"})
    return json.loads(path.read_text()) if path.exists() else {"state": "unknown"}


def management_password(payload):
    name, password = payload["admin_username"], payload.get("password", "")
    if not 3 <= len(password) <= 256 or any(char in password for char in "\n\r\x00:"):
        raise Refuse("密码至少 3 字符，不能含换行、NUL 或冒号")
    local, user = local_user(name), user_exists(name)
    if (
        not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", name)
        or not local
        or not user
        or int(local[2]) != user.pw_uid
        or local[5] != user.pw_dir
        or (user.pw_uid != 0 and user.pw_uid < 1000)
        or user.pw_uid == 65534
    ):
        raise Refuse("连接管理账号不是已核验的本地账号，禁止改密")
    previous = management_password_status(payload)
    if previous["state"] in ["changed", "failed"]:
        return previous
    if previous["state"] != "unknown":
        raise Refuse("原改密结果尚未核实，禁止重复执行")
    path = safe_directory("/var/lib/compute-control/jobs", True) / (
        "management-" + payload["change_id"] + ".json"
    )
    atomic_json(path, {"state": "started"})
    try:
        run(["chpasswd"], name + ":" + password + "\n")
    except Refuse:
        atomic_json(path, {"state": "failed"})
        raise
    result = {"state": "changed"}
    atomic_json(path, result)
    return result


def sudo_status(name):
    if name == "root":
        return {"is_admin": True, "admin_status": "root 账号"}
    try:
        result = subprocess.run(
            ["sudo", "-n", "-l", "-U", name],
            capture_output=True,
            text=True,
            timeout=5,
            env=dict(os.environ, LC_ALL="C"),
        )
        output = result.stdout + result.stderr
        rules = re.findall(r"^\s+\(([^)]+)\)\s+(.+)$", result.stdout, re.M)
        if result.returncode == 0 and rules:
            privileged = any(
                any(
                    part.strip() and not part.strip().startswith("!")
                    for part in commands.split(",")
                )
                for _, commands in rules
            )
            return {
                "is_admin": privileged,
                "admin_status": "有 sudo 授权" if privileged else "无 sudo 授权",
            }
        if "not allowed to run sudo" in output or "may not run sudo" in output:
            return {"is_admin": False, "admin_status": "无 sudo 授权"}
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"is_admin": None, "admin_status": "sudo 权限未能确认"}


def privilege_path(account_id):
    if not re.fullmatch("[0-9a-f]{32}", account_id):
        raise Refuse("账号标识非法")
    return safe_directory("/etc/sudoers.d") / ("compute-control-" + account_id)


def set_privilege(payload):
    path, proof = known_account(payload)
    user, local = user_exists(proof["name"]), local_user(proof["name"])
    if (
        not user
        or not local
        or user.pw_uid < 1000
        or proof["name"] in ["root", "admin", "nobody", payload["admin_username"]]
        or user.pw_uid != proof["uid"]
        or user.pw_dir != proof["home"]
        or int(local[2]) != user.pw_uid
        or local[5] != user.pw_dir
        or proof["state"] != "active"
    ):
        raise Refuse("账号身份不符或为受保护账号，禁止变更权限")
    if type(payload.get("enabled")) is not bool or not re.fullmatch(
        "[a-zA-Z_][a-zA-Z0-9_-]{0,63}", proof["name"]
    ):
        raise Refuse("权限目标或用户名非法")
    target = privilege_path(payload["account_id"])
    # Record intent before changes; reconciliation checks the actual resulting policy.
    proof["last_privilege_job"] = payload["job_id"]
    atomic_json(path, proof)
    if payload["enabled"]:
        if not shutil.which("visudo"):
            raise Refuse("缺少 visudo，不能验证授权规则")
        temporary = target.with_suffix(".tmp")
        content = proof["name"] + " ALL=(ALL:ALL) ALL\n"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o440)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            run(["visudo", "-c", "-f", str(temporary)])
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
    else:
        if target.exists():
            target.unlink()
        for group in ["sudo", "wheel"]:
            try:
                record = grp.getgrnam(group)
            except KeyError:
                continue
            if user.pw_gid == record.gr_gid:
                raise Refuse("管理组是主组，需管理员先调整主组后关闭权限")
            if proof["name"] in record.gr_mem:
                run(["gpasswd", "-d", proof["name"], group])
    actual = sudo_status(proof["name"])
    if actual["is_admin"] is None or actual["is_admin"] != payload["enabled"]:
        raise Refuse("已执行权限调整但当前授权未达目标，请核查其他 sudoers 规则；不会误报成功")
    return {
        "state": "active",
        "uid": proof["uid"],
        "home": proof["home"],
        "last_privilege_job": payload["job_id"],
        **actual,
    }


def reboot(payload):
    job_id = payload["job_id"]
    if not re.fullmatch("[0-9a-f]{32}", job_id):
        raise Refuse("重启任务标识非法")
    directory = safe_directory("/var/lib/compute-control/jobs", True)
    receipt = directory / ("reboot-" + job_id + ".json")
    if receipt.exists():
        return json.loads(receipt.read_text())
    command = shutil.which("shutdown")
    if not command:
        raise Refuse("系统不支持 shutdown，请管理员处理")
    result = {
        "state": "reboot_scheduled",
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }
    # Record intent first: a disconnect never causes an automatic second reboot.
    atomic_json(receipt, dict(result, uncertain=True))
    run([command, "-r", "now"])
    atomic_json(receipt, result)
    return result


def main():
    os.umask(0o077)
    print("CC_READY", flush=True)
    payload = json.loads(sys.stdin.readline())
    try:
        action = payload["action"]
        operations = {
            "inspect": inspect,
            "create": create,
            "retire": retire,
            "reconcile": reconcile,
            "cleanup": cleanup,
            "restore": restore,
            "delete_archive": delete_archive,
            "adopt": adopt,
            "set_password": set_password,
            "management_password": management_password,
            "management_password_status": management_password_status,
            "set_privilege": set_privilege,
            "reboot": reboot,
        }
        if action not in operations:
            raise Refuse("未知操作")
        expected = payload.get("expected_identity")
        if expected and Path("/etc/machine-id").read_text().strip() != expected:
            raise Refuse("机器身份发生变化，停止操作")
        if action == "inspect":
            result = inspect(payload)
        else:
            import fcntl

            directory = (
                safe_directory("/var/lib/compute-control/jobs", True)
                if action in ["reboot", "management_password", "management_password_status"]
                else receipt_path(payload["account_id"]).parent
            )
            path = directory / (
                ("reboot-" + payload["job_id"] + ".lock")
                if action == "reboot" and payload.get("force")
                else "machine.lock"
            )
            with path.open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Refuse("原远端操作仍在执行，禁止重复操作") from exc
                result = operations[action](payload)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False), flush=True)
    except Exception as exc:
        # Do not serialize raw system errors, input, command output or password hashes.
        message = str(exc) if isinstance(exc, Refuse) else "远端操作异常，需管理员核查任务阶段"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
