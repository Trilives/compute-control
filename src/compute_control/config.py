"""Validated startup configuration; secrets live outside configuration."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    mode: str = "development"
    host: str = "127.0.0.1"
    port: int = 8443
    runtime_dir: Path = Path("runtime")
    session_seconds: int = 28800
    snapshot_seconds: int = 3600
    login_seconds: int = 3600
    ssh_timeout: int = 30
    ssh_concurrency: int = 4

    @property
    def secure_cookie(self):
        return self.mode == "production"

    @classmethod
    def load(cls, path=None):
        explicit = path is not None or bool(os.environ.get("COMPUTE_CONTROL_CONFIG"))
        path = Path(path or os.environ.get("COMPUTE_CONTROL_CONFIG", "config.toml")).resolve()
        data = tomllib.loads(path.read_text()) if path.exists() else {}
        if not path.exists() and explicit:
            raise ValueError("指定配置文件不存在")
        unknown = data.keys() - cls.__dataclass_fields__.keys()
        if unknown:
            raise ValueError(f"未知配置项: {', '.join(sorted(unknown))}")
        runtime = data.get("runtime_dir", "runtime")
        if not isinstance(runtime, str) or not runtime or "\x00" in runtime:
            raise ValueError("runtime_dir 必须为非空路径字符串")
        data["runtime_dir"] = (path.parent / runtime).resolve()
        config = cls(**data)
        if config.mode not in ("development", "production"):
            raise ValueError("mode 必须为 development 或 production")
        if not isinstance(config.host, str) or not config.host:
            raise ValueError("host 必须是非空字符串")
        if config.mode == "development" and config.host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("开发模式仅允许回环监听，局域网部署请启用 production 和 TLS")
        for key in (
            "port",
            "session_seconds",
            "snapshot_seconds",
            "login_seconds",
            "ssh_timeout",
            "ssh_concurrency",
        ):
            value = getattr(config, key)
            if type(value) is not int or value < 1:
                raise ValueError(f"{key} 必须为正整数")
        if config.port > 65535:
            raise ValueError("端口不能超过 65535")
        return config
