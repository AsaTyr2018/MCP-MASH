from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    public_url: str
    mcp_token: str
    timezone: str
    scheduler_interval_seconds: int
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mash.db"

    @property
    def scripts_dir(self) -> Path:
        return self.data_dir / "scripts"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


public_url = os.getenv("MASH_PUBLIC_URL", "http://127.0.0.1:18083")

settings = Settings(
    data_dir=Path(os.getenv("MASH_DATA_DIR", "/data")),
    public_url=public_url,
    mcp_token=os.getenv("MASH_MCP_TOKEN", ""),
    timezone=os.getenv("MASH_TIMEZONE", "Europe/Berlin"),
    scheduler_interval_seconds=max(5, _int_env("MASH_SCHEDULER_INTERVAL_SECONDS", 30)),
    allowed_hosts=_csv_env("MASH_ALLOWED_HOSTS", "127.0.0.1,127.0.0.1:8080,127.0.0.1:18083,localhost,localhost:8080,localhost:18083"),
    allowed_origins=_csv_env("MASH_ALLOWED_ORIGINS", f"http://127.0.0.1:8080,{public_url}"),
)
