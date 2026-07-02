from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from .db import get_config


@dataclass(frozen=True)
class RuntimeConfig:
    mailbridge_mcp_url: str = ""
    mailbridge_mcp_token: str = ""
    mailbridge_sync_before_run: bool = True
    account_aliases: dict[str, str] | None = None


class RuntimeConfigPoller:
    def __init__(self) -> None:
        self._config = RuntimeConfig()
        self._task: asyncio.Task[None] | None = None
        self.running = False
        self.last_poll_at = ""

    def load_once(self) -> RuntimeConfig:
        try:
            aliases = json.loads(get_config("account_aliases", "{}") or "{}")
        except json.JSONDecodeError:
            aliases = {}
        self._config = RuntimeConfig(
            mailbridge_mcp_url=get_config("mailbridge_mcp_url", ""),
            mailbridge_mcp_token=get_config("mailbridge_mcp_token", ""),
            mailbridge_sync_before_run=get_config("mailbridge_sync_before_run", "true") == "true",
            account_aliases={str(key): str(value) for key, value in aliases.items()},
        )
        return self._config

    def snapshot(self) -> RuntimeConfig:
        if not self._config.mailbridge_mcp_url:
            return self.load_once()
        return self._config

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.load_once()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self.running:
            self.load_once()
            await asyncio.sleep(10)


runtime_config = RuntimeConfigPoller()
